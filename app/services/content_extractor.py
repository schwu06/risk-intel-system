"""网页正文提取（优先 trafilatura，失败回退 HTML 清洗）。"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (compatible; RiskIntelBot/1.1; +https://localhost) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_article_text(url: str, timeout: int = 25, max_chars: int = 8000) -> str:
    """抓取 URL 并提取可读正文。"""
    if not url or not url.startswith(("http://", "https://")):
        return ""
    host = urlparse(url).netloc.lower()
    # 跳过明显不可抓的门户检索入口
    if any(x in host for x in ("edinet-fsa.go.jp", "release.tdnet.info")):
        return ""

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})
            if resp.status_code >= 400:
                return ""
            html = resp.text or ""
            content_type = (resp.headers.get("content-type") or "").lower()
            if "html" not in content_type and "<html" not in html[:500].lower():
                return (html or "")[:max_chars].strip()
    except httpx.HTTPError as exc:
        logger.debug("正文抓取失败 %s: %s", url, exc)
        return ""

    text = _extract_with_trafilatura(html, url) or _strip_html(html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def _extract_with_trafilatura(html: str, url: str) -> Optional[str]:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        result = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        return (result or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("trafilatura 提取失败: %s", exc)
        return None


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def enrich_items_with_body(
    items: list[dict],
    *,
    url_key: str = "url",
    body_key: str = "body",
    max_items: int = 8,
    max_chars: int = 6000,
) -> list[dict]:
    """为前 N 条结果补充正文，便于下游 LLM 分析。"""
    enriched: list[dict] = []
    fetched = 0
    for row in items:
        item = dict(row)
        url = str(item.get(url_key) or "")
        if fetched < max_items and url:
            body = extract_article_text(url, max_chars=max_chars)
            if body:
                item[body_key] = body
                fetched += 1
        enriched.append(item)
    return enriched
