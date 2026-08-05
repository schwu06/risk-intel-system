"""页面展示用简体中文：库内保留原文，渲染时翻译并缓存。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.deepseek_analyzer import DeepSeekAnalyzer
from app.services.llm_cache import get_cached_items, material_hash, set_cached_items
from app.services.api_keys import is_placeholder_key
from app.config import get_settings

logger = logging.getLogger(__name__)

_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_DEGRADED_ZH = "结构化分析暂不可用"


@dataclass
class NewsDisplayCard:
    """日报卡片展示对象（不回写数据库）。"""

    id: int
    module_code: str
    title: str
    related_company: Optional[str]
    risk_category: Optional[str]
    category_tag: Optional[str]
    risk_level: str
    summary: str
    impact_analysis: Optional[str]
    source_url: Optional[str]
    published_at: Any
    social_source_label: str = "暂无"
    social_source_url: Optional[str] = None


def needs_chinese_display(text: Optional[str]) -> bool:
    """判断文本是否需要译成简体中文展示。"""
    t = (text or "").strip()
    if not t:
        return False
    if _DEGRADED_ZH in t:
        return False
    if _KANA_RE.search(t):
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 18:
        latin = sum(1 for c in letters if "a" <= c.lower() <= "z")
        if latin / len(letters) >= 0.72:
            return True
    # 几乎无汉字且含较多非中文符号数字标题——仍交由后续批处理跳过短串
    han = len(_HAN_RE.findall(t))
    if han == 0 and len(t) >= 12 and any(c.isalpha() for c in t):
        return True
    return False


def _translate_batch(items: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """调用 DeepSeek 批量翻译，返回 id → {title,summary,impact}。"""
    if not items:
        return {}
    settings = get_settings()
    if is_placeholder_key(getattr(settings, "deepseek_api_key", None)):
        return {}
    analyzer = DeepSeekAnalyzer()
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    system = (
        "你是金融资讯翻译。将输入 json 数组中每条新闻的 title/summary/impact "
        "译为准确、简洁的简体中文，专有名词可保留原文。"
        "输出必须是合法 JSON 对象（json），格式："
        '{"items":[{"id":"...","title":"...","summary":"...","impact":"..."}]}。'
        "不要编造事实；impact 若为系统提示语（如结构化分析暂不可用）则原样返回中文提示。"
        "不要输出 markdown，只输出 json。"
    )
    user = f"请翻译以下资讯字段为简体中文：\n{payload}"
    try:
        # 复用 chat JSON 对象接口
        raw = analyzer._request_chat(system, user)  # noqa: SLF001 — 内部展示翻译
        text = raw.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        rows = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return {}
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            if not rid:
                continue
            out[rid] = {
                "title": str(row.get("title") or "").strip(),
                "summary": str(row.get("summary") or "").strip(),
                "impact": str(row.get("impact") or "").strip(),
            }
        return out
    except Exception as exc:
        logger.warning("展示翻译失败，回退原文: %s", exc)
        return {}


def build_display_cards(
    db: Session,
    entries: list[Any],
    *,
    social_resolver,
) -> list[NewsDisplayCard]:
    """为日报条目生成中文展示卡片；库内原文不变。"""
    pending: list[dict[str, str]] = []
    cache_hits: dict[str, dict[str, str]] = {}

    for e in entries:
        eid = str(e.id)
        title = e.title or ""
        summary = e.summary or ""
        impact = e.impact_analysis or ""
        if not (
            needs_chinese_display(title)
            or needs_chinese_display(summary)
            or needs_chinese_display(impact)
        ):
            continue
        key = material_hash(
            f"{title}\n{summary}\n{impact}",
            module_code="ZH",
            source="display_zh",
        )
        cached = get_cached_items(db, material_key=key, max_age_hours=720)
        if cached and isinstance(cached, list) and cached and isinstance(cached[0], dict):
            cache_hits[eid] = {
                "title": str(cached[0].get("title") or title),
                "summary": str(cached[0].get("summary") or summary),
                "impact": str(cached[0].get("impact") or impact),
            }
            continue
        pending.append(
            {
                "id": eid,
                "title": title[:300],
                "summary": summary[:800],
                "impact": impact[:500],
                "_cache_key": key,
            }
        )

    # 批量翻译（限制单次条数，避免首屏超时；其余下次访问继续补齐）
    translated: dict[str, dict[str, str]] = dict(cache_hits)
    max_per_request = 12
    pending = pending[:max_per_request]
    chunk_size = 6
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i : i + chunk_size]
        payload = [
            {k: v for k, v in row.items() if not k.startswith("_")} for row in chunk
        ]
        result = _translate_batch(payload)
        for row in chunk:
            eid = row["id"]
            t = result.get(eid)
            if not t:
                continue
            translated[eid] = {
                "title": t.get("title") or row["title"],
                "summary": t.get("summary") or row["summary"],
                "impact": t.get("impact") or row["impact"],
            }
            set_cached_items(
                db,
                material_key=row["_cache_key"],
                module_code="ZH",
                source="display_zh",
                items=[translated[eid]],
            )

    cards: list[NewsDisplayCard] = []
    for e in entries:
        eid = str(e.id)
        tr = translated.get(eid) or {}
        social = social_resolver(e)
        cards.append(
            NewsDisplayCard(
                id=e.id,
                module_code=e.module_code,
                title=tr.get("title") or e.title,
                related_company=e.related_company,
                risk_category=e.risk_category,
                category_tag=getattr(e, "category_tag", None),
                risk_level=e.risk_level,
                summary=tr.get("summary") or e.summary,
                impact_analysis=tr.get("impact") if tr.get("impact") is not None else e.impact_analysis,
                source_url=e.source_url,
                published_at=e.published_at,
                social_source_label=social.get("social_source_label") or "暂无",
                social_source_url=social.get("social_source_url"),
            )
        )
    return cards
