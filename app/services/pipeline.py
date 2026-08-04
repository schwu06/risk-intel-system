"""双模型采集与分析流水线（分阶段 + staging 替换 + 降级入库）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import MODULE_CODES, PAGE_MODULES, get_settings, module_search_queries
from app.database.models import (
    ContentFingerprint,
    DailyRiskEntry,
    EntityRisk,
    NewsArticle,
    PipelineArtifact,
    ReportRun,
    SearchLog,
)
from app.services.chart_generator import extract_and_build_charts
from app.services.content_extractor import enrich_items_with_body
from app.services.data_source_service import get_module_authoritative_text
from app.services.dedup import content_fingerprint
from app.services.deepseek_analyzer import DeepSeekAnalyzer
from app.services.domain_rules import get_active_blacklist, get_active_whitelist
from app.services.entity_credit import refresh_entity_credit, resolve_entity
from app.services.llm_cache import get_cached_items, material_hash, set_cached_items
from app.services.mita_search import MitaSearchClient
from app.services.news_quality import (
    filter_publishable_rows,
    is_reference_only_item,
    is_substantive_news_item,
)
from app.services.recency import is_within_hours, parse_published_at
from app.services.rss_news import RssNewsCollector
from app.services.scrapers.official_portals import OfficialPortalScraper
from app.services.scrapers.tdnet_collector import TdnetCollector

logger = logging.getLogger(__name__)

RISK_LEVEL_ORDER = {"极高": 4, "高": 3, "中": 2, "低": 1}
NEWS_MODULES = set(PAGE_MODULES.get("daily_news", ("B", "C", "D")))
ENTITY_MODULES = set(PAGE_MODULES.get("entity_assessment", ("A",)))


class RiskPipeline:
    def __init__(
        self,
        db: Session,
        mita: Optional[MitaSearchClient] = None,
        deepseek: Optional[DeepSeekAnalyzer] = None,
        rss: Optional[RssNewsCollector] = None,
        job_id: Optional[str] = None,
        authority_text: Optional[str] = None,
        entity_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.mita = mita or MitaSearchClient()
        self.deepseek = deepseek or DeepSeekAnalyzer()
        self.settings = get_settings()
        self.window_hours = int(getattr(self.settings, "news_window_hours", 24) or 24)
        self.job_id = job_id
        # None = 实时读取数据源；非 None（含空串）= 任务启动时冻结的快照，运行中变更不影响本次
        self._authority_snapshot = authority_text
        self.entity_id = entity_id
        self.llm_top_k = int(getattr(self.settings, "pipeline_llm_top_k", 12) or 12)
        self.cache_hours = int(getattr(self.settings, "pipeline_llm_cache_hours", 168) or 168)
        self.mita_pause = float(getattr(self.settings, "pipeline_mita_query_pause_seconds", 0.8) or 0)
        if rss is not None:
            self.rss = rss
        else:
            self.rss = RssNewsCollector(
                retry_attempts=int(getattr(self.settings, "network_retry_attempts", 3) or 3),
                retry_backoff=float(getattr(self.settings, "network_retry_backoff_seconds", 1.5) or 1.5),
            )

    def run_module(self, module_code: str, report_date: date) -> int:
        module_code = module_code.upper()
        if module_code not in MODULE_CODES:
            raise ValueError(f"未知模块: {module_code}")

        run = self._ensure_run(report_date, module_code)
        # 先清掉历史误入库的检索入口占位，避免页面继续展示假新闻
        purged_early = self._purge_non_news_placeholders(report_date, module_code)
        previous_count = self._count_existing(report_date, module_code)
        funnel: dict[str, Any] = {
            "previous_count": previous_count,
            "rss_fetched": 0,
            "rss_feeds_ok": 0,
            "rss_feeds_fail": 0,
            "mita_fetched": 0,
            "portal_fetched": 0,
            "authority": 0,
            "after_dedup": 0,
            "sent_llm": 0,
            "llm_cached": 0,
            "structured": 0,
            "degraded": 0,
            "saved": 0,
            "kept_previous": False,
            "source_fail": [],
            "purged_placeholders": purged_early,
        }

        whitelist = get_active_whitelist(self.db, module_code)
        blacklist = get_active_blacklist(self.db)
        source_ok = 0
        source_fail = 0
        fail_notes: list[str] = []

        try:
            # ---- phase: collect（不清空旧数据）----
            run.phase = "collect"
            run.status = "running"
            run.notes = "采集中…"
            self.db.commit()

            batches: list[dict[str, Any]] = []
            if self._authority_snapshot is not None:
                authority_text = self._authority_snapshot
            else:
                authority_text = get_module_authoritative_text(self.db, module_code) or ""

            if authority_text.strip():
                batches.append(
                    {
                        "source": "authority",
                        "items": [],
                        "authority_text": authority_text[:50000],
                        "metadata": {"source": "authority"},
                    }
                )
                funnel["authority"] = 1
                source_ok += 1

            try:
                rss_batch = self._collect_rss(module_code, funnel)
                if rss_batch["items"]:
                    batches.append(rss_batch)
                    source_ok += 1
                elif rss_batch.get("error"):
                    source_fail += 1
                    fail_notes.append(f"RSS: {rss_batch['error']}")
                    funnel["source_fail"].append("rss")
            except Exception as exc:
                source_fail += 1
                fail_notes.append(f"RSS: {exc}")
                funnel["source_fail"].append("rss")
                logger.warning("模块 %s RSS 采集失败（已跳过）: %s", module_code, exc)

            if module_code == "C":
                try:
                    tdnet_batch = self._collect_tdnet(module_code, funnel)
                    if tdnet_batch["items"]:
                        batches.append(tdnet_batch)
                        source_ok += 1
                    elif tdnet_batch.get("error"):
                        source_fail += 1
                        fail_notes.append(f"TDnet: {tdnet_batch['error']}")
                        funnel["source_fail"].append("tdnet")
                except Exception as exc:
                    source_fail += 1
                    fail_notes.append(f"TDnet: {exc}")
                    funnel["source_fail"].append("tdnet")
                    logger.warning("模块 %s TDnet 采集失败（已跳过）: %s", module_code, exc)
                try:
                    # EDINET 仍作人工核对备注（需正式 API Key 后才能正文入库）
                    portal_note = self._collect_portals_as_notes(
                        module_code, report_date, funnel
                    )
                    if portal_note:
                        authority_text = (
                            f"{authority_text}\n\n=== EDINET 核对入口（非新闻正文）===\n"
                            f"{portal_note}"
                        ).strip()
                except Exception as exc:
                    logger.warning("EDINET 门户备注失败: %s", exc)

            mita_ok = 0
            mita_fail = 0
            entity_targets = self._entity_search_targets() if module_code in ENTITY_MODULES else None
            from app.services.api_keys import is_placeholder_key

            skip_mita = is_placeholder_key(getattr(self.settings, "mita_api_key", None))
            if skip_mita:
                funnel["source_fail"].append("mita_unconfigured")
                fail_notes.append("秘塔未配置，已跳过检索")
            else:
                queries = module_search_queries(
                    module_code,
                    report_date.isoformat(),
                    entity_targets=entity_targets,
                )
                for idx, qcfg in enumerate(queries):
                    try:
                        batch = self._collect_mita(
                            module_code=module_code,
                            query=qcfg["query"],
                            metadata=qcfg.get("metadata") or {},
                            whitelist=whitelist,
                            blacklist=blacklist,
                            funnel=funnel,
                        )
                        if batch["items"]:
                            batches.append(batch)
                            mita_ok += 1
                        else:
                            mita_fail += 1
                        if self.mita_pause > 0 and idx + 1 < len(queries):
                            time.sleep(self.mita_pause)
                    except Exception as exc:
                        mita_fail += 1
                        logger.warning("单条检索失败，已跳过: %s | %s", qcfg.get("query", ""), exc)
                    # 主体采集：连续失败时提前结束，走演示降级，避免长时间空转
                    if (
                        module_code in ENTITY_MODULES
                        and self.entity_id
                        and mita_ok == 0
                        and mita_fail >= 2
                    ):
                        fail_notes.append("秘塔连续失败，提前结束检索")
                        break
                if mita_ok:
                    source_ok += 1
                if mita_fail:
                    source_fail += 1
                    if not mita_ok:
                        fail_notes.append(f"秘塔: {mita_fail}/{mita_fail + mita_ok} 条查询失败")
                        funnel["source_fail"].append("mita")

            self._save_artifact(
                report_date, module_code, "collect", "all", {"batches": batches}, item_count=_batch_item_total(batches)
            )

            # 主体评估 + 外部能力未配置且无有效批次：直接演示降级，避免空转 LLM
            from app.services.api_keys import is_placeholder_key as _ph
            external_offline = _ph(getattr(self.settings, "mita_api_key", None)) and _ph(
                getattr(self.settings, "deepseek_api_key", None)
            )
            has_external_items = any(
                (b.get("items") or b.get("source") == "authority") for b in batches
            )
            if (
                module_code in ENTITY_MODULES
                and self.entity_id
                and external_offline
                and not has_external_items
            ):
                from app.services.entity_mock import refresh_entity_demo_for_collect

                saved = refresh_entity_demo_for_collect(
                    self.db, entity_id=self.entity_id, report_date=report_date
                )
                funnel["saved"] = saved
                funnel["demo_fallback"] = True
                run.entry_count = saved
                run.kept_previous = False
                run.status = "completed" if saved > 0 else "empty"
                run.phase = "done"
                run.notes = "外部检索未配置，已写入近24小时演示样本并刷新授信"
                run.finished_at = datetime.utcnow()
                self._set_funnel(run, funnel)
                self.db.commit()
                return saved

            # ---- phase: analyze ----
            run.phase = "analyze"
            run.notes = "分析中…"
            self._set_funnel(run, funnel)
            self.db.commit()

            pending_rows: list[dict[str, Any]] = []
            merge_llm = bool(getattr(self.settings, "pipeline_merge_llm", True))
            if merge_llm:
                pending_rows = self._analyze_merged(
                    batches,
                    module_code=module_code,
                    report_date=report_date,
                    authority_text=authority_text,
                    funnel=funnel,
                )
            else:
                for batch in batches:
                    rows = self._analyze_batch(
                        batch,
                        module_code=module_code,
                        report_date=report_date,
                        authority_text=authority_text,
                        funnel=funnel,
                    )
                    pending_rows.extend(rows)

            pending_rows = self._dedupe_structured(pending_rows, module_code, funnel)
            pending_rows = filter_publishable_rows(pending_rows)
            funnel["after_quality_filter"] = len(pending_rows)
            self._save_artifact(
                report_date,
                module_code,
                "analyze",
                "structured",
                {"rows": pending_rows, "funnel": funnel},
                item_count=len(pending_rows),
            )

            # ---- phase: publish（仅成功产出时替换；否则保留旧数据）----
            run.phase = "publish"
            run.notes = "发布中…"
            self._set_funnel(run, funnel)
            self.db.commit()

            # 清理历史误入库的「披露检索」占位（再扫一次，防分析阶段写入）
            purged = self._purge_non_news_placeholders(report_date, module_code)
            if purged:
                funnel["purged_placeholders"] = int(funnel.get("purged_placeholders") or 0) + purged
                previous_count = self._count_existing(report_date, module_code)

            if pending_rows:
                self._replace_module_entries(report_date, module_code)
                saved = self._save_structured_entries(
                    pending_rows,
                    module_code=module_code,
                    report_date=report_date,
                    metadata={},
                    search_log_id=None,
                    raw_context=json.dumps(
                        {"funnel": funnel, "count": len(pending_rows)}, ensure_ascii=False
                    ),
                    source_items=None,
                    register_fingerprints=True,
                )
                funnel["saved"] = saved
                run.entry_count = saved
                run.kept_previous = False
                run.status = "completed" if saved > 0 else "empty"
                note = f"近{self.window_hours}小时重要资讯采集"
                if source_fail:
                    note += f"（部分源失败 {source_fail}）"
                if funnel.get("degraded"):
                    note += f"（降级入库 {funnel['degraded']}）"
                if funnel.get("llm_cached"):
                    note += f"（缓存命中 {funnel['llm_cached']}）"
                run.notes = note
            else:
                # 主体评估：无真实产出时写入演示数据，保证页面可刷新
                if module_code in ENTITY_MODULES and self.entity_id:
                    from app.services.entity_mock import refresh_entity_demo_for_collect

                    saved = refresh_entity_demo_for_collect(
                        self.db, entity_id=self.entity_id, report_date=report_date
                    )
                    funnel["saved"] = saved
                    funnel["demo_fallback"] = True
                    run.entry_count = saved
                    run.kept_previous = False
                    run.status = "completed" if saved > 0 else "empty"
                    run.notes = (
                        f"近{self.window_hours}小时采集无新增源数据，已写入演示样本并刷新授信"
                        if saved
                        else "今日无动态"
                    )
                else:
                    # 无新产出：保留旧数据，避免页面变空白
                    funnel["kept_previous"] = True
                    run.kept_previous = True
                    run.entry_count = previous_count
                    if source_fail and not source_ok:
                        run.status = "failed"
                        run.notes = (
                            "请求失败，已保留上次结果｜"
                            + "；".join(fail_notes[:3])
                        )
                    elif previous_count > 0:
                        run.status = "completed"
                        run.notes = "今日无新增动态，已保留上次结果"
                    else:
                        run.status = "empty"
                        run.notes = "今日无动态"

            run.phase = "done"
            run.finished_at = datetime.utcnow()
            self._set_funnel(run, funnel)
            self.db.commit()
            return int(run.entry_count or 0)

        except Exception as exc:
            logger.exception("模块 %s 流水线失败", module_code)
            funnel["kept_previous"] = True
            run.kept_previous = True
            run.entry_count = previous_count
            run.status = "failed"
            run.phase = "done"
            run.notes = f"请求失败，已保留上次结果｜{exc}"
            run.finished_at = datetime.utcnow()
            self._set_funnel(run, funnel)
            self.db.commit()
            raise

    def run_all_modules(self, report_date: date) -> dict[str, int]:
        results: dict[str, int] = {}
        for code in MODULE_CODES:
            try:
                results[code] = self.run_module(code, report_date)
            except Exception as exc:
                logger.error("模块 %s 运行失败: %s", code, exc)
                results[code] = -1
        return results

    # ------------------------------------------------------------------
    # collect helpers
    # ------------------------------------------------------------------

    def _collect_rss(self, module_code: str, funnel: dict[str, Any]) -> dict[str, Any]:
        detailed = self.rss.collect_detailed(
            module_code,
            hours=self.window_hours,
            max_items=36,
        )
        funnel["rss_fetched"] = len(detailed.items)
        funnel["rss_feeds_ok"] = detailed.fetch_ok
        funnel["rss_feeds_fail"] = detailed.fetch_errors
        items = [
            {
                "title": h.title,
                "url": h.url,
                "snippet": h.snippet,
                "published_at": h.published_at,
                "source_domain": h.source_domain,
                "feed": h.feed_label,
                "fingerprint": h.fingerprint,
            }
            for h in detailed.items
        ]
        if items and self.settings.news_fetch_body:
            items = enrich_items_with_body(
                items,
                max_items=int(self.settings.news_max_body_items or 8),
            )
        log = SearchLog(
            module_code=module_code,
            query_text=f"[RSS近{self.window_hours}小时]",
            status="completed" if items else "empty",
            result_count=len(items),
            raw_response=json.dumps(
                {"items": items, "feed_health": [h.__dict__ for h in detailed.feed_health]},
                ensure_ascii=False,
            )[:50000],
        )
        self.db.add(log)
        self.db.flush()
        return {
            "source": "rss",
            "items": items,
            "search_log_id": log.id,
            "metadata": {"source": "rss"},
            "error": None if detailed.fetch_ok else "全部 feed 失败",
        }

    def _collect_tdnet(self, module_code: str, funnel: dict[str, Any]) -> dict[str, Any]:
        """采集监控企业 TDnet 适时应披露，作为可分析/可入库资讯源。"""
        collector = TdnetCollector()
        hits = collector.collect(hours=self.window_hours, max_items=36)
        funnel["tdnet_fetched"] = len(hits)
        items = [
            {
                "title": h.title,
                "url": h.url,
                "snippet": h.snippet,
                "published_at": h.published_at,
                "source_domain": "release.tdnet.info",
                "company": h.company_name,
                "company_code": h.company_code,
                "fingerprint": content_fingerprint(
                    module_code=module_code,
                    title=h.title,
                    url=h.url,
                    published_at=h.published_at,
                ),
            }
            for h in hits
        ]
        if items and self.settings.news_fetch_body:
            items = enrich_items_with_body(
                items,
                max_items=min(8, int(self.settings.news_max_body_items or 8)),
            )
        log = SearchLog(
            module_code=module_code,
            query_text=f"[TDnet适时应披露近{self.window_hours}小时]",
            status="completed" if items else "empty",
            result_count=len(items),
            raw_response=json.dumps({"items": items}, ensure_ascii=False)[:50000],
        )
        self.db.add(log)
        self.db.flush()
        return {
            "source": "tdnet",
            "items": items,
            "search_log_id": log.id,
            "metadata": {"source": "tdnet", "category": "適時開示"},
            "error": None if items else "近窗内无监控企业披露",
        }

    def _collect_portals_as_notes(
        self, module_code: str, report_date: date, funnel: dict[str, Any]
    ) -> str:
        """生成 EDINET 核对备注；TDnet 正文已走独立采集通道。"""
        scraper = OfficialPortalScraper()
        from app.config import MODULE_C_TARGETS

        seen: set[str] = set()
        companies: list[str] = []
        for name in MODULE_C_TARGETS:
            if not any("\u4e00" <= ch <= "\u9fff" for ch in name):
                continue
            if name in seen:
                continue
            seen.add(name)
            companies.append(name)
            if len(companies) >= 5:
                break

        hits = []
        for company in companies:
            # 仅保留 EDINET 入口；TDnet 已正文采集
            for h in scraper.collect_reference_hits(company, report_date):
                if h.source == "EDINET":
                    hits.append(h)
        funnel["portal_fetched"] = len(hits)
        funnel["portal_reference_only"] = True

        lines = [
            f"- [{h.source}] {h.title}: {h.url} （{h.snippet}）"
            for h in hits
        ]
        note = "\n".join(lines)
        log = SearchLog(
            module_code=module_code,
            query_text="[EDINET核对入口-不入库]",
            status="completed",
            result_count=0,
            raw_response=note[:20000],
        )
        self.db.add(log)
        self.db.flush()
        self._save_artifact(
            report_date,
            module_code,
            "collect",
            "official_portal_notes",
            {"notes": note, "hit_count": len(hits)},
            item_count=0,
        )
        return note

    def _collect_mita(
        self,
        *,
        module_code: str,
        query: str,
        metadata: dict[str, Any],
        whitelist: list[str],
        blacklist: list[str],
        funnel: dict[str, Any],
    ) -> dict[str, Any]:
        log = SearchLog(
            module_code=module_code,
            query_text=query,
            domains_whitelist=json.dumps(whitelist, ensure_ascii=False),
            domains_blacklist=json.dumps(blacklist, ensure_ascii=False),
            status="running",
        )
        self.db.add(log)
        self.db.flush()

        response = self.mita.search(
            query=query,
            whitelist_domains=whitelist or None,
            blacklist_domains=blacklist or None,
            max_results=12,
        )
        recent_items = [
            i
            for i in response.items
            if is_within_hours(i.published_at, self.window_hours, allow_unknown=True)
        ]
        payload = [
            {
                "title": i.title,
                "url": i.url,
                "snippet": i.snippet,
                "published_at": i.published_at,
                "source_domain": i.source_domain,
                "fingerprint": content_fingerprint(
                    module_code=module_code,
                    title=i.title,
                    url=i.url,
                    published_at=i.published_at,
                ),
            }
            for i in recent_items
        ]
        funnel["mita_fetched"] = int(funnel.get("mita_fetched") or 0) + len(payload)
        if payload and self.settings.news_fetch_body:
            payload = enrich_items_with_body(
                payload,
                max_items=min(6, int(self.settings.news_max_body_items or 8)),
            )
        log.result_count = len(payload)
        log.raw_response = json.dumps(payload, ensure_ascii=False)[:50000]
        log.status = "completed" if payload else "empty"
        self.db.commit()
        return {
            "source": "mita",
            "items": payload,
            "search_log_id": log.id,
            "metadata": {"source": "mita", **metadata},
            "query": query,
        }

    # ------------------------------------------------------------------
    # analyze helpers
    # ------------------------------------------------------------------

    def _analyze_merged(
        self,
        batches: list[dict[str, Any]],
        *,
        module_code: str,
        report_date: date,
        authority_text: str,
        funnel: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """同一模块多源合并为少量 LLM 调用，显著缩短耗时。"""
        rows: list[dict[str, Any]] = []
        auth_batches = [b for b in batches if b.get("source") == "authority"]
        other_batches = [b for b in batches if b.get("source") != "authority"]

        for batch in auth_batches:
            rows.extend(
                self._analyze_batch(
                    batch,
                    module_code=module_code,
                    report_date=report_date,
                    authority_text=authority_text,
                    funnel=funnel,
                )
            )

        merged_items: list[dict[str, Any]] = []
        for batch in other_batches:
            for it in batch.get("items") or []:
                if not is_substantive_news_item(it):
                    continue
                item = dict(it)
                item["_batch_source"] = batch.get("source")
                item["_batch_meta"] = batch.get("metadata") or {}
                merged_items.append(item)

        if not merged_items and not auth_batches:
            return []
        if not merged_items:
            return rows

        # 去重后取 Top-K
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for it in merged_items:
            fp = it.get("fingerprint") or content_fingerprint(
                module_code=module_code,
                title=it.get("title"),
                url=it.get("url"),
                published_at=it.get("published_at"),
            )
            if fp in seen:
                continue
            seen.add(fp)
            it["fingerprint"] = fp
            unique.append(it)
        ranked = self._prefilter_items(unique, self.llm_top_k)
        funnel["sent_llm"] = int(funnel.get("sent_llm") or 0) + len(ranked)

        web_text = json.dumps(ranked, ensure_ascii=False, indent=2)
        combined = web_text
        if authority_text:
            combined = (
                f"=== 权威数据源（优先参考）===\n{authority_text[:20000]}\n\n"
                f"=== RSS/秘塔合并候选（近{self.window_hours}小时）===\n{web_text}"
            )
        metadata = {"source": "merged"}
        try:
            structured = self._llm_analyze(
                combined,
                module_code=module_code,
                report_date=report_date,
                source="merged",
                authority_first=bool(authority_text),
                funnel=funnel,
                context={
                    "report_date": report_date.isoformat(),
                    "source": "merged",
                    "window_hours": self.window_hours,
                },
            )
            if structured:
                rows.extend(
                    filter_publishable_rows(
                        self._tag_rows(
                            structured,
                            metadata,
                            ranked,
                            module_code=module_code,
                            degraded=False,
                        )
                    )
                )
            else:
                logger.info("合并材料 LLM 返回空，视为无资讯")
        except Exception as exc:
            logger.warning("合并 LLM 失败，降级实质条目: %s", exc)
            degraded = self._degrade_items(ranked, metadata)
            funnel["degraded"] = int(funnel.get("degraded") or 0) + len(degraded)
            funnel["source_fail"].append("merged_llm")
            rows.extend(degraded)
        return rows

    def _analyze_batch(
        self,
        batch: dict[str, Any],
        *,
        module_code: str,
        report_date: date,
        authority_text: str,
        funnel: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source = batch.get("source") or "unknown"
        metadata = dict(batch.get("metadata") or {})
        items = list(batch.get("items") or [])

        if source == "authority":
            text = batch.get("authority_text") or authority_text
            if not text:
                return []
            try:
                structured = self._llm_analyze(
                    text,
                    module_code=module_code,
                    report_date=report_date,
                    source=source,
                    authority_first=True,
                    funnel=funnel,
                    context={"report_date": report_date.isoformat(), "source": source},
                )
                return self._tag_rows(
                    structured, metadata, items, module_code=module_code, degraded=False
                )
            except Exception as exc:
                logger.warning("权威源 LLM 失败，跳过: %s", exc)
                funnel["source_fail"].append("authority_llm")
                return []

        if not items:
            return []

        # 去掉检索入口占位，避免污染 LLM / 降级入库
        real_items = [it for it in items if is_substantive_news_item(it)]
        if not real_items:
            logger.info("来源 %s 无实质资讯条目，跳过分析", source)
            return []

        # 粗筛 Top-K，降低 LLM 超时概率
        ranked = self._prefilter_items(real_items, self.llm_top_k)
        funnel["sent_llm"] = int(funnel.get("sent_llm") or 0) + len(ranked)
        web_text = json.dumps(ranked, ensure_ascii=False, indent=2)
        combined = web_text
        if authority_text:
            combined = (
                f"=== 权威数据源（优先参考）===\n{authority_text[:20000]}\n\n"
                f"=== {source} 近{self.window_hours}小时 ===\n{web_text}"
            )

        try:
            structured = self._llm_analyze(
                combined,
                module_code=module_code,
                report_date=report_date,
                source=source,
                authority_first=bool(authority_text),
                funnel=funnel,
                context={
                    "report_date": report_date.isoformat(),
                    "source": source,
                    "window_hours": self.window_hours,
                    **{k: v for k, v in metadata.items() if k != "source"},
                },
            )
            if structured:
                rows = self._tag_rows(
                    structured, metadata, ranked, module_code=module_code, degraded=False
                )
                return filter_publishable_rows(rows)
            # LLM 明确返回空 = 材料中无合格新闻，不降级硬塞
            logger.info("来源 %s LLM 返回空结果，视为无资讯", source)
            return []
        except Exception as exc:
            # rss / mita / tdnet 在异常时降级；且只降级有实质内容的条目
            if source not in ("rss", "mita", "tdnet"):
                logger.warning("LLM 分析失败（%s），不降级: %s", source, exc)
                funnel["source_fail"].append(f"{source}_llm")
                return []
            logger.warning("LLM 分析失败，降级入库实质原始条目 (%s): %s", source, exc)
            degraded = self._degrade_items(ranked, metadata)
            funnel["degraded"] = int(funnel.get("degraded") or 0) + len(degraded)
            funnel["source_fail"].append(f"{source}_llm")
            return degraded

    def _llm_analyze(
        self,
        text: str,
        *,
        module_code: str,
        report_date: date,
        source: str,
        authority_first: bool,
        funnel: dict[str, Any],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        key = material_hash(text, module_code=module_code, source=source)
        cached = get_cached_items(self.db, material_key=key, max_age_hours=self.cache_hours)
        if cached is not None:
            funnel["llm_cached"] = int(funnel.get("llm_cached") or 0) + 1
            funnel["structured"] = int(funnel.get("structured") or 0) + len(cached)
            return cached
        structured = self.deepseek.analyze_raw(
            text,
            module_code=module_code,
            context=context,
            authority_first=authority_first,
        )
        set_cached_items(
            self.db,
            material_key=key,
            module_code=module_code,
            source=source,
            items=structured,
        )
        funnel["structured"] = int(funnel.get("structured") or 0) + len(structured)
        return structured

    def _prefilter_items(self, items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        def score(it: dict[str, Any]) -> tuple:
            body = (it.get("body") or it.get("snippet") or "")
            has_body = 1 if len(body) > 80 else 0
            has_time = 1 if it.get("published_at") else 0
            return (has_body, has_time, len(body), len(it.get("title") or ""))

        ranked = sorted(items, key=score, reverse=True)
        return ranked[: max(1, top_k)]

    def _degrade_items(
        self, items: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rows = []
        for it in items:
            if not is_substantive_news_item(it):
                continue
            title = (it.get("title") or "未命名条目").strip()
            snippet = (it.get("snippet") or it.get("body") or "").strip()
            if len(snippet) < 12:
                continue
            rows.append(
                {
                    "标题": title,
                    "关联企业": metadata.get("company") or metadata.get("target") or "",
                    "风险类别": metadata.get("category") or metadata.get("topic") or "资讯快讯",
                    "风险等级": "中",
                    "核心摘要": snippet[:800],
                    "影响分析": "结构化分析暂不可用，已按原始摘要入库，请人工复核。",
                    "来源链接": it.get("url") or "",
                    "发布时间": it.get("published_at") or "",
                    "_degraded": True,
                    "_fingerprint": it.get("fingerprint"),
                    "_metadata": metadata,
                    "_source_item": it,
                }
            )
            if len(rows) >= self.llm_top_k:
                break
        return filter_publishable_rows(rows)

    def _purge_non_news_placeholders(self, report_date: date, module_code: str) -> int:
        """删除已入库的披露检索等非新闻占位。"""
        removed = 0
        if module_code in NEWS_MODULES:
            rows = (
                self.db.query(NewsArticle)
                .filter(
                    NewsArticle.report_date == report_date,
                    NewsArticle.module_code == module_code,
                )
                .all()
            )
            for row in rows:
                probe = {
                    "title": row.title,
                    "snippet": row.summary,
                    "url": row.source_url,
                    "影响分析": row.impact_analysis,
                    "核心摘要": row.summary,
                }
                impact = row.impact_analysis or ""
                if is_reference_only_item(probe) or (
                    "DeepSeek 暂不可用" in impact and is_reference_only_item(probe)
                ) or (
                    "披露检索" in (row.title or "")
                    or "法定披露" in (row.title or "")
                ):
                    # 同步删 legacy
                    if row.legacy_entry_id:
                        self.db.query(DailyRiskEntry).filter(
                            DailyRiskEntry.id == row.legacy_entry_id
                        ).delete(synchronize_session=False)
                    self.db.delete(row)
                    removed += 1
        legacy = (
            self.db.query(DailyRiskEntry)
            .filter(
                DailyRiskEntry.report_date == report_date,
                DailyRiskEntry.module_code == module_code,
            )
            .all()
        )
        for row in legacy:
            if (
                "披露检索" in (row.title or "")
                or "法定披露" in (row.title or "")
                or "DeepSeek 暂不可用" in (row.impact_analysis or "")
            ):
                # 若 news 已删，这里补漏；若仍有 news 指向则上面已处理
                still = (
                    self.db.query(NewsArticle)
                    .filter(NewsArticle.legacy_entry_id == row.id)
                    .first()
                )
                if not still:
                    self.db.delete(row)
                    removed += 1
        if removed:
            self.db.commit()
            logger.info("模块 %s 已清理非新闻占位 %d 条", module_code, removed)
        return removed

    def _tag_rows(
        self,
        structured: list[dict[str, Any]],
        metadata: dict[str, Any],
        source_items: list[dict[str, Any]],
        *,
        module_code: str,
        degraded: bool,
    ) -> list[dict[str, Any]]:
        out = []
        for row in structured:
            enriched = dict(row)
            enriched["_metadata"] = metadata
            enriched["_source_items"] = source_items
            enriched["_degraded"] = degraded
            src_url = (row.get("来源链接") or "").strip()
            if src_url and source_items:
                for it in source_items:
                    if (it.get("url") or "").strip() == src_url:
                        enriched["_fingerprint"] = it.get("fingerprint")
                        enriched["_source_item"] = it
                        break
            if not enriched.get("_fingerprint"):
                enriched["_fingerprint"] = content_fingerprint(
                    module_code=module_code,
                    title=row.get("标题"),
                    url=src_url or None,
                    published_at=row.get("发布时间"),
                )
            out.append(enriched)
        return out

    def _dedupe_structured(
        self, rows: list[dict[str, Any]], module_code: str, funnel: dict[str, Any]
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for row in rows:
            fp = row.get("_fingerprint") or content_fingerprint(
                module_code=module_code,
                title=row.get("标题"),
                url=row.get("来源链接"),
                published_at=row.get("发布时间"),
            )
            title_key = (row.get("标题") or "").strip().lower()
            keys = {fp, f"title:{title_key}" if title_key else ""}
            if any(k and k in seen for k in keys):
                continue
            for k in keys:
                if k:
                    seen.add(k)
            row["_fingerprint"] = fp
            unique.append(row)
        funnel["after_dedup"] = len(unique)
        return unique

    # ------------------------------------------------------------------
    # publish / storage
    # ------------------------------------------------------------------

    def _ensure_run(self, report_date: date, module_code: str) -> ReportRun:
        run = (
            self.db.query(ReportRun)
            .filter(ReportRun.report_date == report_date, ReportRun.module_code == module_code)
            .first()
        )
        if not run:
            run = ReportRun(report_date=report_date, module_code=module_code, status="running")
            self.db.add(run)
        else:
            run.status = "running"
        run.started_at = datetime.utcnow()
        run.finished_at = None
        run.entry_count = 0
        run.kept_previous = False
        run.phase = "collect"
        run.job_id = self.job_id
        run.notes = None
        self.db.commit()
        return run

    def _entity_search_targets(self) -> list[str] | None:
        """主体评估限定检索目标；未指定 entity_id 则搜全部默认主体。"""
        if not self.entity_id:
            return None
        from app.database.models import TargetEntity

        ent = self.db.query(TargetEntity).filter(TargetEntity.id == self.entity_id).first()
        if not ent:
            return None
        names = [ent.display_name or ent.name, ent.name]
        return [n for n in dict.fromkeys(names) if n]

    def _count_existing(self, report_date: date, module_code: str) -> int:
        if module_code in NEWS_MODULES:
            return (
                self.db.query(NewsArticle)
                .filter(NewsArticle.report_date == report_date, NewsArticle.module_code == module_code)
                .count()
            )
        if module_code in ENTITY_MODULES:
            q = self.db.query(EntityRisk).filter(EntityRisk.report_date == report_date)
            if self.entity_id:
                q = q.filter(EntityRisk.entity_id == self.entity_id)
            return q.count()
        return (
            self.db.query(DailyRiskEntry)
            .filter(DailyRiskEntry.report_date == report_date, DailyRiskEntry.module_code == module_code)
            .count()
        )

    def _replace_module_entries(self, report_date: date, module_code: str) -> None:
        """仅在确定有新结果要发布时调用。"""
        if module_code in NEWS_MODULES:
            self.db.query(NewsArticle).filter(
                NewsArticle.report_date == report_date,
                NewsArticle.module_code == module_code,
            ).delete(synchronize_session=False)

        if module_code in ENTITY_MODULES:
            from app.database.models import CreditUpdate

            q = self.db.query(EntityRisk.id).filter(EntityRisk.report_date == report_date)
            if self.entity_id:
                q = q.filter(EntityRisk.entity_id == self.entity_id)
            risk_ids = [r[0] for r in q.all()]
            if risk_ids:
                self.db.query(CreditUpdate).filter(
                    CreditUpdate.trigger_risk_id.in_(risk_ids)
                ).update({CreditUpdate.trigger_risk_id: None}, synchronize_session=False)
                self.db.query(EntityRisk).filter(EntityRisk.id.in_(risk_ids)).delete(
                    synchronize_session=False
                )

        # 主体限定采集时不整模块清空 DailyRiskEntry，避免误删其他主体对应旧条目
        if not (module_code in ENTITY_MODULES and self.entity_id):
            self.db.query(DailyRiskEntry).filter(
                DailyRiskEntry.report_date == report_date,
                DailyRiskEntry.module_code == module_code,
            ).delete(synchronize_session=False)
        self.db.commit()

    def _save_artifact(
        self,
        report_date: date,
        module_code: str,
        phase: str,
        source: str,
        payload: dict[str, Any],
        *,
        item_count: int,
    ) -> None:
        try:
            self.db.add(
                PipelineArtifact(
                    job_id=self.job_id,
                    report_date=report_date,
                    module_code=module_code,
                    phase=phase,
                    source=source,
                    payload_json=json.dumps(payload, ensure_ascii=False)[:500000],
                    item_count=item_count,
                )
            )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            logger.warning("中间落盘失败（不影响主流程）: %s", exc)

    def _set_funnel(self, run: ReportRun, funnel: dict[str, Any]) -> None:
        run.funnel_json = json.dumps(funnel, ensure_ascii=False)

    def _resolve_published_at(
        self,
        row: dict[str, Any],
        source_items: Optional[list[dict[str, Any]]],
    ) -> Optional[datetime]:
        candidates = [
            row.get("发布时间"),
            row.get("published_at"),
        ]
        src_item = row.get("_source_item")
        if isinstance(src_item, dict):
            candidates.append(src_item.get("published_at"))
        src_url = (row.get("来源链接") or "").strip()
        items = source_items or row.get("_source_items")
        if items and src_url:
            for item in items:
                if (item.get("url") or "").strip() == src_url:
                    candidates.append(item.get("published_at"))
                    break
        for cand in candidates:
            dt = parse_published_at(cand)
            if dt:
                return dt.replace(tzinfo=None)
        return None

    def _save_structured_entries(
        self,
        structured: list[dict[str, Any]],
        *,
        module_code: str,
        report_date: date,
        metadata: dict[str, Any],
        search_log_id: Optional[int],
        raw_context: str,
        source_items: Optional[list[dict[str, Any]]] = None,
        register_fingerprints: bool = False,
    ) -> int:
        chart_specs, _ = extract_and_build_charts(raw_context)
        chart_json = json.dumps(chart_specs, ensure_ascii=False) if chart_specs else None

        count = 0
        touched_entities = []
        seen_titles: set[str] = set()

        for row in structured:
            title = (row.get("标题") or "未命名条目").strip()
            title_key = title.lower()
            if title_key in seen_titles:
                continue

            published_at = self._resolve_published_at(row, source_items)
            if published_at and not is_within_hours(
                published_at, self.window_hours, allow_unknown=False
            ):
                # 降级条目若带时间且过期则丢弃；无时间保留
                if not row.get("_degraded"):
                    continue

            seen_titles.add(title_key)
            meta = dict(row.get("_metadata") or metadata or {})
            enriched = {
                k: v
                for k, v in row.items()
                if not str(k).startswith("_")
            }
            if chart_json and count == 0:
                enriched["_chart_specs"] = chart_json
            if published_at:
                enriched["发布时间"] = published_at.isoformat()
            if row.get("_degraded"):
                enriched["_degraded"] = True
            target_hint = (
                meta.get("target")
                or meta.get("company")
                or meta.get("topic")
            )
            entry = DailyRiskEntry(
                report_date=report_date,
                module_code=module_code,
                country_or_region=meta.get("region") or meta.get("country"),
                target_entity=target_hint,
                title=title,
                related_company=row.get("关联企业"),
                risk_category=row.get("风险类别"),
                risk_level=row.get("风险等级") or "中",
                summary=row.get("核心摘要") or "",
                impact_analysis=row.get("影响分析"),
                source_url=row.get("来源链接"),
                source_title=None,
                pillar_or_topic=meta.get("category") or meta.get("topic"),
                structured_json=json.dumps(enriched, ensure_ascii=False),
                search_log_id=search_log_id or row.get("_search_log_id"),
                published_at=published_at,
            )
            self.db.add(entry)
            self.db.flush()

            if register_fingerprints:
                fp = row.get("_fingerprint") or content_fingerprint(
                    module_code=module_code,
                    title=title,
                    url=row.get("来源链接"),
                    published_at=row.get("发布时间"),
                )
                exists = (
                    self.db.query(ContentFingerprint)
                    .filter(
                        ContentFingerprint.module_code == module_code,
                        ContentFingerprint.fingerprint == fp,
                    )
                    .first()
                )
                if not exists:
                    self.db.add(
                        ContentFingerprint(
                            module_code=module_code,
                            fingerprint=fp,
                            title=title[:512],
                            source_url=(row.get("来源链接") or "")[:1024] or None,
                            report_date=report_date,
                        )
                    )

            if module_code in NEWS_MODULES:
                self.db.add(
                    NewsArticle(
                        report_date=entry.report_date,
                        module_code=entry.module_code,
                        category_tag=entry.pillar_or_topic or entry.risk_category,
                        country_or_region=entry.country_or_region,
                        target_entity=entry.target_entity,
                        title=entry.title,
                        related_company=entry.related_company,
                        risk_category=entry.risk_category,
                        risk_level=entry.risk_level,
                        summary=entry.summary,
                        impact_analysis=entry.impact_analysis,
                        source_url=entry.source_url,
                        source_title=entry.source_title,
                        structured_json=entry.structured_json,
                        published_at=published_at,
                        legacy_entry_id=entry.id,
                    )
                )
            elif module_code in ENTITY_MODULES:
                ent = None
                if self.entity_id:
                    from app.database.models import TargetEntity

                    ent = (
                        self.db.query(TargetEntity)
                        .filter(TargetEntity.id == self.entity_id)
                        .first()
                    )
                if not ent:
                    ent = resolve_entity(
                        self.db,
                        name_hint=target_hint,
                        related_company=row.get("关联企业"),
                        create_if_missing=True,
                    )
                if ent:
                    risk = EntityRisk(
                        entity_id=ent.id,
                        report_date=report_date,
                        title=entry.title,
                        risk_category=entry.risk_category,
                        risk_level=entry.risk_level,
                        summary=entry.summary,
                        impact_analysis=entry.impact_analysis,
                        source_url=entry.source_url,
                        related_company=entry.related_company,
                        structured_json=entry.structured_json,
                        legacy_entry_id=entry.id,
                    )
                    self.db.add(risk)
                    self.db.flush()
                    touched_entities.append((ent, risk))

            count += 1

        for ent, risk in touched_entities:
            refresh_entity_credit(self.db, ent, trigger_risk=risk)

        self.db.commit()
        return count

    def ingest_manual_entries(
        self,
        module_code: str,
        report_date: date,
        entries: list[dict[str, str]],
    ) -> int:
        """手工录入或爬虫结果直接结构化入库。"""
        code = module_code.upper()
        count = 0
        touched = []
        for row in entries:
            published_at = parse_published_at(row.get("发布时间") or row.get("published_at"))
            if published_at:
                published_at = published_at.replace(tzinfo=None)
            entry = DailyRiskEntry(
                report_date=report_date,
                module_code=code,
                title=row.get("标题", ""),
                related_company=row.get("关联企业"),
                risk_category=row.get("风险类别"),
                risk_level=row.get("风险等级") or "中",
                summary=row.get("核心摘要") or "",
                impact_analysis=row.get("影响分析"),
                source_url=row.get("来源链接"),
                structured_json=json.dumps(row, ensure_ascii=False),
                published_at=published_at,
            )
            self.db.add(entry)
            self.db.flush()

            if code in NEWS_MODULES:
                self.db.add(
                    NewsArticle(
                        report_date=report_date,
                        module_code=code,
                        category_tag=row.get("风险类别"),
                        title=entry.title,
                        related_company=entry.related_company,
                        risk_category=entry.risk_category,
                        risk_level=entry.risk_level,
                        summary=entry.summary,
                        impact_analysis=entry.impact_analysis,
                        source_url=entry.source_url,
                        structured_json=entry.structured_json,
                        published_at=published_at,
                        legacy_entry_id=entry.id,
                    )
                )
            elif code in ENTITY_MODULES:
                ent = resolve_entity(
                    self.db,
                    name_hint=row.get("关联企业"),
                    related_company=row.get("关联企业"),
                    create_if_missing=True,
                )
                if ent:
                    risk = EntityRisk(
                        entity_id=ent.id,
                        report_date=report_date,
                        title=entry.title,
                        risk_category=entry.risk_category,
                        risk_level=entry.risk_level,
                        summary=entry.summary,
                        impact_analysis=entry.impact_analysis,
                        source_url=entry.source_url,
                        related_company=entry.related_company,
                        structured_json=entry.structured_json,
                        legacy_entry_id=entry.id,
                    )
                    self.db.add(risk)
                    self.db.flush()
                    touched.append((ent, risk))
            count += 1

        for ent, risk in touched:
            refresh_entity_credit(self.db, ent, trigger_risk=risk)
        self.db.commit()
        return count


def _batch_item_total(batches: list[dict[str, Any]]) -> int:
    total = 0
    for b in batches:
        if b.get("source") == "authority":
            total += 1
        else:
            total += len(b.get("items") or [])
    return total
