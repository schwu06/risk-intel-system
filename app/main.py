"""FastAPI 应用入口与 Web 仪表盘。"""

from datetime import date
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.routes import router as api_router
from app.config import CREDIT_LEVELS, MODULE_CODES, PAGE_META, get_settings, modules_for_page
from app.database.models import CreditUpdate, EntityRisk, NewsArticle, ReportRun, SearchLog, TargetEntity
from app.database.session import get_db, init_db
from app.services.api_keys import is_placeholder_key
from app.services.data_bridge import migrate_legacy_data
from app.services.data_source_service import list_all_sources, list_industry_sources, list_module_sources
from app.services.domain_rules import seed_default_domains
from app.services.industry_analysis import IndustryAnalysisService
from app.services.pipeline import RISK_LEVEL_ORDER
from app.services.scheduler import shutdown_scheduler, start_scheduler

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
    if "mita.ai" in cfg.mita_api_base_url:
        log.warning("MITA_API_BASE_URL 应设为 https://metaso.cn/api/v1，请更新 .env 后重启服务")

    db = SessionLocal()
    try:
        seed_default_domains(db)
        stats = migrate_legacy_data(db)
        log.info("切片2数据就绪: %s", stats)
    finally:
        db.close()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    shutdown_scheduler()


def _parse_report_date(value: str | None) -> date:
    if not value:
        return date.today()
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


def _source_drawer_context(
    db: Session,
    *,
    industry_name: str | None = None,
) -> dict:
    """全站统一数据源抽屉。"""
    sources = list_all_sources(db)
    return {
        "drawer_sources": sources,
        "drawer_modules": dict(MODULE_CODES),
        "drawer_module_sources": {},
        "drawer_industry_sources": [],
        "drawer_industry_name": industry_name or "",
    }


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
) -> dict:
    page_key = "daily_news"
    allowed = modules_for_page(page_key)
    allowed_codes = tuple(allowed.keys())
    meta = PAGE_META[page_key]
    rd = _parse_report_date(report_date)

    selected = (module_code or "").upper() or None
    if selected and selected not in allowed:
        selected = None

    q = (
        db.query(NewsArticle)
        .filter(NewsArticle.report_date == rd)
        .filter(NewsArticle.module_code.in_(allowed_codes))
    )
    if selected:
        q = q.filter(NewsArticle.module_code == selected)
    entries = _sort_news(q.all())

    runs = (
        db.query(ReportRun)
        .filter(ReportRun.report_date == rd)
        .filter(ReportRun.module_code.in_(allowed_codes))
        .all()
    )
    run_map = {r.module_code: r for r in runs}

    stats = {"低": 0, "中": 0, "高": 0, "极高": 0}
    for e in entries:
        if e.risk_level in stats:
            stats[e.risk_level] += 1

    grouped: dict[str, list[NewsArticle]] = {k: [] for k in allowed_codes}
    for e in entries:
        grouped.setdefault(e.module_code, []).append(e)

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

    module_sources = {code: list_module_sources(db, code) for code in allowed_codes}

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
        "entries": entries,
        "grouped_entries": grouped,
        "stats": stats,
        "run_map": run_map,
        "module_ui": module_ui,
        "module_sources": module_sources,
        "entry_charts_json": _news_charts_json(entries),
        "pages": PAGE_META,
        **_source_drawer_context(db),
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
    if selected_entity:
        risks = (
            db.query(EntityRisk)
            .filter(EntityRisk.entity_id == selected_entity.id)
            .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
            .limit(100)
            .all()
        )
        # 可选：按报告日期过滤展示
        date_filtered = [r for r in risks if r.report_date == rd]
        # 侧栏仍展示全部近期；主区优先当日，若无则展示全部
        display_risks = date_filtered if date_filtered else risks

        credit_logs = (
            db.query(CreditUpdate)
            .filter(CreditUpdate.entity_id == selected_entity.id)
            .order_by(CreditUpdate.created_at.desc())
            .limit(20)
            .all()
        )
    else:
        display_risks = []

    credit_counts = {lv: 0 for lv in CREDIT_LEVELS}
    for ent in entities:
        if ent.credit_level in credit_counts:
            credit_counts[ent.credit_level] += 1

    module_sources = {code: list_module_sources(db, code) for code in allowed_codes}
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
        "credit_logs": credit_logs,
        "credit_levels": CREDIT_LEVELS,
        "credit_counts": credit_counts,
        "module_sources": module_sources,
        "run_map": run_map,
        "pages": PAGE_META,
        **_source_drawer_context(db),
    }


@app.get("/", response_class=HTMLResponse)
def root_redirect():
    return RedirectResponse(url="/daily-news", status_code=302)


@app.get("/industry-analysis", response_class=HTMLResponse)
def industry_analysis_redirect(request: Request):
    qs = request.url.query
    target = "/deep-reports" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=302)


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


@app.get("/deep-reports", response_class=HTMLResponse)
def deep_reports_page(
    request: Request,
    report_id: int | None = None,
    db: Session = Depends(get_db),
):
    reports = IndustryAnalysisService(db).list_reports(limit=15)
    selected = None
    if report_id:
        selected = IndustryAnalysisService(db).get_report(report_id)
    elif reports:
        selected = reports[0]

    industry_name = selected.industry_name if selected else None
    industry_sources = list_industry_sources(db, industry_name) if industry_name else []

    meta = PAGE_META["deep_reports"]
    return templates.TemplateResponse(
        "industry_analysis.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "deep_reports",
            "page_title": meta["title"],
            "page_subtitle": meta["subtitle"],
            "pages": PAGE_META,
            "reports": reports,
            "selected_report": selected,
            "industry_sources": industry_sources,
            **_source_drawer_context(db, industry_name=industry_name),
        },
    )
