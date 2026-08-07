"""RSS / Google News 近 24 小时资讯采集（支持外置 YAML 配置）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import mktime
from typing import Optional
from urllib.parse import quote_plus, urlparse

import httpx

from app.config import get_settings
from app.services.dedup import content_fingerprint
from app.services.http_retry import with_retries
from app.services.recency import is_within_hours, parse_published_at
from app.services.rss_config import RssQuerySpec, load_rss_config

logger = logging.getLogger(__name__)

_UA = "RiskIntelBot/1.2 (+local; RSS collector)"


@dataclass
class RssNewsItem:
    title: str
    url: str
    snippet: str
    published_at: Optional[str] = None
    source_domain: Optional[str] = None
    feed_label: Optional[str] = None
    fingerprint: Optional[str] = None
    # Google News <source> 或标题后缀解析出的真实媒体名
    publisher: Optional[str] = None
    # 主体目录赋予的采集范围；用于阻止跨主体误归属。
    entity_key: Optional[str] = None
    relation: str = "unscoped"
    source_type: str = "media"
    configured_source_url: Optional[str] = None


@dataclass
class FeedHealth:
    label: str
    url: str
    ok: bool
    item_count: int = 0
    error: Optional[str] = None


@dataclass
class CollectResult:
    items: list[RssNewsItem] = field(default_factory=list)
    feed_health: list[FeedHealth] = field(default_factory=list)
    fetch_ok: int = 0
    fetch_errors: int = 0


def google_news_rss_url(
    query: str,
    *,
    hl: str = "zh-CN",
    gl: str = "CN",
    ceid: str = "CN:zh-Hans",
) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    )


class RssNewsCollector:
    """采集 Google News RSS + 精选源，并按小时窗过滤。"""

    def __init__(self, timeout: int = 25, retry_attempts: int = 3, retry_backoff: float = 1.5) -> None:
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff
        settings = get_settings()
        path = getattr(settings, "rss_config_path", None) or "config/rss_feeds.yaml"
        self.config = load_rss_config(path)

    def collect_for_module(
        self,
        module_code: str,
        *,
        hours: int = 24,
        max_items: int = 40,
        allow_unknown: bool = False,
        entity_key: str | None = None,
        extra_queries: list[RssQuerySpec] | None = None,
    ) -> list[RssNewsItem]:
        return self.collect_detailed(
            module_code,
            hours=hours,
            max_items=max_items,
            allow_unknown=allow_unknown,
            entity_key=entity_key,
            extra_queries=extra_queries,
        ).items

    def collect_detailed(
        self,
        module_code: str,
        *,
        hours: int = 24,
        max_items: int = 40,
        allow_unknown: bool = False,
        entity_key: str | None = None,
        extra_queries: list[RssQuerySpec] | None = None,
    ) -> CollectResult:
        code = module_code.upper()
        seen: set[str] = set()
        result = CollectResult()
        cfg = self.config

        # 1) Google News 查询（按 priority 降序）
        configured_queries = list(cfg.queries.get(code, [])) + list(extra_queries or [])
        queries = sorted(
            [
                q
                for q in configured_queries
                if q.enabled and (not entity_key or not q.entity_key or q.entity_key == entity_key)
            ],
            key=lambda x: x.priority,
            reverse=True,
        )
        for q in queries:
            hl, gl, ceid = cfg.resolve_google_locale(
                code, hl=q.google_hl, gl=q.google_gl, ceid=q.google_ceid
            )
            url = google_news_rss_url(q.query, hl=hl, gl=gl, ceid=ceid)
            hits, health = self._fetch_feed_safe(url, feed_label=q.label)
            for hit in hits:
                hit.entity_key = q.entity_key
                hit.relation = q.relation
                hit.source_type = q.source_type
                hit.configured_source_url = q.source_url
            result.feed_health.append(health)
            if health.ok:
                result.fetch_ok += 1
            else:
                result.fetch_errors += 1
            limit = int(q.max_items or cfg.max_items_per_feed)
            self._merge_hits(
                hits[:limit],
                result.items,
                seen,
                module_code=code,
                hours=hours,
                allow_unknown=True,
                max_items=max_items,
            )
            if len(result.items) >= max_items:
                break

        # 2) 直连精选源（保底；即使 Google 全挂也尽量有内容）
        feeds = sorted(
            [
                f
                for f in cfg.feeds
                if f.enabled
                and code in f.modules
                and (not entity_key or not f.entity_key or f.entity_key == entity_key)
            ],
            key=lambda x: x.priority,
            reverse=True,
        )
        for feed in feeds:
            if len(result.items) >= max_items:
                break
            feed_url = feed.url
            if feed.feed_type == "google":
                hl, gl, ceid = cfg.resolve_google_locale(
                    code, hl=feed.google_hl, gl=feed.google_gl, ceid=feed.google_ceid
                )
                feed_url = google_news_rss_url(feed.url, hl=hl, gl=gl, ceid=ceid)
            hits, health = self._fetch_feed_safe(feed_url, feed_label=feed.label)
            for hit in hits:
                hit.entity_key = feed.entity_key
                hit.relation = feed.relation
                hit.source_type = feed.source_type
                hit.configured_source_url = feed.url
            result.feed_health.append(health)
            if health.ok:
                result.fetch_ok += 1
            else:
                result.fetch_errors += 1
            limit = int(feed.max_items or cfg.max_items_per_feed)
            self._merge_hits(
                hits[:limit],
                result.items,
                seen,
                module_code=code,
                hours=hours,
                allow_unknown=False,
                max_items=max_items,
            )

        logger.info(
            "RSS 模块 %s 采集 %d 条（%dh 窗，成功源 %d，失败源 %d）",
            code,
            len(result.items),
            hours,
            result.fetch_ok,
            result.fetch_errors,
        )
        if not result.items and result.fetch_errors and not result.fetch_ok:
            raise RuntimeError(f"RSS 全部源请求失败（{result.fetch_errors} 个）")
        return result

    def _merge_hits(
        self,
        hits: list[RssNewsItem],
        items: list[RssNewsItem],
        seen: set[str],
        *,
        module_code: str,
        hours: int,
        allow_unknown: bool,
        max_items: int,
    ) -> None:
        for hit in hits:
            fp = hit.fingerprint or content_fingerprint(
                module_code=module_code,
                title=hit.title,
                url=hit.url,
                published_at=hit.published_at,
            )
            hit.fingerprint = fp
            if fp in seen:
                continue
            if not is_within_hours(hit.published_at, hours, allow_unknown=allow_unknown):
                continue
            seen.add(fp)
            items.append(hit)
            if len(items) >= max_items:
                return

    def _fetch_feed_safe(self, feed_url: str, *, feed_label: str) -> tuple[list[RssNewsItem], FeedHealth]:
        try:
            hits = self._fetch_feed(feed_url, feed_label=feed_label)
            return hits, FeedHealth(label=feed_label, url=feed_url, ok=True, item_count=len(hits))
        except Exception as exc:
            logger.warning("RSS 拉取失败 %s: %s", feed_url, exc)
            return [], FeedHealth(
                label=feed_label, url=feed_url, ok=False, error=str(exc)[:300]
            )

    def _fetch_feed(self, feed_url: str, *, feed_label: str) -> list[RssNewsItem]:
        try:
            import feedparser
        except ImportError:
            logger.warning("未安装 feedparser，跳过 RSS 采集")
            raise RuntimeError("未安装 feedparser，请执行: pip install feedparser") from None

        def _get() -> bytes:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(
                    feed_url,
                    headers={
                        "User-Agent": _UA,
                        "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    },
                )
                resp.raise_for_status()
                return resp.content

        raw = with_retries(
            _get,
            attempts=self.retry_attempts,
            backoff_seconds=self.retry_backoff,
            label=f"RSS:{feed_label}",
        )

        parsed = feedparser.parse(raw)
        out: list[RssNewsItem] = []
        for entry in getattr(parsed, "entries", []) or []:
            title = str(getattr(entry, "title", "") or "").strip() or "无标题"
            link = str(getattr(entry, "link", "") or "").strip()
            summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
            summary = re_strip_tags(summary)[:500]
            published = _entry_published(entry)
            domain = urlparse(link).netloc if link else None
            publisher = _entry_publisher(entry, title=title, snippet=summary, domain=domain)
            out.append(
                RssNewsItem(
                    title=title,
                    url=link,
                    snippet=summary,
                    published_at=published.isoformat() if published else None,
                    source_domain=domain,
                    feed_label=feed_label,
                    publisher=publisher,
                )
            )
        return out


def re_strip_tags(html: str) -> str:
    import re

    text = re.sub(r"(?is)<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _entry_publisher(
    entry,
    *,
    title: str,
    snippet: str,
    domain: Optional[str],
) -> Optional[str]:
    """尽量解析真实媒体名（Google News <source> / 标题后缀 / 域名）。"""
    src = getattr(entry, "source", None)
    if src is not None:
        name = ""
        if isinstance(src, dict):
            name = str(src.get("title") or "").strip()
        else:
            name = str(getattr(src, "title", "") or "").strip()
        if name:
            return name[:128]

    # Google News 常见标题：正文 - 媒体名
    if " - " in (title or ""):
        tail = title.rsplit(" - ", 1)[-1].strip()
        if 1 < len(tail) <= 64 and "http" not in tail.lower():
            return tail

    # 摘要里偶发 "...  MediaName"
    if snippet:
        import re

        m = re.search(r"(?:\u00a0|\s){2,}([^\n|]{2,64})\s*$", snippet)
        if m:
            return m.group(1).strip()[:128]

    if domain and "news.google." not in domain.lower():
        return domain.removeprefix("www.")[:128]
    return None


def _entry_published(entry) -> Optional[datetime]:
    for attr in ("published", "updated", "created"):
        val = getattr(entry, attr, None)
        if val:
            dt = parse_published_at(val)
            if dt:
                return dt
    for attr in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, attr, None)
        if struct:
            try:
                return datetime.fromtimestamp(mktime(struct), tz=timezone.utc)
            except (OverflowError, TypeError, ValueError):
                continue
    return None
