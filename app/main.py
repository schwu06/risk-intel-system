"""FastAPI 应用入口与 Web 仪表盘。"""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.routes import router as api_router
from app.config import (
    CREDIT_LEVELS,
    MODULE_CODES,
    NEWS_WINDOW_HOURS_24,
    NEWS_WINDOW_HOURS_7X24,
    PAGE_META,
    get_settings,
    modules_for_page,
)
from app.database.models import (
    EntityRisk, IndustryGroundedReportRun, NewsArticle,
    ReportRun, SearchLog, TargetEntity,
)
from app.database.session import get_db, init_db
from app.database.industry_db import (
    find_report_sector,
    industry_session,
    init_all_industry_databases,
    list_all_sector_reports,
)
from app.industry_sectors import INDUSTRY_SECTORS, ensure_registry_file, refresh_sectors, require_sector_key
from app.services.api_keys import is_placeholder_key
from app.services.data_bridge import migrate_legacy_data
from app.services.data_source_service import list_industry_sources
from app.services.display_zh import (
    build_display_cards,
    format_news_overview,
    has_publishable_content_detail,
    translate_fields_to_chinese,
)
from app.services.domain_rules import seed_default_domains
from app.services.entity_catalog import (
    apply_bilingual_display_name,
    configured_entity_catalog,
    group_monitored_entities,
    split_bilingual_display_name,
)
from app.services.entity_briefing import (
    build_financials_panel,
    build_latest_news,
    news_lookback_days,
    news_lookback_start,
)
from app.services.entity_relevance import classify_risk_tab, is_monitored_public_event
from app.services.risk_reasoning import build_risk_reasoning
from app.services.news_quality import is_substantive_news_item
from app.services.industry_analysis import IndustryAnalysisService, source_list_html
from app.services.industry_migration import migrate_main_db_industry_reports
from app.services.news_section_router import item_in_module_scope
from app.services.citation_rendering import (
    CitationPresentationError, build_citation_context, render_report_html,
)
from app.services.evidence_packet import build_evidence_packet
from app.services.pipeline import RISK_LEVEL_ORDER
from app.services.scheduler import shutdown_scheduler, start_scheduler
from app.services.social_source import resolve_social_source
from app.timeutil import format_tokyo, tokyo_day_tabs, tokyo_today

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

settings = get_settings()
app = FastAPI(title=settings.app_name, version="2.1.0")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["tokyo_time"] = format_tokyo
templates.env.filters["bilingual_name"] = split_bilingual_display_name
app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
def on_startup():
    init_db()
    from app.database.session import SessionLocal
    import logging

    log = logging.getLogger("uvicorn.error")
    cfg = get_settings()
    if is_placeholder_key(cfg.mita_api_key):
        log.warning("MITA_API_KEY 未配置或为占位符，流水线检索将不可用")
    if is_placeholder_key(cfg.deepseek_api_key):
        log.warning("DEEPSEEK_API_KEY 未配置或为占位符，结构化分析将不可用")
    if is_placeholder_key(cfg.gemini_api_key):
        log.warning("GEMINI_API_KEY 未配置或为占位符，主体评估结构化分析将不可用")
    if "mita.ai" in cfg.mita_api_base_url:
        log.warning("MITA_API_BASE_URL 应设为 https://metaso.cn/api/v1，请更新 .env 后重启服务")

    try:
        db = SessionLocal()
        try:
            seed_default_domains(db)
            stats = migrate_legacy_data(db)
            log.info("切片2数据就绪: %s", stats)
            init_all_industry_databases()
            ensure_registry_file()
            migrated = migrate_main_db_industry_reports(db)
            if migrated:
                log.info("深度研报已迁移至行业独立库: %s", migrated)
        finally:
            db.close()
        from app.services.pipeline_runner import recover_stale_jobs

        recovered = recover_stale_jobs()
        if recovered:
            log.warning("启动时清理遗留采集任务: %s", recovered)
        start_scheduler()
        log.info(
            "定时采集已启用（需保持进程运行）：整点新闻 + 每日 cron=%s",
            cfg.daily_pipeline_cron,
        )
    except Exception:
        log.exception("启动初始化失败，HTTP 服务仍继续；修复后刷新或重启即可")


