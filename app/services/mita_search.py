"""秘塔（Metaso）搜索 API 封装（支持域名白名单/黑名单）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings
from app.services.api_keys import validate_mita_key
from app.services.http_retry import with_retries

logger = logging.getLogger(__name__)


@dataclass
class MitaSearchResultItem:
    title: str
    url: str
    snippet: str
    published_at: Optional[str] = None
    source_domain: Optional[str] = None


@dataclass
class MitaSearchResponse:
    query: str
    items: list[MitaSearchResultItem] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class MitaSearchClient:
    """
    秘塔 AI 搜索客户端。
    官方端点: POST https://metaso.cn/api/v1/search
    文档: https://metaso.cn/search-api/playground
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.mita_api_base_url).rstrip("/")
        self.api_key = api_key or settings.mita_api_key
        self.timeout = timeout or settings.request_timeout_seconds
        self.retry_attempts = int(getattr(settings, "network_retry_attempts", 3) or 3)
        self.retry_backoff = float(getattr(settings, "network_retry_backoff_seconds", 1.5) or 1.5)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _search_url(self) -> str:
        """构造普通网页 Search API 地址。"""
        base = self.base_url.rstrip("/")
        if base.endswith("/search"):
            return base
        return f"{base}/search"

    def _build_query(
        self,
        query: str,
        whitelist_domains: Optional[list[str]] = None,
    ) -> str:
        """秘塔 API 无原生域名参数时，通过 site: 语法收窄检索范围。"""
        wl = [d.strip() for d in (whitelist_domains or []) if d and d.strip()]
        if not wl:
            return query
        if len(wl) <= 5:
            site_part = " OR ".join(f"site:{d}" for d in wl)
            return f"{query} ({site_part})"
        return query

    def search(
        self,
        query: str,
        whitelist_domains: Optional[list[str]] = None,
        blacklist_domains: Optional[list[str]] = None,
        max_results: int = 10,
        extra_params: Optional[dict[str, Any]] = None,
    ) -> MitaSearchResponse:
        validate_mita_key(self.api_key)

        effective_query = self._build_query(query, whitelist_domains)
        payload: dict[str, Any] = {
            "q": effective_query,
            "scope": "webpage",
            "size": max(1, min(int(max_results), 50)),
            "includeSummary": True,
            "includeRawContent": False,
            "conciseSnippet": False,
        }
        if extra_params:
            payload.update(extra_params)

        url = self._search_url()
        logger.info("秘塔搜索: q=%s", effective_query[:200])

        def _do_search() -> dict[str, Any] | list[Any]:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, headers=self._headers(), json=payload)
                    response.raise_for_status()
                    try:
                        return response.json()
                    except ValueError as exc:
                        snippet = (response.text or "")[:500]
                        logger.error("秘塔 API 返回非 JSON: %s", snippet)
                        raise RuntimeError(f"秘塔搜索响应解析失败: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                body = (exc.response.text or "")[:300]
                # 5xx / 429 可重试
                if exc.response.status_code in (408, 425, 429, 500, 502, 503, 504):
                    raise
                logger.error("秘塔 API HTTP %s: %s", exc.response.status_code, body)
                raise RuntimeError(
                    f"秘塔搜索请求失败 (HTTP {exc.response.status_code}): {body or exc}"
                ) from exc
            except httpx.HTTPError as exc:
                logger.error("秘塔 API 请求失败: %s", exc)
                raise RuntimeError(f"秘塔搜索请求失败: {exc}") from exc

        try:
            data = with_retries(
                _do_search,
                attempts=self.retry_attempts,
                backoff_seconds=self.retry_backoff,
                label="秘塔搜索",
            )
        except httpx.HTTPStatusError as exc:
            body = (exc.response.text or "")[:300]
            raise RuntimeError(
                f"秘塔搜索请求失败 (HTTP {exc.response.status_code}): {body or exc}"
            ) from exc

        if not isinstance(data, (dict, list)):
            logger.warning("秘塔 API 返回未知顶层类型: %s", type(data).__name__)
            data = {}

        # API 业务错误（errCode）也记清楚，便于界面区分「无资讯」与「请求失败」
        if isinstance(data, dict) and data.get("errCode") not in (None, 0, "0"):
            err = data.get("errMsg") or data.get("errCode")
            raise RuntimeError(f"秘塔搜索失败: {err}")

        items = self._parse_items(data)[:max_results]
        if not items:
            logger.warning(
                "秘塔搜索未解析到任何结果，响应键: %s",
                list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )

        filtered = self._apply_local_domain_filter(items, whitelist_domains, blacklist_domains)
        if items and not filtered and whitelist_domains:
            logger.warning(
                "域名白名单过滤后结果为空（原始 %d 条），已降级为仅黑名单过滤",
                len(items),
            )
            filtered = self._apply_local_domain_filter(items, None, blacklist_domains)

        return MitaSearchResponse(query=effective_query, items=filtered, raw=data if isinstance(data, dict) else {"items": data})

    def _parse_items(self, data: dict[str, Any]) -> list[MitaSearchResultItem]:
        candidates: list[Any] = []
        answer_text = ""
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            payload_data = data.get("data")
            if isinstance(payload_data, dict):
                answer_text = str(payload_data.get("text") or "")
            for key in ("webpages", "results", "items", "data", "list", "records"):
                block = data.get(key)
                if isinstance(block, list):
                    candidates = block
                    break
                if isinstance(block, dict):
                    for sub in ("references", "webpages", "items", "list", "results"):
                        inner = block.get(sub)
                        if isinstance(inner, list):
                            candidates = inner
                            break
                if candidates:
                    break

        parsed: list[MitaSearchResultItem] = []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            url = str(
                row.get("url")
                or row.get("link")
                or row.get("href")
                or row.get("sourceUrl")
                or ((row.get("file_meta") or {}).get("url") if isinstance(row.get("file_meta"), dict) else "")
                or ""
            )
            if url.startswith("/"):
                url = urljoin("https://metaso.cn", url)
            snippet = str(
                row.get("snippet")
                or row.get("summary")
                or row.get("content")
                or row.get("abstract")
                or row.get("desc")
                or answer_text
                or ""
            )
            title = str(row.get("title") or row.get("name") or row.get("siteName") or url or "无标题")
            published = (
                row.get("published_at")
                or row.get("date")
                or row.get("publishTime")
                or row.get("publishDate")
            )
            domain = row.get("domain") or row.get("source_domain") or row.get("siteName")
            if not domain and url:
                domain = urlparse(url).netloc
            parsed.append(
                MitaSearchResultItem(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=str(published) if published else None,
                    source_domain=str(domain) if domain else None,
                )
            )
        if not parsed and answer_text:
            parsed.append(
                MitaSearchResultItem(
                    title="秘塔网络搜索摘要",
                    url="",
                    snippet=answer_text,
                    source_domain="metaso.cn",
                )
            )
        return parsed

    def _apply_local_domain_filter(
        self,
        items: list[MitaSearchResultItem],
        whitelist: Optional[list[str]],
        blacklist: Optional[list[str]],
    ) -> list[MitaSearchResultItem]:
        wl = {d.lower().strip() for d in (whitelist or []) if d}
        bl = {d.lower().strip() for d in (blacklist or []) if d}

        def domain_of(url: str) -> str:
            host = urlparse(url).netloc or url.split("/", 1)[0]
            return host.lower().removeprefix("www.")

        out: list[MitaSearchResultItem] = []
        for item in items:
            dom = (item.source_domain or domain_of(item.url)).lower()
            if bl and any(dom == b or dom.endswith("." + b) for b in bl):
                continue
            if wl and not any(dom == w or dom.endswith("." + w) for w in wl):
                continue
            out.append(item)
        return out

    def search_to_json_text(self, response: MitaSearchResponse) -> str:
        return json.dumps(
            [
                {
                    "title": i.title,
                    "url": i.url,
                    "snippet": i.snippet,
                    "published_at": i.published_at,
                    "source_domain": i.source_domain,
                }
                for i in response.items
            ],
            ensure_ascii=False,
        )
