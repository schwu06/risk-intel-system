"""东京（Asia/Tokyo）时间工具：流水线刷新时间统一按东九区墙钟写入/返回。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TOKYO = ZoneInfo("Asia/Tokyo")


def tokyo_now() -> datetime:
    """当前东京时间（naive，写入 SQLite DateTime）。"""
    return datetime.now(TOKYO).replace(tzinfo=None)


def tokyo_isoformat(dt: datetime | None, *, timespec: str = "seconds") -> str | None:
    """序列化为带 +09:00 的 ISO 字符串，供前端按东京时间解析。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=TOKYO)
    else:
        aware = dt.astimezone(TOKYO)
    return aware.isoformat(timespec=timespec)