@app.on_event("shutdown")
def on_shutdown():
    from app.services.http_client import close_http_client

    shutdown_scheduler()
    close_http_client()


def _parse_report_date(value: str | None) -> date:
    if not value:
        return tokyo_today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="报告日期格式无效，请使用 YYYY-MM-DD") from exc


def _published_timestamp(value: object) -> float:
    """兼容 SQLite 历史字符串和 datetime，供新闻时间轴稳定排序。"""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        try:
            return float(value.timestamp())
        except (OSError, OverflowError, ValueError):
            return 0.0
    if isinstance(value, date):
        return float(datetime.combine(value, datetime.min.time()).timestamp())
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return float(datetime.fromisoformat(text).timestamp())
        except ValueError:
            return 0.0
    return 0.0


def _sort_news(entries: list[NewsArticle]) -> list[NewsArticle]:
    """按事件发生/发布时间倒序；缺失发布时间的旧记录排在最后。"""

    return sorted(
        entries,
        key=lambda e: (
            _published_timestamp(getattr(e, "published_at", None)),
            (e.report_date.toordinal() if e.report_date else 0),
            e.id,
        ),
        reverse=True,
    )



def _dedupe_daily_news(entries: list[NewsArticle]) -> list[NewsArticle]:
    """合并日报与历史快照，优先保留近 24 小时采集到的同源新闻。"""

    ranked = sorted(
        entries,
        key=lambda e: (
            0 if e.window_hours == NEWS_WINDOW_HOURS_24 else 1,
            -_published_timestamp(getattr(e, "published_at", None)),
            -int(getattr(e, "id", 0) or 0),
        ),
    )
    seen: set[str] = set()
    unique: list[NewsArticle] = []
    for entry in ranked:
        source_url = (entry.source_url or "").strip().rstrip("/").lower()
        title = " ".join((entry.title or "").lower().split())
        identity = f"url:{source_url}" if source_url else f"title:{title}"
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(entry)
    return _sort_news(unique)


def _news_charts_json(entries: list[NewsArticle]) -> str:
    entry_charts: list[dict] = []
    for e in entries:
        if not e.structured_json:
            continue
        try:
            payload = json.loads(e.structured_json)
            raw = payload.get("_chart_specs")
            if not raw:
                continue
            specs = json.loads(raw) if isinstance(raw, str) else raw
            if specs:
                entry_charts.append({"entry_id": e.id, "specs": specs})
        except (json.JSONDecodeError, TypeError):
            continue
    return json.dumps(entry_charts, ensure_ascii=False)


