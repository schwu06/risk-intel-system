"""可配置的主体监控清单与公开信源目录。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.config import get_settings
from app.services.rss_config import RssQuerySpec


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _ROOT / "config" / "entity_targets.yaml"


@dataclass(frozen=True)
class EntitySourceSpec:
    label: str
    url: str
    source_type: str
    relation: str = "direct"
    priority: int = 10
    query: str | None = None
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "url": self.url,
            "source_type": self.source_type,
            "relation": self.relation,
            "priority": self.priority,
            "query": self.query,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class EntityProfile:
    key: str
    display_name: str
    aliases: tuple[str, ...] = ()
    industry: str | None = None
    region: str | None = None
    stock_code: str | None = None
    sources: tuple[EntitySourceSpec, ...] = field(default_factory=tuple)

    @property
    def all_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.key, self.display_name, *self.aliases)))

    def rss_queries(self) -> list[RssQuerySpec]:
        return [
            RssQuerySpec(
                label=source.label,
                query=source.query or "",
                priority=source.priority,
                enabled=source.enabled,
                entity_key=self.key,
                relation=source.relation,
                source_type=source.source_type,
                source_url=source.url,
            )
            for source in self.sources
            if source.enabled and source.query
        ]

    def as_seed(self) -> dict[str, str | None]:
        return {
            "name": self.key,
            "display_name": self.display_name,
            "aliases": ",".join(self.aliases),
            "industry": self.industry,
            "region": self.region,
        }


@dataclass(frozen=True)
class EntityCatalog:
    profiles: tuple[EntityProfile, ...]

    def find(self, names: Iterable[str | None]) -> EntityProfile | None:
        needles = [_normalize_name(name) for name in names if name and str(name).strip()]
        if not needles:
            return None
        for profile in self.profiles:
            candidates = {_normalize_name(name) for name in profile.all_names}
            if any(
                needle == candidate or needle in candidate or candidate in needle
                for needle in needles
                for candidate in candidates
                if candidate
            ):
                return profile
        return None


def _normalize_name(value: str | None) -> str:
    return "".join(str(value or "").casefold().split())


def _parse_source(row: dict[str, Any]) -> EntitySourceSpec | None:
    label = str(row.get("label") or "").strip()
    url = str(row.get("url") or "").strip()
    source_type = str(row.get("source_type") or "media").strip().lower()
    relation = str(row.get("relation") or "direct").strip().lower()
    if not label or not url:
        return None
    if relation not in {"direct", "contextual"}:
        relation = "direct"
    return EntitySourceSpec(
        label=label,
        url=url,
        source_type=source_type,
        relation=relation,
        priority=int(row.get("priority") or 10),
        query=str(row["query"]).strip() if row.get("query") else None,
        enabled=bool(row.get("enabled", True)),
    )


def _parse_catalog(data: dict[str, Any]) -> EntityCatalog:
    profiles: list[EntityProfile] = []
    seen: set[str] = set()
    for row in data.get("entities") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key or _normalize_name(key) in seen:
            continue
        seen.add(_normalize_name(key))
        sources = tuple(
            source
            for source in (_parse_source(item) for item in (row.get("sources") or []))
            if source is not None
        )
        profiles.append(
            EntityProfile(
                key=key,
                display_name=str(row.get("display_name") or key).strip(),
                aliases=tuple(
                    str(alias).strip()
                    for alias in (row.get("aliases") or [])
                    if str(alias).strip()
                ),
                industry=str(row["industry"]).strip() if row.get("industry") else None,
                region=str(row["region"]).strip() if row.get("region") else None,
                stock_code=str(row["stock_code"]).strip() if row.get("stock_code") else None,
                sources=sources,
            )
        )
    return EntityCatalog(tuple(profiles))


@lru_cache(maxsize=4)
def load_entity_catalog(path: str | None = None) -> EntityCatalog:
    target = Path(path) if path else _DEFAULT_PATH
    if not target.is_absolute():
        target = _ROOT / target
    if not target.is_file():
        return EntityCatalog(())
    try:
        import yaml

        with target.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return EntityCatalog(())
    return _parse_catalog(data if isinstance(data, dict) else {})


def configured_entity_catalog() -> EntityCatalog:
    path = getattr(get_settings(), "entity_targets_config_path", None) or None
    return load_entity_catalog(path)


def reload_entity_catalog(path: str | None = None) -> EntityCatalog:
    load_entity_catalog.cache_clear()
    return load_entity_catalog(path)
