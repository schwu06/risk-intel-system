"""评级抓取引擎。

优先官方 API（待补充）；未上市且仍为 NR 时，用 Playwright 抓取
新浪/东财债券相关公开页与评级机构公开发布页。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from intl_ratings.config import SourcesConfig
from intl_ratings.engines.listing import looks_like_ticker
from intl_ratings.engines.playwright_ratings import PlaywrightRatingsScraper
from intl_ratings.logging_utils import ErrorIssuerLogger, RawResponseStore
from intl_ratings.models import NEED_REVIEW, NO, NR, EntityMapping, RatingSnapshot

logger = logging.getLogger(__name__)

PENDING_RATING_SOURCES = (
    "moodys_api（Moody's Analytics / RatingsHub）",
    "sp_api（S&P Capital IQ / RatingsDirect）",
    "fitch_api（Fitch Connect）",
    "rating_change_feed（近 90 日评级变动历史）",
    "中债/中国货币网主体评级正式接口（函数名与鉴权待确认）",
)


class RatingsEngine:
    def __init__(
        self,
        sources: SourcesConfig,
        raw_store: Optional[RawResponseStore] = None,
        error_log: Optional[ErrorIssuerLogger] = None,
        placeholders_nr: str = NR,
        playwright_scraper: Optional[PlaywrightRatingsScraper] = None,
    ) -> None:
        self.sources = sources
        self.raw_store = raw_store
        self.error_log = error_log
        self.nr = placeholders_nr
        self.playwright_scraper = playwright_scraper

    def fetch(self, mapping: EntityMapping) -> RatingSnapshot:
        moodys = self.nr
        sp = self.nr
        fitch = self.nr
        changed = NO
        source_bits: list[str] = []

        if not (self.sources.moodys_api or self.sources.sp_api or self.sources.fitch_api):
            source_bits.append("skipped_official_apis")
        else:
            if self.error_log:
                self.error_log.log(
                    mapping.issuer_name,
                    "ratings",
                    "sources.*_api 已开启但适配器未实现",
                )
            moodys = NEED_REVIEW
            sp = NEED_REVIEW
            fitch = NEED_REVIEW
            source_bits.append("official_apis_stub")

        if self.sources.akshare:
            self._probe_zjl_em(mapping)
            source_bits.append("ak_zjl_em_probed_not_rating")

        # 未上市 + 三大仍为 NR → Playwright 公开页兜底
        unlisted = not looks_like_ticker(mapping.stock_ticker)
        all_nr = self._is_nr(moodys) and self._is_nr(sp) and self._is_nr(fitch)
        if (
            unlisted
            and all_nr
            and self.sources.playwright_ratings
            and self.playwright_scraper is not None
        ):
            scraped = self.playwright_scraper.scrape(mapping)
            source_bits.append("playwright_fallback")
            if scraped.get("moodys"):
                moodys = str(scraped["moodys"])
            if scraped.get("sp"):
                sp = str(scraped["sp"])
            if scraped.get("fitch"):
                fitch = str(scraped["fitch"])
            if not (scraped.get("moodys") or scraped.get("sp") or scraped.get("fitch")):
                source_bits.append("playwright_no_hit")
        elif unlisted and all_nr and not self.sources.playwright_ratings:
            source_bits.append("playwright_disabled")

        if self.sources.rating_change_feed:
            changed = NEED_REVIEW
            if self.error_log:
                self.error_log.log(
                    mapping.issuer_name,
                    "评级是否变化",
                    "rating_change_feed 已开启但适配器未实现",
                )
        else:
            changed = NO
            source_bits.append("rating_change_skipped")

        snap = RatingSnapshot(
            moodys=moodys or self.nr,
            sp=sp or self.nr,
            fitch=fitch or self.nr,
            rating_changed=changed,
            raw_source=",".join(source_bits) or "none",
        )
        if self.raw_store:
            self.raw_store.save(mapping.issuer_name, "ratings_engine", snap.model_dump())
        return snap

    @staticmethod
    def _is_nr(value: str) -> bool:
        raw = (value or "").strip()
        if raw == NEED_REVIEW:
            return True
        s = raw.upper()
        return s in {"", NR, "N/A", "—", "-"}

    def _probe_zjl_em(self, mapping: EntityMapping) -> None:
        ticker = (mapping.stock_ticker or "").split(".")[0]
        if not (ticker.isdigit() and len(ticker) == 6):
            return
        try:
            import akshare as ak  # type: ignore
        except ImportError:
            return
        fn = getattr(ak, "stock_comment_detail_zjl_em", None)
        if not callable(fn):
            return
        try:
            raw = fn(symbol=ticker)
            if self.raw_store:
                self.raw_store.save(
                    mapping.issuer_name,
                    "akshare_stock_comment_detail_zjl_em",
                    {
                        "symbol": ticker,
                        "note": "该接口为资金流向，非穆迪/标普/惠誉主体评级",
                        "preview": str(raw)[:2000],
                    },
                )
        except Exception as exc:
            logger.debug("zjl_em 探测失败: %s", exc)


def ratings_pending_notice() -> str:
    return "【待补充数据源】" + "；".join(PENDING_RATING_SOURCES)