def _daily_news_context(
    *,
    request: Request,
    report_date: str | None,
    timeline_day: str | None = None,
    module_code: str | None,
    db: Session,
    page_key: str = "daily_news",
) -> dict:
    if page_key not in PAGE_META or page_key not in ("daily_news", "news_7x24"):
        page_key = "daily_news"
    allowed = modules_for_page(page_key)
    allowed_codes = tuple(allowed.keys())
    meta = PAGE_META[page_key]
    window_hours = int(meta.get("window_hours") or NEWS_WINDOW_HOURS_24)
    rd = _parse_report_date(report_date)
    today = tokyo_today()
    day_tabs: list[dict] = []
    selected_timeline_day: date | None = None
    if page_key == "daily_news":
        # 单一入口：沿用近 24 小时卡片排版，并按日查看最近一周的已保存资讯。
        if rd < today - timedelta(days=6) or rd > today:
            rd = today
        for offset, tab_day in enumerate(tokyo_day_tabs(7)):
            label = "今日" if offset == 0 else f"{tab_day.month}/{tab_day.day}"
            day_tabs.append(
                {
                    "label": label,
                    "date": tab_day.isoformat(),
                    "href": f"{meta['path']}?report_date={tab_day.isoformat()}",
                    "active": rd == tab_day,
                }
            )

    selected = (module_code or "").upper() or None
    if selected and selected not in allowed:
        selected = None

    q = (
        db.query(NewsArticle)
        .filter(NewsArticle.module_code.in_(allowed_codes))
    )
    if page_key == "daily_news":
        q = q.filter(NewsArticle.window_hours.in_((NEWS_WINDOW_HOURS_24, NEWS_WINDOW_HOURS_7X24)))
        q = q.filter(NewsArticle.report_date == rd)
    elif page_key == "news_7x24":
        q = q.filter(NewsArticle.window_hours == window_hours)
        if selected_timeline_day:
            q = q.filter(NewsArticle.report_date == selected_timeline_day)
        else:
            q = q.filter(NewsArticle.report_date >= rd - timedelta(days=6)).filter(
                NewsArticle.report_date <= rd
            )
    else:
        q = q.filter(NewsArticle.window_hours == window_hours)
        q = q.filter(NewsArticle.report_date == rd)
    if selected:
        q = q.filter(NewsArticle.module_code == selected)
    raw_entries = q.all()
    raw_entries = _dedupe_daily_news(raw_entries) if page_key == "daily_news" else _sort_news(raw_entries)
    # 展示层再滤一遍，立刻隐藏历史越界旧数据（科普/彩票/非本板块等）
    entries: list[NewsArticle] = []
    for e in raw_entries:
        ok, _ = item_in_module_scope(
            e.module_code,
            title=e.title or "",
            content=f"{e.summary or ''} {e.impact_analysis or ''}",
            source=str(e.source_title or e.source_url or ""),
            related_company=str(e.related_company or ""),
        )
        if ok:
            entries.append(e)

    def _social_for(e: NewsArticle) -> dict:
        return resolve_social_source(
            source_url=e.source_url,
            structured_json=e.structured_json,
        )

    display_cards = build_display_cards(db, entries, social_resolver=_social_for)

    # 各日报板块与 7×24 共用同一时间轴顺序：最新发布时间优先，未核验时间置后。
    def _display_card_sort_key(card) -> tuple[float, int]:
        return _published_timestamp(getattr(card, "published_at", None)), int(getattr(card, "id", 0) or 0)

    display_cards.sort(key=_display_card_sort_key, reverse=True)

    runs = (
        db.query(ReportRun)
        .filter(ReportRun.report_date == rd)
        .filter(ReportRun.module_code.in_(allowed_codes))
        .filter(ReportRun.window_hours == window_hours)
        .all()
    )
    run_map = {r.module_code: r for r in runs}

    stats = {"低": 0, "中": 0, "高": 0, "极高": 0}
    for e in display_cards:
        if e.risk_level in stats:
            stats[e.risk_level] += 1

    grouped: dict[str, list] = {k: [] for k in allowed_codes}
    for card in display_cards:
        grouped.setdefault(card.module_code, []).append(card)

    overview = {
        "total": len(display_cards),
        "high": sum(1 for e in display_cards if e.risk_level in {"高", "极高"}),
        "sources": len({(e.source_title or e.source_url or "").strip() for e in display_cards if (e.source_title or e.source_url)}),
        "latest": [
            {"code": code, "name": allowed[code], "title": rows[0].title, "level": rows[0].risk_level}
            for code, rows in grouped.items() if rows
        ],
    }

    # 兼容旧跑批：completed+0 但当日检索日志全失败 → 仍显示请求失败
    failed_log_modules: set[str] = set()
    okish_log_modules: set[str] = set()
    for log in (
        db.query(SearchLog)
        .filter(SearchLog.module_code.in_(allowed_codes))
        .order_by(SearchLog.id.desc())
        .limit(80)
        .all()
    ):
        code = log.module_code
        if (log.status or "").lower() == "failed":
            failed_log_modules.add(code)
        elif (log.status or "").lower() in ("completed", "empty"):
            okish_log_modules.add(code)

    module_ui: dict[str, dict[str, str]] = {}
    for code in allowed_codes:
        run = run_map.get(code)
        has_entries = bool(grouped.get(code))
        if has_entries:
            module_ui[code] = {
                "state": "ok",
                "message": "",
            }
        elif not run:
            module_ui[code] = {"state": "idle", "message": "尚未采集，请点击右上角“刷新”开始采集。"}
        elif (run.status or "").lower() == "failed" or (
            run.notes and "请求失败" in (run.notes or "")
        ):
            module_ui[code] = {
                "state": "failed",
                "message": "请求失败，请重新加载",
                "detail": (run.notes or "")[:200],
            }
        elif (
            (run.status or "").lower() in ("empty", "completed")
            and (run.entry_count or 0) == 0
            and code in failed_log_modules
            and code not in okish_log_modules
        ):
            module_ui[code] = {
                "state": "failed",
                "message": "请求失败，请重新加载",
                "detail": (run.notes or "外网请求失败")[:200],
            }
        elif (run.status or "").lower() in ("empty", "completed") and (run.entry_count or 0) == 0:
            module_ui[code] = {"state": "empty", "message": "今日无动态"}
        else:
            module_ui[code] = {"state": "idle", "message": "尚未采集，请点击右上角“刷新”开始采集。"}

    return {
        "request": request,
        "app_name": settings.app_name,
        "active_page": page_key,
        "page_path": meta["path"],
        "page_title": meta["title"],
        "page_subtitle": meta["subtitle"],
        "report_date": rd.isoformat(),
        "modules": allowed,
        "module_codes_csv": ",".join(allowed_codes),
        "selected_module": selected or "",
        "entries": display_cards,
        "grouped_entries": grouped,
        "stats": stats,
        "overview": overview,
        "timeline_entries": display_cards if page_key == "news_7x24" else [],
        "timeline_start": (rd - timedelta(days=6)).isoformat() if page_key == "news_7x24" else None,
        "timeline_range_label": (
            f"仅查看 {selected_timeline_day.isoformat()}"
            if selected_timeline_day
            else f"{(rd - timedelta(days=6)).isoformat()} 至 {rd.isoformat()}"
        ) if page_key == "news_7x24" else None,
        "run_map": run_map,
        "module_ui": module_ui,
        "entry_charts_json": _news_charts_json(entries),
        "pages": PAGE_META,
        "window_hours": window_hours,
        "collect_label": meta.get("collect_label") or f"采集近{window_hours}小时资讯",
        "empty_hint": meta.get("empty_hint")
        or "当前筛选条件下暂无条目。请点击右上角“刷新”采集资讯。",
        "news_subnav": False,
        "day_tabs": day_tabs,
        "tokyo_today": today.isoformat(),
        # 无条目且无当日跑批记录时才自动补采，避免「今日无动态」反复触发
        "auto_backfill": bool(
            page_key == "news_7x24"
            and not entries
            and not run_map
            and settings.news_auto_backfill_on_empty
        ),
    }


