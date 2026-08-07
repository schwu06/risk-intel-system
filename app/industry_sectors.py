"""深度研报常用行业分类与独立数据库键。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndustrySector:
    key: str
    label: str
    default_industry_name: str
    keywords: tuple[str, ...]


INDUSTRY_SECTORS: dict[str, IndustrySector] = {
    sector.key: sector
    for sector in (
        IndustrySector("aviation", "航空", "航空运输", ("航空", "飞机", "机场", "航司")),
        IndustrySector("shipping", "航运", "航运", ("航运", "船舶", "港口", "海运", "船")),
        IndustrySector("passenger_cars", "乘用车", "乘用车", ("乘用车", "汽车", "车企", "整车")),
        IndustrySector("tires", "轮胎", "轮胎", ("轮胎", "橡胶")),
        IndustrySector("energy_storage", "储能", "储能", ("储能", "电池", "电芯")),
        IndustrySector("power", "电力", "电力", ("电力", "发电", "电网", "火电", "水电", "光伏", "风电")),
    )
}


def sector_keys() -> tuple[str, ...]:
    return tuple(INDUSTRY_SECTORS.keys())


def require_sector_key(sector_key: str) -> IndustrySector:
    sector = INDUSTRY_SECTORS.get(sector_key)
    if not sector:
        raise KeyError(sector_key)
    return sector


def guess_sector_key(industry_name: str, default: str = "aviation") -> str:
    """根据行业名称关键词推断所属独立数据库。"""
    text = (industry_name or "").strip()
    if not text:
        return default
    for sector in INDUSTRY_SECTORS.values():
        if any(keyword in text for keyword in sector.keywords):
            return sector.key
    return default
