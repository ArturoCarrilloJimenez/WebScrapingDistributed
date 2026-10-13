from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from shared.logging import Logger
from shared.models import RobotsStatus
from infrastructure.cache import BaseCacheAdapter, MemoryCacheAdapter
from infrastructure.network.client import SecureNetworkClient

log = Logger("Robots Cache Service")


class RobotsCacheService:
    """
    Servicio de cumplimiento legal de robots.txt con caché de 24 horas.
    Descarga, parsea y valida URLs contra las directivas del portal objetivo,
    utilizando un adaptador de caché y el cliente de red centralizado.
    """

    CACHE_TTL_SECONDS = 86400  # 24 horas

    def __init__(
        self,
        cache_adapter: BaseCacheAdapter | None = None,
        network_client: SecureNetworkClient | None = None,
    ):
        self._cache = cache_adapter or MemoryCacheAdapter()
        self._network_client = network_client or SecureNetworkClient()

    def extract_domain_and_scheme(self, url: str) -> tuple[str, str]:
        parsed = urlparse(url)
        scheme = parsed.scheme or "https"
        host = parsed.netloc or parsed.path.split("/")[0]
        if ":" in host:
            host = host.split(":")[0]
        return scheme, host

    async def is_allowed(
        self,
        url: str,
        user_agent: str | None = None,
        respect_robots_txt: bool | None = True,
    ) -> tuple[bool, RobotsStatus]:
        """
        Determina si una URL puede ser scrapeada según la normativa del portal.
        Retorna (allowed: bool, status: RobotsStatus).
        """
        if respect_robots_txt is False:
            return True, RobotsStatus.BYPASS

        scheme, domain = self.extract_domain_and_scheme(url)
        ua = user_agent or "*"
        # Extraer token principal del User-Agent (ej: 'AcmeBot/1.0' -> 'AcmeBot')
        ua_token = ua.split("/")[0].split(" ")[0]

        parser, status = await self._get_or_fetch_parser(scheme, domain)
        if parser is None:
            return True, status

        allowed = parser.can_fetch(ua_token, url)
        if allowed:
            return True, RobotsStatus.ALLOWED

        log.warning(
            f"Acceso denegado por robots.txt de '{domain}' para la ruta '{url}' con User-Agent '{ua_token}'"
        )
        return False, RobotsStatus.DISALLOWED

    async def _get_or_fetch_parser(
        self, scheme: str, domain: str
    ) -> tuple[RobotFileParser | None, RobotsStatus]:
        cache_key = f"robots_txt:{domain}"

        # 1. Comprobar caché
        cached_content = await self._cache.get(cache_key)
        if cached_content is not None:
            if cached_content == "__404_NOT_FOUND__":
                return None, RobotsStatus.NOT_FOUND
            parser = self._parse_robots_content(cached_content)
            return parser, RobotsStatus.ALLOWED

        # 2. Descargar robots.txt del servidor remoto a través de SecureNetworkClient
        robots_url = f"{scheme}://{domain}/robots.txt"
        try:
            session = await self._network_client.get_session(robots_url, use_proxy=False)
            resp = await session.get(robots_url)

            if resp.status_code == 404:
                log.info(
                    f"No existe robots.txt en {domain} (404 Not Found), acceso permitido.")
                await self._cache.set(cache_key, "__404_NOT_FOUND__", ttl_seconds=self.CACHE_TTL_SECONDS)
                return None, RobotsStatus.NOT_FOUND
            elif resp.status_code != 200:
                log.warning(
                    f"Error {resp.status_code} obteniendo robots.txt de {domain}, permitiendo por defecto.")
                return None, RobotsStatus.ALLOWED

            content = resp.text
            parser = self._parse_robots_content(content)
            await self._cache.set(cache_key, content, ttl_seconds=self.CACHE_TTL_SECONDS)
            return parser, RobotsStatus.ALLOWED

        except Exception as e:
            log.warning(
                f"No se pudo descargar robots.txt de {robots_url}: {e}. Permitiendo acceso por resiliencia.")
            return None, RobotsStatus.ALLOWED

    def _parse_robots_content(self, content: str) -> RobotFileParser:
        parser = RobotFileParser()
        parser.parse(content.splitlines())
        return parser

    async def close(self) -> None:
        """Cierra el adaptador de caché."""
        await self._cache.close()
