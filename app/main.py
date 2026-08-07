"""FastAPI 应用入口与 Web 仪表盘。"""

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.routes import router as api_router
from app.config import CREDIT_LEVELS, MODULE_CODES, NEWS_WINDOW_HOURS_24, PAGE_META, get_settings, modules_for_page
from app.database.models import (
    CreditUpdate, EntityRisk, IndustryGroundedReportRun, NewsArticle,
    ReportRun, SearchLog, TargetEntity,
)
from app.database.session import get_db, init_db
from app.database.industry_db import find_report_sector, industry_session, init_all_industry_databases
from app.industry_sectors import INDUSTRY_SECTORS, require_sector_key
from app.services.api_keys import is_placeholder_key
from app.services.data_bridge import migrate_legacy_data
from app.services.data_source_service import list_industry_sources
from app.services.display_zh import build_display_cards
from app.services.domain_rules import seed_default_domains
from app.services.entity_catalog import configured_entity_catalog
from app.services.industry_analysis import IndustryAnalysisService
from app.services.industry_migration import migrate_main_db_industry_reports
from app.services.news_section_router import item_in_module_scope
from app.services.citation_rendering import (
    CitationPresentationError, build_citation_context, render_report_html,
)
from app.services.evidence_packet import build_evidence_packet
from app.services.pipeline import RISK_LEVEL_ORDER
from app.services.scheduler import shutdown_scheduler, start_scheduler
from app.services.social_source import resolve_social_source
from app.timeutil import tokyo_day_tabs, tokyo_today

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

settings = get_settings()
app = FastAPI(title=settings.app_name, version="2.1.0")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
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

    db = SessionLocal()
    try:
        seed_default_domains(db)
        stats = migrate_legacy_data(db)
        log.info("切片2数据就绪: %s", stats)
        init_all_industry_databases()
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


@app.on_event("shutdown")
def on_shutdown():
    shutdown_scheduler()


def _parse_report_date(value: str | None) -> date:
    if not value:
        return tokyo_today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="报告日期格式无效，请使用 YYYY-MM-DD") from exc


def _sort_news(entries: list[NewsArticle]) -> list[NewsArticle]:
    return sorted(
        entries,
        key=lambda e: (
            e.module_code,
            -RISK_LEVEL_ORDER.get(e.risk_level, 0),
            e.id,
        ),
    )



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
    # 7×24：按日快照，与近24小时共用 window_hours=24
    if page_key == "news_7x24":
        window_hours = NEWS_WINDOW_HOURS_24
    rd = _parse_report_date(report_date)
    today = tokyo_today()
    day_tabs: list[dict] = []
    if page_key == "news_7x24":
        for d in tokyo_day_tabs(7):
            day_tabs.append(
                {
                    "date": d.isoformat(),
                    "label": "今天" if d == today else d.strftime("%m-%d"),
                    "active": d == rd,
                }
            )
        # 仅允许最近 7 个东京日
        allowed_days = {t["date"] for t in day_tabs}
        if rd.isoformat() not in allowed_days:
            rd = today
        for t in day_tabs:
            t["active"] = t["date"] == rd.isoformat()

    selected = (module_code or "").upper() or None
    if selected and selected not in allowed:
        selected = None

    q = (
        db.query(NewsArticle)
        .filter(NewsArticle.report_date == rd)
        .filter(NewsArticle.module_code.in_(allowed_codes))
        .filter(NewsArticle.window_hours == window_hours)
    )
    if selected:
        q = q.filter(NewsArticle.module_code == selected)
    raw_entries = _sort_news(q.all())
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

    runs = (
        db.query(ReportRun)
        .filter(ReportRun.report_date == rd)
        .filter(ReportRun.module_code.in_(allowed_codes))
        .filter(ReportRun.window_hours == window_hours)
        .all()
    )
    run_map = {r.module_code: r for r in runs}

    stats = {"低": 0, "中": 0, "高": 0, "极高": 0}
    for e in entries:
        if e.risk_level in stats:
            stats[e.risk_level] += 1

    grouped: dict[str, list] = {k: [] for k in allowed_codes}
    for card in display_cards:
        grouped.setdefault(card.module_code, []).append(card)

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
            module_ui[code] = {"state": "ok", "message": ""}
        elif not run:
            module_ui[code] = {"state": "idle", "message": "尚未采集，请点击侧边栏运行流水线。"}
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
            module_ui[code] = {"state": "idle", "message": "尚未采集，请点击侧边栏运行流水线。"}

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
        "run_map": run_map,
        "module_ui": module_ui,
        "entry_charts_json": _news_charts_json(entries),
        "pages": PAGE_META,
        "window_hours": window_hours,
        "collect_label": meta.get("collect_label") or f"采集近{window_hours}小时资讯",
        "empty_hint": meta.get("empty_hint")
        or "当前筛选条件下暂无条目。可通过侧边栏运行流水线采集资讯。",
        "news_subnav": True,
        "day_tabs": day_tabs,
        "tokyo_today": today.isoformat(),
        # 无条目且无当日跑批记录时才自动补采，避免「今日无动态」反复触发
        "auto_backfill": bool(
            page_key == "news_7x24"
            and not entries
            and not run_map
        ),
    }


