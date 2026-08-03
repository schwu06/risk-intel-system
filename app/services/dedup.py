"""稳定内容指纹：跨 RSS/秘塔去重，规避发布时间毫秒抖动。"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: Optional[str]) -> str:
    if not url:
        return ""
    raw = url.strip()
    try:
        parsed = urlparse(raw)
        query = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        cleaned = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=re.sub(r"/+$", "", parsed.path or "") or "/",
            query=urlencode(query),
            fragment="",
        )
        return urlunparse(cleaned)
    except Exception:
        return raw.lower()


def normalize_title(title: Optional[str]) -> str:
    text = (title or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text[:240]


def published_day_key(published_at: Optional[str]) -> str:
    """只用到日，避免 feedparser 毫秒差异导致 hash 抖动。"""
    if not published_at:
        return ""
    text = str(published_at).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text[:10]


def content_fingerprint(
    *,
    module_code: str,
    title: Optional[str],
    url: Optional[str] = None,
    published_at: Optional[str] = None,
) -> str:
    url_key = normalize_url(url)
    if url_key:
        seed = f"{module_code.upper()}|url|{url_key}"
    else:
        seed = (
            f"{module_code.upper()}|title|"
            f"{normalize_title(title)}|{published_day_key(published_at)}"
        )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]
