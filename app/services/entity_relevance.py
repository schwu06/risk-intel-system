"""主体评估的确定性归属门禁与信号字段归一化。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from app.database.models import TargetEntity
from app.services.entity_catalog import EntityProfile


_IMPACT_TO_RISK = {
    "none": "低",
    "low": "中",
    "medium": "高",
    "high": "极高",
    "critical": "极高",
}
_VALID_DIRECTIONS = {"positive", "neutral", "negative", "unknown"}
_VALID_IMPACTS = set(_IMPACT_TO_RISK)
_VALID_IMPORTANCE = {"低", "中", "高", "极高"}


def _normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _contains_name(text: str, name: str) -> bool:
    needle = _normalize(name).strip()
    if len(needle) < 2:
        return False
    if re.fullmatch(r"[a-z0-9&.\- ]+", needle):
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return needle in text


def entity_names(entity: TargetEntity, profile: EntityProfile | None) -> tuple[str, ...]:
    names: list[str] = [entity.name, entity.display_name or ""]
    if entity.aliases:
        names.extend(alias.strip() for alias in entity.aliases.split(",") if alias.strip())
    if profile:
        names.extend(profile.all_names)
    return tuple(dict.fromkeys(name for name in names if name))


def classify_entity_relevance(
    entity: TargetEntity,
    row: dict[str, Any],
    profile: EntityProfile | None,
) -> str:
    """返回 direct/contextual/unrelated；不单独信任模型给出的归属结论。"""
    source_item = row.get("_source_item") if isinstance(row.get("_source_item"), dict) else {}
    if not source_item:
        # 自动分析必须能回连到一条采集候选；不信任无法溯源的模型归属判断。
        return "unrelated"

    text = _normalize(
        " ".join(
            str(value or "")
            for value in (
                row.get("标题"),
                row.get("关联企业"),
                row.get("核心摘要"),
                source_item.get("title"),
                source_item.get("snippet"),
                source_item.get("body"),
                source_item.get("company"),
            )
        )
    )
    if any(_contains_name(text, name) for name in entity_names(entity, profile)):
        return "direct"

    scoped_key = str(source_item.get("entity_key") or "").strip()
    same_scope = bool(profile and scoped_key == profile.key)
    relation = str(source_item.get("relation") or "unscoped").lower()
    source_type = str(source_item.get("source_type") or "media").lower()
    if same_scope and relation == "contextual":
        return "contextual"
    if same_scope and relation == "direct" and source_type in {
        "official",
        "regulatory",
        "exchange",
    }:
        return "direct"
    return "unrelated"


def _confidence(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def source_name_for_row(row: dict[str, Any]) -> str | None:
    source_item = row.get("_source_item") if isinstance(row.get("_source_item"), dict) else {}
    explicit = str(
        row.get("来源名称")
        or source_item.get("publisher")
        or source_item.get("feed")
        or ""
    ).strip()
    if explicit:
        return explicit
    url = str(row.get("来源链接") or source_item.get("url") or "").strip()
    return urlparse(url).netloc or None


def prepare_entity_row(
    entity: TargetEntity,
    row: dict[str, Any],
    profile: EntityProfile | None,
) -> bool:
    """归一并校验一条模型输出；无关条目返回 False。"""
    relevance = classify_entity_relevance(entity, row, profile)
    if relevance == "unrelated":
        return False

    source_item = row.get("_source_item") if isinstance(row.get("_source_item"), dict) else {}
    relation = str(source_item.get("relation") or "unscoped").lower()
    if relation == "contextual":
        relevance = "contextual"

    direction = str(row.get("影响方向") or "unknown").strip().lower()
    if direction not in _VALID_DIRECTIONS:
        direction = "unknown"
    impact = str(row.get("信用风险信号") or "none").strip().lower()
    if impact not in _VALID_IMPACTS:
        impact = "none"
    if relevance != "direct" or direction != "negative" or row.get("_degraded"):
        impact = "none"

    importance = str(row.get("资讯重要度") or row.get("风险等级") or "中").strip()
    if importance not in _VALID_IMPORTANCE:
        importance = "中"

    row["关联企业"] = entity.display_name or entity.name
    row["主体相关性"] = relevance
    row["影响方向"] = direction
    row["信用风险信号"] = impact
    row["资讯重要度"] = importance
    row["风险等级"] = _IMPACT_TO_RISK[impact]
    row["来源名称"] = source_name_for_row(row) or ""
    row["_entity_relevance"] = relevance
    row["_confidence"] = _confidence(row.get("置信度"))
    return True
