"""判断条目是否具备可发布的新闻实质（过滤检索入口占位等）。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# 标题/摘要中的非新闻模板
_NON_NEWS_TITLE = re.compile(
    r"(披露检索|法定披露|检索入口|请在\s*TDnet|请人工复核|DeepSeek\s*暂不可用)",
    re.I,
)
_PORTAL_ENTRY_MARKERS = re.compile(
    r"(query=|I_main_|BLMainController|I_search|WJEWZZ)",
    re.I,
)
_DOC_URL = re.compile(r"\.(pdf|zip)(?:$|\?)", re.I)


def _is_disclosure_document_url(url: str) -> bool:
    """TDnet PDF / 转发链接视为真实披露文档，而非检索入口。"""
    u = (url or "").strip()
    if not u:
        return False
    if _DOC_URL.search(u):
        return True
    if "/inbs/" in u.lower() and not _PORTAL_ENTRY_MARKERS.search(u):
        return True
    return False


def is_reference_only_item(item: dict[str, Any]) -> bool:
    if item.get("reference_only") is True:
        return True
    title = str(item.get("title") or item.get("标题") or "")
    snippet = str(
        item.get("snippet")
        or item.get("核心摘要")
        or item.get("summary")
        or ""
    )
    url = str(item.get("url") or item.get("来源链接") or "")
    if _NON_NEWS_TITLE.search(title) or _NON_NEWS_TITLE.search(snippet):
        return True
    # 真实披露 PDF / 文档链接允许入库
    if _is_disclosure_document_url(url):
        return False
    host = (urlparse(url).netloc or "").lower()
    url_l = url.lower()
    if any(m in host for m in ("release.tdnet.info", "edinet-fsa.go.jp", "disclosure.edinet")):
        return True
    if "tdnet" in url_l or "edinet" in url_l:
        if _PORTAL_ENTRY_MARKERS.search(url):
            return True
    return False


def is_substantive_news_item(item: dict[str, Any]) -> bool:
    """可用于降级入库或展示的真实资讯候选。"""
    if is_reference_only_item(item):
        return False
    title = (item.get("title") or item.get("标题") or "").strip()
    if not title or len(title) < 4:
        return False
    snippet = (
        item.get("body")
        or item.get("snippet")
        or item.get("核心摘要")
        or item.get("summary")
        or ""
    ).strip()
    url = (item.get("url") or item.get("来源链接") or "").strip()
    # 至少要有摘要或可点开的正文链接
    if len(snippet) < 12 and not url.startswith("http"):
        return False
    return True


def filter_publishable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        # 结构化行用中文字段
        probe = {
            "title": row.get("标题") or row.get("title"),
            "标题": row.get("标题"),
            "snippet": row.get("核心摘要") or row.get("snippet"),
            "核心摘要": row.get("核心摘要"),
            "url": row.get("来源链接") or row.get("url"),
            "来源链接": row.get("来源链接"),
            "reference_only": row.get("reference_only") or row.get("_reference_only"),
            "_degraded": row.get("_degraded"),
        }
        if row.get("_degraded") and not is_substantive_news_item(probe):
            continue
        if is_reference_only_item(probe):
            continue
        # 影响分析若仅为降级提示且摘要也像占位，丢弃
        impact = str(row.get("影响分析") or "")
        summary = str(row.get("核心摘要") or "")
        if "DeepSeek 暂不可用" in impact or "结构化分析暂不可用" in impact:
            if is_reference_only_item(probe) or len(summary) < 20:
                continue
        out.append(row)
    return out