def _entity_assessment_context(
    *,
    request: Request,
    report_date: str | None,
    entity_id: int | None,
    db: Session,
    live: bool = False,
) -> dict:
    page_key = "entity_assessment"
    meta = PAGE_META[page_key]
    rd = _parse_report_date(report_date)
    lookback_days = news_lookback_days()
    lookback_start = news_lookback_start(rd, lookback_days)
    allowed = modules_for_page(page_key)
    allowed_codes = tuple(allowed.keys())

    entities = (
        db.query(TargetEntity)
        .filter(TargetEntity.monitor_status == "active")
        .order_by(TargetEntity.name.asc())
        .all()
    )
    catalog = configured_entity_catalog()
    entity_groups = group_monitored_entities(entities, catalog)

    selected_entity: TargetEntity | None = None
    if entity_id:
        selected_entity = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    else:
        selected_entity = next(
            (ent for group in entity_groups for ent in group["entities"]),
            None,
        )
    if selected_entity is not None:
        apply_bilingual_display_name(selected_entity)

    risks: list[EntityRisk] = []
    entity_sources: list[dict] = []
    recent_risks_count = 0
    observed_source_count = 0
    unverified_time_count = 0
    profile = None
    if selected_entity:
        risks = (
            db.query(EntityRisk)
            .filter(
                EntityRisk.entity_id == selected_entity.id,
                EntityRisk.provenance != "demo",
            )
            .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
            .limit(500)
            .all()
        )
        profile = catalog.find(
            (selected_entity.name, selected_entity.display_name, selected_entity.aliases)
        )
        # 近三个月内：目标企业 / 已点名股东与上下游 / 高管变动；排除行业背景与营销稿。
        display_risks = [
            r
            for r in risks
            if r.report_date is not None
            and lookback_start <= r.report_date <= rd
            and is_monitored_public_event(r, entity=selected_entity, profile=profile)
            and is_substantive_news_item(
                {
                    "title": r.title,
                    "summary": r.summary,
                    "url": r.source_url,
                }
            )
        ]
        recent_risks_count = max(0, len(risks) - len(display_risks))
        observed_source_count = len(
            {
                (
                    risk.source_name
                    or urlparse(risk.source_url or "").netloc
                    or ""
                ).strip().lower()
                for risk in risks
                if (risk.source_name or risk.source_url or "").strip()
            }
        )
        unverified_time_count = sum(1 for risk in display_risks if risk.published_at is None)
        translated_risks = translate_fields_to_chinese([
            {
                "id": str(risk.id),
                "title": risk.title or "",
                "summary": risk.summary or "",
                "impact": risk.impact_analysis or "",
            }
            for risk in display_risks
        ], db=db, cache_source="entity_risk_display")
        for risk in display_risks:
            translated = translated_risks.get(str(risk.id), {})
            detail_available = has_publishable_content_detail(risk.title, risk.summary)
            setattr(risk, "display_title", translated.get("title") or risk.title)
            setattr(risk, "display_summary", translated.get("summary") or risk.summary)
            setattr(risk, "display_impact_analysis", translated.get("impact") or risk.impact_analysis)
            setattr(
                risk,
                "display_overview",
                format_news_overview(
                    title=risk.display_title,
                    summary=risk.display_summary if detail_available else "",
                    source_name=risk.source_name,
                    source_url=risk.source_url,
                    published_at=risk.published_at,
                    subject=risk.related_company,
                ),
            )
            setattr(risk, "risk_tab", classify_risk_tab(risk.risk_category))
            setattr(
                risk,
                "risk_reasoning",
                build_risk_reasoning(
                    title=risk.display_title,
                    summary=risk.display_summary,
                    impact=risk.display_impact_analysis,
                    risk_level=risk.risk_level,
                    category=risk.risk_category,
                ),
            )

        if profile:
            entity_sources = [source.as_dict() for source in profile.sources if source.enabled]
    else:
        display_risks = []

    credit_counts = {lv: 0 for lv in CREDIT_LEVELS}
    for ent in entities:
        if ent.credit_level in credit_counts:
            credit_counts[ent.credit_level] += 1

    runs = (
        db.query(ReportRun)
        .filter(ReportRun.report_date == rd)
        .filter(ReportRun.module_code.in_(allowed_codes))
        .all()
    )
    run_map = {r.module_code: r for r in runs}

    return {
        "request": request,
        "app_name": settings.app_name,
        "active_page": page_key,
        "page_path": meta["path"],
        "page_title": meta["title"],
        "page_subtitle": meta["subtitle"],
        "report_date": rd.isoformat(),
        "news_lookback_days": lookback_days,
        "news_lookback_start": lookback_start.isoformat(),
        "modules": allowed,
        "module_codes_csv": ",".join(allowed_codes),
        "entities": entities,
        "entity_groups": entity_groups,
        "selected_entity": selected_entity,
        "risks": display_risks,
        "all_risks_count": len(risks) if selected_entity else 0,
        "recent_risks_count": recent_risks_count,
        "observed_source_count": observed_source_count,
        "unverified_time_count": unverified_time_count,
        "entity_sources": entity_sources,
        "latest_news": build_latest_news(
            risks=display_risks,
            report_date=rd,
            profile=profile,
            live=live,
            recent_risks=risks if selected_entity else [],
            lookback_days=lookback_days,
        ),
        "financials": build_financials_panel(profile, live=live),
        "credit_levels": CREDIT_LEVELS,
        "credit_counts": credit_counts,
        "run_map": run_map,
        "pages": PAGE_META,
    }


