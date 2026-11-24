import asyncio
import importlib.util
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from config.settings import settings
from infrastructure.network.client import SecureNetworkClient
from scraping.exceptions import ErrorCategory, ScrapingError
from scraping.interfaces.interfaces import ParseResult
from scraping.security import DomainRateLimiter, RobotsCacheService
from shared.models import ScrapingTask
from shared.models.parse_config.static_css import FollowLinkRule

from .base import BaseParser
from .extractor import UniversalDOMExtractor

_DEFAULT_BS_PARSER = "lxml" if importlib.util.find_spec("lxml") else "html.parser"


class StaticCSSParser(BaseParser):
    """Parser asíncrono universal para HTML estático con soporte opcional de subconsultas (follow_links)."""

    _parse_semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}

    def __init__(
        self,
        network_client: SecureNetworkClient,
        extractor: Optional[UniversalDOMExtractor] = None,
        rate_limiter: Optional[DomainRateLimiter] = None,
        robots_service: Optional[RobotsCacheService] = None,
        max_concurrent_dom_parses: Optional[int] = None,
        bs_parser: str = _DEFAULT_BS_PARSER,
    ):
        super().__init__()
        self.network_client = network_client
        self.extractor = extractor or UniversalDOMExtractor()
        self.rate_limiter = rate_limiter
        self.robots_service = robots_service
        self._max_concurrent_dom_parses = max(
            1,
            max_concurrent_dom_parses
            if max_concurrent_dom_parses is not None
            else settings.worker_max_concurrent_dom_parses,
        )
        self._bs_parser = bs_parser

    @classmethod
    def _get_parse_semaphore(cls, max_parses: int) -> asyncio.Semaphore:
        """Retorna el semáforo de parseo DOM compartido para el event loop activo."""
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        key = (loop_id, max_parses)
        sem = cls._parse_semaphores.get(key)
        if sem is None:
            cls._parse_semaphores = {
                k: v for k, v in cls._parse_semaphores.items() if k[0] == loop_id
            }
            sem = asyncio.Semaphore(max_parses)
            cls._parse_semaphores[key] = sem
        return sem

    async def _fetch_response(self, task: ScrapingTask, target_url: str) -> Any:
        """Ejecuta una petición HTTP GET usando el pool de sesiones y clasifica errores."""
        session = await self.network_client.get_session(
            target_url=target_url,
            sticky_session_id=task.context.get("sticky_session_id"),
            use_proxy=task.use_proxy,
            custom_headers=task.headers,
        )
        self.log.info(
            f"Infiltración TLS Keep-Alive activa. Descargando: {target_url} | Tarea ID: {task.task_id}"
        )

        response = await session.get(target_url, allow_redirects=True)

        if response.status_code == 404:
            raise ScrapingError(
                ErrorCategory.NOT_FOUND, "Página no existe", task.task_id
            )
        if response.status_code in [403, 429]:
            raise ScrapingError(
                ErrorCategory.BLOCKED, "IP bloqueada o Rate Limit", task.task_id
            )
        if response.status_code >= 500:
            raise ScrapingError(
                ErrorCategory.SERVER_ERROR,
                f"Fallo del servidor destino: {response.status_code}",
                task.task_id,
            )

        response.raise_for_status()
        return response

    async def _acquire_domain_clearance(self, task: ScrapingTask, target_url: str) -> bool:
        """Valida robots.txt y adquiere permiso del rate limiter para sub-peticiones."""
        if self.robots_service is not None:
            user_agent = (task.headers or {}).get("User-Agent")
            allowed, robots_status = await self.robots_service.is_allowed(
                url=target_url,
                user_agent=user_agent,
                respect_robots_txt=task.respect_robots_txt,
            )
            if not allowed:
                self.log.warning(
                    f"Subconsulta descartada por robots.txt (Status: {robots_status}) | URL: {target_url}"
                )
                return False

        if self.rate_limiter is not None:
            await self.rate_limiter.acquire(
                url_or_domain=target_url,
                rate_limit_per_second=task.rate_limit_per_second,
            )

        return True

    @staticmethod
    def _is_extracted_empty(extracted: Any) -> bool:
        """Comprueba si el resultado extraído por los selectores está vacío."""
        if not extracted:
            return True
        if isinstance(extracted, dict):
            return all(not v for v in extracted.values())
        return False

    @staticmethod
    def _non_html_content_type(response: Any) -> Optional[str]:
        """Devuelve el Content-Type si la respuesta no corresponde a contenido HTML/texto."""
        headers = getattr(response, "headers", None)
        if not headers or not hasattr(headers, "get"):
            return None

        raw_ct = headers.get("content-type") or headers.get("Content-Type")
        if not isinstance(raw_ct, str) or not raw_ct.strip():
            return None

        ct = raw_ct.split(";")[0].strip().lower()
        allowed_markers = ("html", "xml", "text/plain", "text/")
        if any(marker in ct for marker in allowed_markers):
            return None
        return ct

    @staticmethod
    def _decompose_excluded(
        soup: BeautifulSoup,
        exclude_selectors: Optional[list[str]],
    ) -> None:
        """Elimina nodos excluidos del árbol DOM antes de extraer selectores (incluye tags XML tipo 'ix:header')."""
        if not exclude_selectors:
            return

        for selector in exclude_selectors:
            try:
                matched_tags = soup.select(selector)
            except Exception:
                # Etiquetas con namespace XML como 'ix:header' generan SelectorSyntaxError sin escape CSS
                matched_tags = soup.find_all(selector)

            for tag in matched_tags:
                tag.decompose()

    @staticmethod
    def _match_row_candidate(
        row: Tag,
        rule: FollowLinkRule,
        filter_pattern: Optional[re.Pattern[str]],
    ) -> Optional[tuple[Tag, Optional[str], Optional[dict[str, Optional[str]]]]]:
        """Evalúa una fila contenedora contra el filtro y devuelve enlace, etiqueta y metadatos."""
        matched_label: Optional[str] = None
        if rule.filter_selector:
            filter_el = row.select_one(rule.filter_selector)
            if filter_el is None:
                return None
            matched_label = filter_el.get_text(strip=True)
            if filter_pattern and not filter_pattern.search(matched_label):
                return None

        link_el = row.select_one(rule.link_selector)
        if link_el is None:
            return None

        if not rule.filter_selector and filter_pattern:
            matched_label = link_el.get_text(strip=True)
            if not filter_pattern.search(matched_label):
                return None

        metadata: Optional[dict[str, Optional[str]]] = None
        if rule.metadata_selectors:
            metadata = {
                key: (el.get_text(strip=True) if (el := row.select_one(sel)) else None)
                for key, sel in rule.metadata_selectors.items()
            }

        return link_el, matched_label, metadata

    @staticmethod
    def _match_link_candidate(
        link_el: Tag,
        filter_pattern: Optional[re.Pattern[str]],
    ) -> Optional[tuple[Tag, Optional[str], Optional[dict[str, Optional[str]]]]]:
        """Evalúa un elemento enlace directo contra el filtro regex opcional."""
        text = link_el.get_text(strip=True)
        if filter_pattern and not filter_pattern.search(text):
            return None
        return link_el, (text or None), None

    def _extract_follow_candidates(
        self,
        soup: BeautifulSoup,
        base_url: str,
        rule: FollowLinkRule,
    ) -> list[tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]]:
        """Descubre, normaliza y deduplica las URLs candidatas de una regla follow_links."""
        filter_pattern = re.compile(rule.filter_regex) if rule.filter_regex else None
        strip_pattern = re.compile(rule.url_strip_regex) if rule.url_strip_regex else None

        if rule.row_selector:
            raw_matches = (
                self._match_row_candidate(row, rule, filter_pattern)
                for row in soup.select(rule.row_selector)
            )
        else:
            raw_matches = (
                self._match_link_candidate(link_el, filter_pattern)
                for link_el in soup.select(rule.link_selector)
            )

        candidates: list[
            tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]
        ] = []
        seen_urls: set[str] = set()

        for match in raw_matches:
            if match is None:
                continue
            link_el, matched_label, metadata = match
            raw_href = link_el.get(rule.link_attribute)
            if not raw_href:
                continue

            cleaned_href = str(raw_href).strip()
            if strip_pattern:
                cleaned_href = strip_pattern.sub("", cleaned_href).strip()
            if not cleaned_href or cleaned_href.startswith("#"):
                continue

            resolved_url = urljoin(base_url, cleaned_href)
            if urlparse(resolved_url).scheme not in ("http", "https"):
                continue
            if resolved_url in seen_urls:
                continue

            seen_urls.add(resolved_url)
            candidates.append((resolved_url, matched_label, metadata))
            if len(candidates) >= rule.max_links:
                break

        return candidates

    @staticmethod
    def _build_sublink_entry(
        target_url: str,
        matched_label: Optional[str],
        metadata: Optional[dict[str, Optional[str]]],
        *,
        data: Any = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Construye la estructura uniforme de salida para un sub-enlace procesado."""
        entry: dict[str, Any] = {
            "url": target_url,
            "label": matched_label,
        }
        if metadata is not None:
            entry["metadata"] = metadata
        if error is not None:
            entry["error"] = error
        else:
            entry["data"] = data
        return entry

    async def _parse_sublink_soup(
        self,
        raw_html: str,
        rule: FollowLinkRule,
    ) -> Any:
        """Parsea y extrae un sub-documento bajo el semáforo DOM, liberando memoria al instante."""
        async with self._get_parse_semaphore(self._max_concurrent_dom_parses):
            sub_soup = BeautifulSoup(raw_html, self._bs_parser)
            del raw_html
            self._decompose_excluded(sub_soup, rule.exclude_selectors)
            sub_data = self.extractor.extract_from_soup(
                soup=sub_soup,
                selectors=rule.selectors,
                container=rule.container,
            )
            del sub_soup
            return sub_data

    async def _fetch_and_extract_sublink(
        self,
        task: ScrapingTask,
        target_url: str,
        matched_label: Optional[str],
        metadata: Optional[dict[str, Optional[str]]],
        rule: FollowLinkRule,
    ) -> dict[str, Any]:
        """Descarga y extrae un único sub-enlace, aislando fallos permanentes de red temporal."""
        if not await self._acquire_domain_clearance(task, target_url):
            return self._build_sublink_entry(
                target_url, matched_label, metadata, error="DISALLOWED_ROBOTS_TXT"
            )

        try:
            response = await self._fetch_response(task, target_url)
        except ScrapingError as se:
            if se.category == ErrorCategory.NOT_FOUND:
                return self._build_sublink_entry(
                    target_url, matched_label, metadata, error=str(se)
                )
            raise

        non_html_ct = self._non_html_content_type(response)
        if non_html_ct:
            return self._build_sublink_entry(
                target_url,
                matched_label,
                metadata,
                error=f"Contenido no HTML: {non_html_ct}",
            )

        raw_html = response.text
        del response
        sub_data = await self._parse_sublink_soup(raw_html, rule)
        del raw_html
        if self._is_extracted_empty(sub_data):
            return self._build_sublink_entry(
                target_url,
                matched_label,
                metadata,
                error="Selectores no extrajeron datos",
            )

        return self._build_sublink_entry(
            target_url, matched_label, metadata, data=sub_data
        )

    async def _fetch_index_candidates(
        self,
        task: ScrapingTask,
        index_urls: list[str],
        rule: FollowLinkRule,
    ) -> Optional[list[tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]]]:
        """Descarga la primera URL índice disponible probando fallbacks ante 404 y extrae sus candidatos."""
        for candidate_url in index_urls:
            if not await self._acquire_domain_clearance(task, candidate_url):
                continue
            try:
                source_resp = await self._fetch_response(task, candidate_url)
            except ScrapingError as se:
                if se.category == ErrorCategory.NOT_FOUND:
                    self.log.info(
                        f"Índice {candidate_url} devolvió 404 en tarea {task.task_id}; probando siguiente alternativa."
                    )
                    continue
                raise

            raw_index_html = source_resp.text
            del source_resp
            async with self._get_parse_semaphore(self._max_concurrent_dom_parses):
                base_soup = BeautifulSoup(raw_index_html, self._bs_parser)
                del raw_index_html
                candidates = self._extract_follow_candidates(
                    base_soup, candidate_url, rule
                )
                del base_soup
                return candidates

        self.log.warning(
            f"Ninguna URL índice respondió en tarea {task.task_id} ({index_urls}). Se conserva extracción principal."
        )
        return None

    async def _resolve_follow_rule(
        self,
        task: ScrapingTask,
        rule: FollowLinkRule,
        direct_candidates: Optional[
            list[tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]]
        ] = None,
    ) -> list[dict[str, Any]]:
        """Resuelve los candidatos (desde source_url con fallbacks o desde HTML principal) y extrae cada sub-enlace."""
        if rule.source_url is not None:
            index_urls = [
                str(rule.source_url),
                *(str(u) for u in (rule.fallback_source_urls or [])),
            ]
            candidates = await self._fetch_index_candidates(task, index_urls, rule)
            if candidates is None:
                return []
        else:
            candidates = direct_candidates or []

        results: list[dict[str, Any]] = []
        for target_url, matched_label, metadata in candidates:
            item = await self._fetch_and_extract_sublink(
                task, target_url, matched_label, metadata, rule
            )
            results.append(item)
        return results

    async def _parse_main_document(
        self,
        task: ScrapingTask,
        raw_html: str,
        follow_rules: list[FollowLinkRule],
    ) -> tuple[Any, dict[str, list[tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]]]]:
        """Parsea el HTML principal bajo el semáforo DOM, extrae campos y precalcula candidatos sin source_url."""
        selectors = task.parser_config.get("selectors", {})
        container = task.parser_config.get("container")
        exclude_selectors = task.parser_config.get("exclude_selectors")

        async with self._get_parse_semaphore(self._max_concurrent_dom_parses):
            soup = BeautifulSoup(raw_html, self._bs_parser)
            del raw_html
            self._decompose_excluded(soup, exclude_selectors)

            extracted = self.extractor.extract_from_soup(
                soup=soup,
                selectors=selectors,
                container=container,
            )

            if self._is_extracted_empty(extracted):
                del soup
                raise ScrapingError(
                    ErrorCategory.INVALID_SCHEMA,
                    "Selectores no extrajeron datos (posible cambio de DOM)",
                    task.task_id,
                )

            direct_candidates: dict[
                str, list[tuple[str, Optional[str], Optional[dict[str, Optional[str]]]]]
            ] = {}
            if follow_rules and isinstance(extracted, dict):
                base_url = str(task.url)
                for rule in follow_rules:
                    if rule.source_url is None:
                        direct_candidates[rule.name] = self._extract_follow_candidates(
                            soup, base_url, rule
                        )

            del soup
            return extracted, direct_candidates

    async def parse(self, task: ScrapingTask) -> ParseResult:
        """Parser asíncrono universal para HTML estático libre de bloqueos TLS 403."""
        raw_follow_rules = task.parser_config.get("follow_links") or []

        try:
            follow_rules = [FollowLinkRule.model_validate(r) for r in raw_follow_rules]
            response = await self._fetch_response(task, str(task.url))
            raw_html = response.text
            del response

            extracted, direct_candidates = await self._parse_main_document(
                task, raw_html, follow_rules
            )
            del raw_html

            if follow_rules and isinstance(extracted, dict):
                for rule in follow_rules:
                    extracted[rule.name] = await self._resolve_follow_rule(
                        task, rule, direct_candidates.get(rule.name)
                    )

        except ScrapingError:
            raise
        except Exception as e:
            self.log.error(f"Error al procesar la tarea {task.task_id}: {str(e)}")
            raise ScrapingError(
                ErrorCategory.SERVER_ERROR,
                "Error inesperado durante el scraping",
                task.task_id,
                original_error=str(e),
            )

        return self._prepare_result(task, extracted)
