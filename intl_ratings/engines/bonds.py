"""债券价格异动：yfinance + tvDatafeed + AkShare bond_zh_cov。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from intl_ratings.config import BondPriceConfig, SourcesConfig
from intl_ratings.engines.tvdatafeed_client import TvDatafeedClient
from intl_ratings.formulas import calc_month_change_pct, judge_bond_price_drop
from intl_ratings.logging_utils import ErrorIssuerLogger, RawResponseStore
from intl_ratings.models import NO_PUBLIC_TRADE, BondPriceSnapshot, EntityMapping

logger = logging.getLogger(__name__)


class BondPriceEngine:
    def __init__(
        self,
        sources: SourcesConfig,
        bond_cfg: BondPriceConfig,
        raw_store: Optional[RawResponseStore] = None,
        error_log: Optional[ErrorIssuerLogger] = None,
        no_public_label: str = NO_PUBLIC_TRADE,
        tv_client: Optional[TvDatafeedClient] = None,
    ) -> None:
        self.sources = sources
        self.bond_cfg = bond_cfg
        self.raw_store = raw_store
        self.error_log = error_log
        self.no_public_label = no_public_label
        self.threshold = -abs(float(bond_cfg.drop_threshold_pct))
        self.tv_client = tv_client

    def fetch(self, mapping: EntityMapping) -> BondPriceSnapshot:
        if not self.sources.bond_price_public:
            return BondPriceSnapshot(
                price_drop_flag=self.no_public_label,
                raw_source="disabled",
            )

        details: list[dict[str, Any]] = []
        best_change: Optional[float] = None
        checked: list[str] = []

        # 1) tvDatafeed（TradingView 债券行情优先）
        if self.sources.tvdatafeed and self.tv_client is not None:
            chg, meta = self.tv_client.month_change_pct(mapping)
            details.append(meta)
            if chg is not None:
                best_change = chg
                checked.append(f"TV:{meta.get('exchange')}:{meta.get('symbol')}")

        # 2) yfinance：bond_tickers
        tickers = [t for t in (mapping.bond_tickers or []) if t and ":" not in t]
        if best_change is None and tickers and self.sources.yfinance:
            for bt in tickers:
                chg, meta = self._yf_1mo_change(bt)
                details.append(meta)
                if chg is None:
                    continue
                checked.append(bt)
                if best_change is None or chg < best_change:
                    best_change = chg

        # 3) AkShare 可转债截面（通常无法算 30 日）
        if best_change is None and self.sources.akshare:
            chg, meta = self._ak_bond_zh_cov(mapping)
            details.append(meta)
            if chg is not None:
                best_change = chg
                checked.extend(meta.get("matched") or [])

        if self.raw_store:
            self.raw_store.save(
                mapping.issuer_name,
                "bond_price",
                {"details": details, "threshold": self.threshold, "best_change_pct": best_change},
            )

        if best_change is None:
            if self.error_log and not tickers and not mapping.tv_symbol:
                self.error_log.log(
                    mapping.issuer_name,
                    "债券价格",
                    "无 tv_symbol / bond_tickers；无法计算月环比",
                )
            return BondPriceSnapshot(
                price_drop_flag=self.no_public_label,
                tickers_checked=checked,
                raw_source="no_public_data",
            )

        flag = judge_bond_price_drop(
            best_change,
            threshold_pct=self.threshold,
            no_public_label=self.no_public_label,
        )
        return BondPriceSnapshot(
            price_drop_flag=flag,
            change_pct=best_change,
            max_drop_pct=(-best_change if best_change < 0 else 0.0),
            tickers_checked=checked,
            raw_source="tvdatafeed|yfinance|akshare",
        )

    def _yf_1mo_change(self, ticker: str) -> tuple[Optional[float], dict[str, Any]]:
        meta: dict[str, Any] = {"ticker": ticker, "source": "yfinance.history(period=1mo)"}
        try:
            import yfinance as yf  # type: ignore
        except ImportError:
            meta["error"] = "yfinance_missing"
            return None, meta
        try:
            hist = yf.Ticker(ticker).history(period="1mo")
            if hist is None or hist.empty or "Close" not in hist.columns:
                meta["status"] = "no_data"
                return None, meta
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                meta["status"] = "too_short"
                return None, meta
            price_30d_ago = float(closes.iloc[0])
            price_now = float(closes.iloc[-1])
            change_pct = calc_month_change_pct(price_now, price_30d_ago)
            meta.update(
                {
                    "price_30d_ago": price_30d_ago,
                    "price_now": price_now,
                    "change_pct": change_pct,
                    "n_bars": len(closes),
                }
            )
            return change_pct, meta
        except Exception as exc:
            meta["error"] = str(exc)
            return None, meta

    def _ak_bond_zh_cov(self, mapping: EntityMapping) -> tuple[Optional[float], dict[str, Any]]:
        meta: dict[str, Any] = {"source": "ak.bond_zh_cov", "matched": []}
        try:
            import akshare as ak  # type: ignore
        except ImportError:
            meta["error"] = "akshare_missing"
            return None, meta
        fn = getattr(ak, "bond_zh_cov", None)
        if not callable(fn):
            meta["error"] = "bond_zh_cov_missing"
            return None, meta
        try:
            df = fn()
            if self.raw_store:
                self.raw_store.save(
                    mapping.issuer_name,
                    "akshare_bond_zh_cov_head",
                    {"columns": list(getattr(df, "columns", [])), "nrows": len(df) if df is not None else 0},
                )
            if df is None or getattr(df, "empty", True):
                meta["status"] = "empty"
                return None, meta
            meta["status"] = "section_only_no_30d_history"
            meta["note"] = "ak.bond_zh_cov 为可转债最新截面，无 30 日历史，无法按公式计算"
            return None, meta
        except Exception as exc:
            meta["error"] = str(exc)
            return None, meta