@app.get("/", response_class=HTMLResponse)
def root_redirect():
    return RedirectResponse(url="/daily-news", status_code=302)


@app.get("/industry-analysis", response_class=HTMLResponse)
def industry_analysis_redirect(request: Request):
    qs = request.url.query
    if qs:
        report_id = request.query_params.get("report_id")
        if report_id and report_id.isdigit():
            sector_key = find_report_sector(int(report_id))
            if sector_key:
                return RedirectResponse(
                    url=f"/deep-reports/{sector_key}?report_id={report_id}",
                    status_code=302,
                )
    return RedirectResponse(url="/deep-reports", status_code=302)


def _deep_reports_shell_context(
    request: Request,
    *,
    sector_key: str | None = None,
    report_id: int | None = None,
    db: Session | None = None,
    prefer_client_redirect: bool = False,
) -> dict:
    refresh_sectors()
    sectors = list(INDUSTRY_SECTORS.values())
    history_reports = list_all_sector_reports(limit=40)
    sector = None
    selected = None
    industry_sources: list = []
    display_report_html = ""
    citation_context = None
    candidate_preview = None
    candidate_validation = None
    candidate_stale = False
    generation_config: dict = {}
    source_list_markup = ""

    if sector_key:
        sector = require_sector_key(sector_key)
        if db is not None and report_id:
            selected = IndustryAnalysisService(db).get_report(report_id)
        if selected:
            industry_sources = list_industry_sources(db, selected.id)
            display_report_html = selected.report_html or ""
            source_list_markup = source_list_html(industry_sources)
            if selected.generation_config_json:
                try:
                    parsed_cfg = json.loads(selected.generation_config_json)
                    if isinstance(parsed_cfg, dict):
                        generation_config = parsed_cfg
                except json.JSONDecodeError:
                    generation_config = {}
            if selected.status == "completed" and selected.generation_mode == "grounded":
                try:
                    citation_context = build_citation_context(db, selected)
                    display_report_html = render_report_html(citation_context)
                except CitationPresentationError:
                    display_report_html = (
                        '<aside class="citation-warning" role="alert">'
                        "证据约束报告当前无法安全解析，已停止展示正文。请重新生成并晋升报告。"
                        "</aside>"
                    )
            elif selected.status == "awaiting_approval" and selected.grounded_run_id:
                run = db.query(IndustryGroundedReportRun).filter(
                    IndustryGroundedReportRun.report_id == selected.id,
                    IndustryGroundedReportRun.id == selected.grounded_run_id,
                ).first()
                if run and run.candidate_report_json:
                    try:
                        citation_context = build_citation_context(
                            db, selected, run.candidate_report_json,
                        )
                        candidate_preview = render_report_html(citation_context)
                        candidate_validation = json.loads(run.validation_errors_json or "{}")
                        packet = build_evidence_packet(db, selected.id)
                        candidate_stale = (
                            run.status != "validated"
                            or run.evidence_snapshot_hash != packet["evidence_snapshot_hash"]
                            or run.conflict_snapshot_hash != packet["conflict_snapshot_hash"]
                        )
                    except (CitationPresentationError, json.JSONDecodeError, ValueError):
                        candidate_stale = True

    meta = PAGE_META["deep_reports"]
    title = meta["title"]
    if sector:
        title = f"{meta['title']} · {sector.label}"
    return {
        "request": request,
        "app_name": settings.app_name,
        "active_page": "deep_reports",
        "page_title": title,
        "page_subtitle": meta["subtitle"],
        "pages": PAGE_META,
        "sector_key": sector.key if sector else "",
        "sector_label": sector.label if sector else "",
        "sector_default_industry_name": sector.default_industry_name if sector else "",
        "sector_selected": bool(sector),
        "sectors": sectors,
        "sector_keys": [item.key for item in sectors],
        "prefer_client_redirect": prefer_client_redirect,
        "reports": history_reports,
        "selected_report": selected,
        "display_report_html": display_report_html,
        "citation_context": citation_context,
        "candidate_preview": candidate_preview,
        "candidate_validation": candidate_validation,
        "candidate_stale": candidate_stale,
        "drawer_sources": industry_sources,
        "drawer_ai_sources": [
            source for source in industry_sources
            if source.source_type == "network_search" or source.source_origin == "network_search"
        ],
        "drawer_manual_sources": [
            source for source in industry_sources
            if source.source_type != "network_search" and source.source_origin != "network_search"
        ],
        "drawer_selected_count": sum(
            1 for source in industry_sources
            if source.is_selected and (source.extracted_text or "").strip()
        ),
        "drawer_usable_count": sum(
            1 for source in industry_sources if (source.extracted_text or "").strip()
        ),
        "drawer_report_id": selected.id if selected else None,
        "drawer_report_status": selected.status if selected else "",
        "generation_config": generation_config,
        "source_list_markup": source_list_markup,
        "mita_configured": bool(settings.mita_api_key)
        and not is_placeholder_key(settings.mita_api_key),
        "industry_generation_mode": settings.industry_report_generation_mode,
    }