def _entity_assessment_context(
    *,
    request: Request,
    report_date: str | None,
    entity_id: int | None,
    db: Session,
) -> dict:
    page_key = "entity_assessment"
    meta = PAGE_META[page_key]
    rd = _parse_report_date(report_date)
    allowed = modules_for_page(page_key)
    allowed_codes = tuple(allowed.keys())

    entities = (
        db.query(TargetEntity)
        .filter(TargetEntity.monitor_status == "active")
        .order_by(TargetEntity.name.asc())
        .all()
    )

    selected_entity: TargetEntity | None = None
    if entity_id:
        selected_entity = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    elif entities:
        selected_entity = entities[0]

    risks: list[EntityRisk] = []
    credit_logs: list[CreditUpdate] = []
    entity_sources: list[dict] = []
    recent_risks_count = 0
    observed_source_count = 0
    unverified_time_count = 0
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
        # 报告日期必须与内容严格一致，不再静默回退到其它日期。
        display_risks = [r for r in risks if r.report_date == rd]
        recent_risks_count = len(risks)
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

        profile = configured_entity_catalog().find(
            (selected_entity.name, selected_entity.display_name, selected_entity.aliases)
        )
        if profile:
            entity_sources = [source.as_dict() for source in profile.sources if source.enabled]

        credit_logs = (
            db.query(CreditUpdate)
            .filter(CreditUpdate.entity_id == selected_entity.id)
            .order_by(CreditUpdate.created_at.desc())
            .limit(20)
            .all()
        )
        credit_logs = [
            log for log in credit_logs if "演示" not in (log.reason or "")
        ]
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
        "modules": allowed,
        "module_codes_csv": ",".join(allowed_codes),
        "entities": entities,
        "selected_entity": selected_entity,
        "risks": display_risks,
        "all_risks_count": len(risks) if selected_entity else 0,
        "recent_risks_count": recent_risks_count,
        "observed_source_count": observed_source_count,
        "unverified_time_count": unverified_time_count,
        "entity_sources": entity_sources,
        "credit_logs": credit_logs,
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


def _deep_reports_sector_context(
    request: Request,
    sector_key: str,
    report_id: int | None,
    db: Session,
) -> dict:
    sector = require_sector_key(sector_key)
    reports = IndustryAnalysisService(db).list_reports(limit=15)
    selected = None
    if report_id:
        selected = IndustryAnalysisService(db).get_report(report_id)
    elif reports:
        selected = reports[0]

    industry_sources = list_industry_sources(db, selected.id) if selected else []
    display_report_html = selected.report_html if selected else ""
    citation_context = None
    candidate_preview = None
    candidate_validation = None
    candidate_stale = False
    if selected and selected.status == "completed" and selected.generation_mode == "grounded":
        try:
            citation_context = build_citation_context(db, selected)
            display_report_html = render_report_html(citation_context)
        except CitationPresentationError:
            display_report_html = (
                '<aside class="citation-warning" role="alert">'
                "证据约束报告当前无法安全解析，已停止展示正文。请重新生成并晋升报告。"
                "</aside>"
            )
    elif (
        selected and selected.status == "awaiting_approval" and selected.grounded_run_id
    ):
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
    return {
        "request": request,
        "app_name": settings.app_name,
        "active_page": "deep_reports",
        "page_title": f"{meta['title']} · {sector.label}",
        "page_subtitle": meta["subtitle"],
        "pages": PAGE_META,
        "sector_key": sector.key,
        "sector_label": sector.label,
        "sector_default_industry_name": sector.default_industry_name,
        "sectors": INDUSTRY_SECTORS.values(),
        "reports": reports,
        "selected_report": selected,
        "display_report_html": display_report_html,
        "citation_context": citation_context,
        "candidate_preview": candidate_preview,
        "candidate_validation": candidate_validation,
        "candidate_stale": candidate_stale,
        "drawer_sources": industry_sources,
        "drawer_report_id": selected.id if selected else None,
        "drawer_report_status": selected.status if selected else "",
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
    default_sector = next(iter(INDUSTRY_SECTORS))
    return RedirectResponse(url=f"/deep-reports/{default_sector}", status_code=302)


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
        ctx = _deep_reports_sector_context(request, sector_key, report_id, db)
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
    module_code: str | None = None,
    db: Session = Depends(get_db),
):
    ctx = _daily_news_context(
        request=request,
        report_date=report_date,
        module_code=module_code,
        db=db,
        page_key="news_7x24",
    )
    return templates.TemplateResponse("dashboard.html", ctx)


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
    )
    return templates.TemplateResponse("entity_assessment.html", ctx)



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
