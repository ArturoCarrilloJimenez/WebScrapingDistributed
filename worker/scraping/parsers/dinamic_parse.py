import asyncio
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from playwright_stealth import Stealth

from scraping.parsers.base import BaseParser
from shared.models import ScrapingTask
from scraping.interfaces.interfaces import ParseResult
from infrastructure.network.client import SecureNetworkClient
from scraping.exceptions import ErrorCategory, ScrapingError
from shared.models.parse_config.dynamic_playwright import Config as PlaywrightConfig
from .extractor import UniversalDOMExtractor


DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


class DynamicParser(BaseParser):
    """Parser asíncrono para renderizado de JS dinámico mediante Playwright."""

    _playwright = None
    _browser = None
    _lock = asyncio.Lock()

    def __init__(
        self,
        network_client: SecureNetworkClient,
        extractor: Optional[UniversalDOMExtractor] = None
    ):
        super().__init__()
        self.network_client = network_client
        self.extractor = extractor or UniversalDOMExtractor()

    @classmethod
    async def get_browser(cls):
        """Inicializa Chromium de forma perezosa y persistente (Singleton de Navegador)."""
        async with cls._lock:
            if cls._browser is None:
                cls._playwright = await async_playwright().start()
                cls._browser = await cls._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
            return cls._browser

    @classmethod
    async def close_browser(cls):
        """Libera los recursos del navegador al apagar el worker."""
        async with cls._lock:
            if cls._browser:
                try:
                    await cls._browser.close()
                except Exception:
                    pass
                cls._browser = None
            if cls._playwright:
                try:
                    await cls._playwright.stop()
                except Exception:
                    pass
                cls._playwright = None

    def _get_proxy(self, task: ScrapingTask) -> Optional[Dict[str, str]]:
        """Obtiene y formatea la configuración de proxy si está habilitada."""
        if not (self.network_client and self.network_client.proxy_provider):
            return None

        proxy_url = self.network_client.proxy_provider.get_proxy_url(
            task.context.get("sticky_session_id")
        )
        if not proxy_url:
            return None

        parsed = urlparse(proxy_url)
        playwright_proxy = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        }
        if parsed.username:
            playwright_proxy["username"] = parsed.username
        if parsed.password:
            playwright_proxy["password"] = parsed.password

        return playwright_proxy

    async def _init_browser_context(self, task: ScrapingTask, config: PlaywrightConfig):
        """Crea un BrowserContext aislado configurado con Client Hints y timeouts."""
        browser = await self.get_browser()

        browser_version = browser.version
        major_version = browser_version.split(".")[0] if browser_version else "130"
        dynamic_user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{browser_version} Safari/537.36"

        try:
            context = await browser.new_context(
                proxy=self._get_proxy(task),
                user_agent=dynamic_user_agent,
                viewport={"width": 1280, "height": 720},
                extra_http_headers={
                    "Sec-Ch-Ua": f'"Chromium";v="{major_version}", "Google Chrome";v="{major_version}", "Not?A_Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
                }
            )

            context.set_default_navigation_timeout(config.timeout_ms)
            context.set_default_timeout(config.timeout_ms)

            return context
        except Exception as e:
            self.log.error(f"Error inesperado en tarea dinámica {task.task_id}: {str(e)}")
            raise ScrapingError(
                ErrorCategory.SERVER_ERROR,
                "Error inesperado durante la navegación dinámica",
                task.task_id,
                original_error=str(e)
            )

    async def _navigate_page(self, page: Any, task: ScrapingTask, config: PlaywrightConfig) -> None:
        """Navega a la URL especificada aplicando stealth y validando el código HTTP."""
        await Stealth().apply_stealth_async(page)
        self.log.info(
            f"Navegación dinámica Playwright iniciándose para: {task.url} | Tarea ID: {task.task_id}"
        )

        response = await page.goto(str(task.url), wait_until=config.wait_until)
        if response and response.status in [403, 429]:
            raise ScrapingError(
                ErrorCategory.BLOCKED,
                f"IP bloqueada o Rate Limit detectado por el servidor remoto (Status: {response.status})",
                task.task_id
            )

    async def _execute_interactions(self, page: Any, task: ScrapingTask, config: PlaywrightConfig) -> None:
        """Ejecuta interacciones opcionales como clicks o scrolls en la página."""
        if config.click_selectors:
            for selector in config.click_selectors:
                self.log.info(
                    f"Interacción: Haciendo click en {selector} | Tarea ID: {task.task_id}"
                )
                await page.click(selector)
                await asyncio.sleep(0.5)

        if config.scroll_to_bottom:
            self.log.info(
                f"Interacción: Realizando scroll al final | Tarea ID: {task.task_id}"
            )
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)

    async def _wait_for_conditions(self, page: Any, task: ScrapingTask, config: PlaywrightConfig) -> None:
        """Maneja esperas explícitas de selectores, retardo fijo y elementos contenedores."""
        if config.wait_for_selector:
            self.log.info(
                f"Espera: Esperando el selector {config.wait_for_selector} | Tarea ID: {task.task_id}"
            )
            await page.wait_for_selector(config.wait_for_selector, timeout=config.timeout_ms)

        if config.fixed_sleep_s and config.fixed_sleep_s > 0:
            self.log.info(
                f"Espera: Retardo fijo de {config.fixed_sleep_s}s | Tarea ID: {task.task_id}"
            )
            await asyncio.sleep(config.fixed_sleep_s)

        if config.container and not config.wait_for_selector:
            try:
                await page.wait_for_selector(config.container, timeout=config.timeout_ms)
            except Exception:
                pass

    async def _extract_and_validate_data(
        self, page: Any, task: ScrapingTask, config: PlaywrightConfig
    ) -> Any:
        """Extrae la información del HTML renderizado utilizando BeautifulSoup y valida el resultado."""
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")

        extracted = self.extractor.extract_from_soup(
            soup=soup,
            selectors=config.selectors,
            container=config.container
        )

        if not extracted:
            raise ScrapingError(
                ErrorCategory.INVALID_SCHEMA,
                "Selectores no extrajeron datos (posible cambio de DOM o carga fallida)",
                task.task_id
            )
        elif isinstance(extracted, dict):
            if all(not v for v in extracted.values()):
                raise ScrapingError(
                    ErrorCategory.INVALID_SCHEMA,
                    "Selectores no extrajeron datos (posible cambio de DOM o carga fallida)",
                    task.task_id
                )

        return extracted

    async def parse(self, task: ScrapingTask) -> ParseResult:
        """Parser asíncrono para renderizado de JS dinámico mediante Playwright."""
        config = PlaywrightConfig(**(task.parser_config or {}))
        context = None

        try:
            context = await self._init_browser_context(task, config)
            page = await context.new_page()

            await self._navigate_page(page, task, config)
            await self._execute_interactions(page, task, config)
            await self._wait_for_conditions(page, task, config)
            extracted = await self._extract_and_validate_data(page, task, config)

        except ScrapingError:
            raise
        except PlaywrightTimeoutError as te:
            self.log.error(
                f"Timeout en tarea dinámica {task.task_id}: {str(te)}"
            )
            raise ScrapingError(
                ErrorCategory.TIMEOUT,
                "Excedido el límite de tiempo cargando la página dinámica",
                task.task_id,
                original_error=str(te)
            )
        except PlaywrightError as pe:
            self.log.error(
                f"Error de Playwright en tarea {task.task_id}: {str(pe)}"
            )
            cat = ErrorCategory.BLOCKED if "block" in str(pe).lower() or "deny" in str(pe).lower() else ErrorCategory.SERVER_ERROR
            raise ScrapingError(
                cat,
                f"Error en el motor de navegación dinámica: {str(pe)}",
                task.task_id,
                original_error=str(pe)
            )
        except Exception as e:
            self.log.error(
                f"Error inesperado en tarea dinámica {task.task_id}: {str(e)}"
            )
            raise ScrapingError(
                ErrorCategory.SERVER_ERROR,
                "Error inesperado durante la navegación dinámica",
                task.task_id,
                original_error=str(e)
            )
        finally:
            if context:
                await context.close()

        return self._prepare_result(task, extracted)