@app.get("/deep-reports", response_class=HTMLResponse)
def deep_reports_index(request: Request, report_id: int | None = None):
    if report_id:
        sector_key = find_report_sector(report_id)
        if sector_key:
            return RedirectResponse(
                url=f"/deep-reports/{sector_key}?report_id={report_id}",
                status_code=302,
            )
    sectors = refresh_sectors()
    if not sectors:
        ctx = _deep_reports_shell_context(request, prefer_client_redirect=False)
        return templates.TemplateResponse("industry_analysis.html", ctx)
    # 有行业时交给前端按「上次打开 / 列表第一项」跳转
    ctx = _deep_reports_shell_context(request, prefer_client_redirect=True)
    return templates.TemplateResponse("industry_analysis.html", ctx)


@app.get("/deep-reports/{sector_key}", response_class=HTMLResponse)
def deep_reports_sector_page(
    request: Request,
    sector_key: str,
    report_id: int | None = None,
):
    try:
        require_sector_key(sector_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="行业分类不存在") from exc
    with industry_session(sector_key) as db:
        ctx = _deep_reports_shell_context(
            request,
            sector_key=sector_key,
            report_id=report_id,
            db=db,
        )
    return templates.TemplateResponse("industry_analysis.html", ctx)


@app.get("/daily-news", response_class=HTMLResponse)
def daily_news_page(
    request: Request,
    report_date: str | None = None,
    module_code: str | None = None,
    db: Session = Depends(get_db),
):
    ctx = _daily_news_context(
        request=request,
        report_date=report_date,
        module_code=module_code,
        db=db,
        page_key="daily_news",
    )
    return templates.TemplateResponse("dashboard.html", ctx)


@app.get("/daily-news-7x24", response_class=HTMLResponse)
def daily_news_7x24_page(
    request: Request,
    report_date: str | None = None,
    timeline_day: str | None = None,
    module_code: str | None = None,
):
    """兼容旧链接：7×24 内容已合并到按日浏览的新闻汇总页。"""

    selected_date = timeline_day or report_date
    query: dict[str, str] = {}
    if selected_date:
        query["report_date"] = selected_date
    if module_code:
        query["module_code"] = module_code
    suffix = f"?{urlencode(query)}" if query else ""
    return RedirectResponse(url=f"/daily-news{suffix}", status_code=302)


@app.get("/entity-assessment", response_class=HTMLResponse)
def entity_assessment_page(
    request: Request,
    report_date: str | None = None,
    entity_id: int | None = None,
    db: Session = Depends(get_db),
):
    ctx = _entity_assessment_context(
        request=request,
        report_date=report_date,
        entity_id=entity_id,
        db=db,
        live=False,
    )
    return templates.TemplateResponse("entity_assessment.html", ctx)


@app.get("/entity-assessment/live-panels")
def entity_assessment_live_panels(
    request: Request,
    report_date: str | None = None,
    entity_id: int | None = None,
    db: Session = Depends(get_db),
):
    if not entity_id:
        raise HTTPException(status_code=400, detail="缺少 entity_id")
    ctx = _entity_assessment_context(
        request=request,
        report_date=report_date,
        entity_id=entity_id,
        db=db,
        live=True,
    )
    news_html = templates.get_template("entity_assessment_news_panel.html").render(ctx)
    finance_html = templates.get_template("entity_assessment_finance_panel.html").render(ctx)
    event_html = templates.get_template("entity_assessment_live_event_cards.html").render(ctx)
    return JSONResponse({"news_html": news_html, "finance_html": finance_html, "event_html": event_html})



@app.get("/intl-ratings", response_class=HTMLResponse)
def intl_ratings_page(request: Request):
    meta = PAGE_META["intl_ratings"]
    return templates.TemplateResponse(
        "intl_ratings.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "intl_ratings",
            "page_path": meta["path"],
            "page_title": meta["title"],
            "page_subtitle": meta["subtitle"],
            "pages": PAGE_META,
        },
    )
