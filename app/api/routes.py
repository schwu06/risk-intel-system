"""FastAPI 路由。"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import CREDIT_LEVELS, MODULE_CODES, NEWS_WINDOW_HOURS_24, get_settings, news_window_label
from app.database.models import (
    DailyRiskEntry,
    DomainBlacklist,
    DomainWhitelist,
    EntityRisk,
    IndustryConflictDetectionRun,
    IndustryEvidenceCard,
    IndustryEvidenceExtractionRun,
    IndustryReport,
    NewsArticle,
    SearchLog,
    TargetEntity,
)
from app.database.session import get_db
from app.database.industry_db import get_industry_db_with_query
from app.exporters.docx_report import (
    export_daily_report_to_path,
    export_entity_assessment_to_path,
    export_industry_report_to_path,
)
from app.exporters.pdf_report import export_daily_pdf
from app.schemas import (
    ConflictResolutionRequest,
    DataSourceOut,
    DataSourceUrlIn,
    DomainRuleIn,
    EntityRiskOut,
    IndustryAnalysisRequest,
    IndustryDataSourceUrlIn,
    IndustrySectorCreateIn,
    IndustrySectorRenameIn,
    IndustrySectorOut,
    EvidenceExtractRequest,
    IndustryReportRenameIn,
    IndustryReportOut,
    GroundedPromotionRequest,
    IntlRatingRowOut,
    IntlRatingsJobOut,
    IntlRatingsRefreshOut,
    IntlRatingsSnapshotOut,
    ManualEntryIn,
    NewsArticleOut,
    PipelineJobStatusOut,
    PipelineRunRequest,
    PipelineRunResponse,
    RiskEntryOut,
    RssSourceItemOut,
    TargetEntityOut,
)
from app.services.data_source_parser import MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS
from app.services.data_source_service import (
    IndustryReportNotEditableError,
    delete_industry_source,
    delete_module_source,
    get_industry_source_by_id,
    get_source_by_id,
    list_all_sources,
    list_industry_sources,
    list_industry_source_chunks,
    list_module_sources,
    save_industry_file_source,
    save_industry_url_source,
    save_module_file_source,
    save_module_url_source,
)
from app.services.source_registry import source_registry_state
from app.services.evidence_cards import (
    EvidenceCardService,
    EvidenceExtractionError,
    evidence_card_to_dict,
    evidence_run_to_dict,
)
from app.services.conflict_detection import (
    ConflictDetectionError,
    ConflictDetectionService,
    conflict_run_to_dict,
    conflict_to_dict,
)
from app.services.grounded_report import (
    GroundedPromotionError,
    GroundedReportError,
    GroundedReportService,
    grounded_run_to_dict,
)
from app.services.grounded_readiness import check_grounded_readiness
from app.services.deepseek_analyzer import (
    GROUNDED_REPORT_PROMPT_VERSION,
    STRUCTURED_GROUNDED_REPORT_PROMPT_VERSION,
)
from app.services.citation_rendering import (
    CitationPresentationError,
    build_citation_context,
    citation_detail,
)
from app.services.domain_rules import seed_default_domains
from app.services.entity_briefing import news_lookback_start
from app.services.entity_catalog import configured_entity_catalog
from app.services.entity_relevance import is_monitored_public_event
from app.services.industry_analysis import IndustryAnalysisService, IndustryGenerationError
from app.services.pipeline_runner import (
    get_current_job,
    get_job_status,
    get_last_news_refresh,
    get_running_job_id,
    run_modules_sync,
    start_pipeline_job,
)
from app.services.direct_site_config import (
    load_direct_sites_config,
    reload_direct_sites_config,
)
from app.services.rss_config import load_rss_config, reload_rss_config
from app.services.rss_source_service import list_rss_sources_24h
from app.services.intl_ratings_service import get_job, load_snapshot, start_refresh_job
from app.timeutil import tokyo_today

router = APIRouter()


def _source_mutation_payload(row, *, message: str) -> dict:
    """上传/添加数据源的统一响应；采集进行中时标明下次生效。"""
    running = get_running_job_id()
    deferred = bool(running)
    return {
        "message": (
            f"{message}（当前有采集任务在运行，将在下次采集时生效）"
            if deferred
            else message
        ),
        "id": row.id,
        "name": row.name,
        "source_type": row.source_type,
        "original_filename": row.original_filename,
        "url": row.url,
        "priority": getattr(row, "priority", 0) or 0,
        "chars": len(row.extracted_text or ""),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "deferred_to_next_run": deferred,
        "running_job_id": running,
    }


@router.get("/health")
def health():
    cfg = get_settings()
    from app.services.api_keys import is_placeholder_key

    return {
        "status": "正常",
        "service": cfg.app_name,
        "mita_configured": not is_placeholder_key(cfg.mita_api_key),
        "deepseek_configured": not is_placeholder_key(cfg.deepseek_api_key),
    }


@router.get("/modules")
def list_modules():
    return [{"code": k, "name": v} for k, v in MODULE_CODES.items()]


@router.get("/entries", response_model=list[RiskEntryOut])
def list_entries(
    report_date: date | None = Query(None, description="报告日期"),
    module_code: str | None = Query(None, description="模块：A企业与品牌 / B中东日报 / C日本企业 / D宏观 / E行业"),
    db: Session = Depends(get_db),
):
    q = db.query(DailyRiskEntry).order_by(DailyRiskEntry.created_at.desc())
    if report_date:
        q = q.filter(DailyRiskEntry.report_date == report_date)
    if module_code:
        q = q.filter(DailyRiskEntry.module_code == module_code.upper())
    return q.limit(500).all()


@router.post("/pipeline/run", response_model=PipelineRunResponse)
def run_pipeline(body: PipelineRunRequest, db: Session = Depends(get_db)):
    rd = body.report_date or tokyo_today()
    codes = body.module_codes or list(MODULE_CODES.keys())
    settings = get_settings()
    use_async = (
        settings.pipeline_async_default if body.async_mode is None else bool(body.async_mode)
    )
    hours = int(
        body.window_hours
        if body.window_hours is not None
        else (getattr(settings, "news_window_hours", NEWS_WINDOW_HOURS_24) or NEWS_WINDOW_HOURS_24)
    )

    if use_async:
        try:
            started = start_pipeline_job(
                report_date=rd,
                module_codes=codes,
                entity_id=body.entity_id,
                window_hours=hours,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not started.get("accepted"):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": started.get("message"),
                    "job_id": started.get("job_id"),
                    "status": "conflict",
                },
            )
        return PipelineRunResponse(
            report_date=rd,
            results={},
            message=started["message"],
            job_id=started["job_id"],
            async_mode=True,
            status=started.get("status") or "queued",
            entity_id=body.entity_id,
            window_hours=hours,
        )

    outcome = run_modules_sync(
        report_date=rd,
        module_codes=codes,
        entity_id=body.entity_id,
        window_hours=hours,
    )
    if not outcome.get("ok") and all(v == -1 for v in (outcome.get("results") or {}).values()):
        raise HTTPException(status_code=502, detail=outcome.get("message") or "采集失败")
    return PipelineRunResponse(
        report_date=rd,
        results=outcome.get("results") or {},
        message=outcome.get("message") or "",
        async_mode=False,
        status="completed" if outcome.get("ok") else "failed",
        entity_id=body.entity_id,
        window_hours=hours,
    )


@router.get("/pipeline/jobs/{job_id}", response_model=PipelineJobStatusOut)
def pipeline_job_status(job_id: str):
    row = get_job_status(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return PipelineJobStatusOut(**row)


@router.get("/pipeline/running")
def pipeline_running_job(
    window_hours: int | None = Query(None, ge=1, le=168),
    entity_id: int | None = Query(None),
    module_codes: str | None = Query(None, description="逗号分隔模块，如 B,C,D"),
):
    """当前是否有匹配作用域的采集任务（供前端恢复轮询，不阻断其它操作）。"""
    codes = None
    if module_codes:
        codes = [c.strip().upper() for c in module_codes.split(",") if c.strip()]
    row = get_current_job(
        window_hours=window_hours,
        entity_id=entity_id,
        module_codes=codes,
    )
    if not row:
        return {"job_id": None, "status": "idle", "message": ""}
    return row


@router.get("/pipeline/last-refresh")
def pipeline_last_refresh(
    window_hours: int = Query(NEWS_WINDOW_HOURS_24, ge=1, le=168),
    module_codes: str | None = Query("B,C,D", description="逗号分隔模块"),
):
    """最近一次新闻采集完成时间（东京），供界面同步刷新文案且不打断操作。"""
    codes = None
    if module_codes:
        codes = [c.strip().upper() for c in module_codes.split(",") if c.strip()]
    return get_last_news_refresh(window_hours=window_hours, module_codes=codes)


@router.get("/pipeline/rss-config")
def get_rss_config_summary():
    cfg = load_rss_config()
    return {
        "queries": {k: [{"label": q.label, "priority": q.priority, "enabled": q.enabled} for q in v] for k, v in cfg.queries.items()},
        "feeds": [
            {
                "label": f.label,
                "url": f.url,
                "modules": list(f.modules),
                "priority": f.priority,
                "enabled": f.enabled,
                "type": f.feed_type,
            }
            for f in cfg.feeds
        ],
        "max_items_per_feed": cfg.max_items_per_feed,
    }


@router.post("/pipeline/rss-config/reload")
def reload_rss_feeds():
    cfg = reload_rss_config()
    return {
        "message": "RSS 配置已重新加载",
        "query_groups": len(cfg.queries),
        "feeds": len(cfg.feeds),
    }


@router.get("/pipeline/direct-sites")
def get_direct_sites_summary():
    cfg = load_direct_sites_config()
    return {
        "sites": [
            {
                "label": s.label,
                "list_url": s.list_url,
                "modules": list(s.modules),
                "priority": s.priority,
                "enabled": s.enabled,
                "type": s.site_type,
                "item_selector": s.item_selector,
            }
            for s in cfg.sites
        ],
        "max_items_per_site": cfg.max_items_per_site,
    }


@router.post("/pipeline/direct-sites/reload")
def reload_direct_sites():
    cfg = reload_direct_sites_config()
    return {
        "message": "直连站点配置已重新加载",
        "sites": len(cfg.sites),
        "enabled": sum(1 for s in cfg.sites if s.enabled),
    }


@router.post("/entries/manual")
def manual_entries(body: ManualEntryIn, db: Session = Depends(get_db)):
    from app.services.pipeline import RiskPipeline

    pipeline = RiskPipeline(db)
    count = pipeline.ingest_manual_entries(body.module_code, body.report_date, body.entries)
    return {"saved": count, "message": "手工条目已入库"}


@router.get("/search-logs")
def search_logs(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(SearchLog)
        .order_by(SearchLog.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [
        {
            "id": r.id,
            "module_code": r.module_code,
            "query_text": r.query_text,
            "result_count": r.result_count,
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 统一权威数据源（全站共用）
# ---------------------------------------------------------------------------


def _source_to_out(r, *, include_full: bool = False) -> DataSourceOut:
    text = r.extracted_text or ""
    return DataSourceOut(
        id=r.id,
        module_code=r.module_code,
        entity_id=getattr(r, "entity_id", None),
        name=r.name,
        source_type=r.source_type,
        original_filename=r.original_filename,
        url=r.url,
        priority=r.priority,
        created_at=r.created_at,
        text_preview=text[:200] or None,
        extracted_text=(text if include_full else None),
        chars=len(text),
    )


@router.get("/data-sources", response_model=list[DataSourceOut])
def get_all_data_sources(
    entity_id: int | None = Query(None, description="主体专属数据源；缺省返回全站共用源"),
    db: Session = Depends(get_db),
):
    return [_source_to_out(r) for r in list_all_sources(db, entity_id=entity_id)]


@router.get("/data-sources/item/{source_id}", response_model=DataSourceOut)
def get_data_source_detail(source_id: int, db: Session = Depends(get_db)):
    row = get_source_by_id(db, source_id)
    if not row or not row.is_active:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _source_to_out(row, include_full=True)


@router.get("/data-sources/{module_code}", response_model=list[DataSourceOut])
def get_module_data_sources(module_code: str, db: Session = Depends(get_db)):
    """兼容旧路径：忽略模块代码，返回全站共用列表。"""
    return [_source_to_out(r) for r in list_all_sources(db, entity_id=None)]


def _reject_entity_source_write(entity_id: int | None) -> None:
    """主体清单与公开信源只由后端 YAML 配置，接口不允许按主体写入。"""
    if entity_id is not None:
        raise HTTPException(
            status_code=400,
            detail="主体清单与公开信源仅能通过后端配置维护，不能从页面或接口写入",
        )


@router.post("/data-sources/upload")
async def upload_module_data_source(
    name: str = Form(""),
    priority: int = Form(0),
    file: UploadFile = File(...),
    module_code: str = Form(""),  # 兼容旧表单，忽略
    entity_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    _reject_entity_source_write(entity_id)
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，允许: {', '.join(SUPPORTED_EXTENSIONS)}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB 上传上限")
    try:
        row = save_module_file_source(
            db, None, name or filename, filename, content, priority, entity_id=None
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _source_mutation_payload(row, message="数据源已上传")


@router.post("/data-sources/url")
def add_module_url_source(body: DataSourceUrlIn, db: Session = Depends(get_db)):
    _reject_entity_source_write(body.entity_id)
    try:
        row = save_module_url_source(
            db, None, body.name, body.url, body.priority, entity_id=None
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _source_mutation_payload(row, message="网址数据源已添加")


@router.delete("/data-sources/{source_id}")
def remove_module_data_source(source_id: int, db: Session = Depends(get_db)):
    running = get_running_job_id()
    if not delete_module_source(db, source_id):
        raise HTTPException(status_code=404, detail="数据源不存在")
    deferred = bool(running)
    return {
        "message": (
            "已删除（当前有采集任务在运行，将在下次采集时生效）"
            if deferred
            else "已删除"
        ),
        "deferred_to_next_run": deferred,
        "running_job_id": running,
    }


@router.get("/sources/rss", response_model=list[RssSourceItemOut])
def list_rss_sources(
    hours: int = Query(24, ge=1, le=168, description="近 N 小时 RSS 动态"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """近 N 小时内后端 RSS 管道抓取的动态列表（供侧栏勾选）。"""
    rows = list_rss_sources_24h(db, hours=hours, limit=limit)
    return [RssSourceItemOut(**row) for row in rows]


# ---------------------------------------------------------------------------
# 行业分析
# ---------------------------------------------------------------------------


def _industry_source_origin_label(source_type: str) -> str:
    return "补充网络搜索功能" if source_type == "network_search" else "用户添加"


@router.post("/industry/analyze", response_model=IndustryReportOut)
def run_industry_analysis(body: IndustryAnalysisRequest, db: Session = Depends(get_industry_db_with_query)):
    svc = IndustryAnalysisService(db)
    try:
        report = svc.run_analysis(
            body.industry_name,
            company_name=body.company_name,
            supplement_search=body.supplement_search,
        )
    except IndustryGenerationError as exc:
        raise HTTPException(
            status_code=412,
            detail={"code": exc.code, "message": str(exc), "next_step": exc.next_step},
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return report


@router.post("/industry/reports/drafts", response_model=IndustryReportOut)
def create_industry_report_draft(
    body: IndustryAnalysisRequest, db: Session = Depends(get_industry_db_with_query)
):
    try:
        return IndustryAnalysisService(db).create_draft(
            body.industry_name,
            company_name=body.company_name,
            supplement_search=body.supplement_search,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/industry/sectors", response_model=IndustrySectorOut)
def create_industry_sector(body: IndustrySectorCreateIn):
    from app.database.industry_db import init_industry_database
    from app.industry_sectors import add_sector

    try:
        sector = add_sector(
            body.label,
            default_industry_name=body.default_industry_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    init_industry_database(sector.key)
    return IndustrySectorOut(
        key=sector.key,
        label=sector.label,
        default_industry_name=sector.default_industry_name,
    )


@router.patch("/industry/sectors/{sector_key}", response_model=IndustrySectorOut)
def rename_industry_sector(sector_key: str, body: IndustrySectorRenameIn):
    from app.industry_sectors import rename_sector

    try:
        sector = rename_sector(sector_key, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IndustrySectorOut(
        key=sector.key,
        label=sector.label,
        default_industry_name=sector.default_industry_name,
    )


@router.delete("/industry/sectors/{sector_key}")
def delete_industry_sector(sector_key: str):
    from app.database.industry_db import (
        drop_industry_database,
        industry_session,
    )
    from app.industry_sectors import INDUSTRY_SECTORS, refresh_sectors, remove_sector
    from app.services.data_source_service import INDUSTRY_UPLOAD_ROOT
    import shutil

    refresh_sectors()
    if sector_key not in INDUSTRY_SECTORS:
        raise HTTPException(status_code=404, detail="行业不存在")

    report_ids: list[int] = []
    try:
        with industry_session(sector_key) as db:
            report_ids = [row.id for row in IndustryAnalysisService(db).list_reports(limit=10_000)]
    except Exception:
        report_ids = []

    try:
        remove_sector(sector_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    drop_industry_database(sector_key)
    for report_id in report_ids:
        shutil.rmtree(INDUSTRY_UPLOAD_ROOT / str(report_id), ignore_errors=True)
        shutil.rmtree(INDUSTRY_UPLOAD_ROOT / sector_key / str(report_id), ignore_errors=True)
    shutil.rmtree(INDUSTRY_UPLOAD_ROOT / sector_key, ignore_errors=True)

    refresh_sectors()
    next_key = next(iter(INDUSTRY_SECTORS), None)
    return {"ok": True, "deleted": sector_key, "next_sector_key": next_key}


@router.post("/industry/reports/{report_id}/fork", response_model=IndustryReportOut)
def fork_industry_report(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        return IndustryAnalysisService(db).fork_report(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/industry/reports/{report_id}/generate", response_model=IndustryReportOut)
def generate_industry_report(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        return IndustryAnalysisService(db).generate_report(report_id)
    except IndustryGenerationError as exc:
        raise HTTPException(
            status_code=412,
            detail={"code": exc.code, "message": str(exc), "next_step": exc.next_step},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/industry/reports/{report_id}/grounded-readiness")
def get_grounded_readiness(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        return check_grounded_readiness(db, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/industry/reports", response_model=list[IndustryReportOut])
def list_industry_reports(limit: int = 20, db: Session = Depends(get_industry_db_with_query)):
    rows = IndustryAnalysisService(db).list_reports(limit=limit)
    return rows


@router.get("/industry/reports/{report_id}", response_model=IndustryReportOut)
def get_industry_report(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    row = IndustryAnalysisService(db).get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    return row


@router.delete("/industry/reports/{report_id}")
def delete_industry_report(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        deleted = IndustryAnalysisService(db).delete_report(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"message": "报告已删除", "id": report_id}


@router.patch("/industry/reports/{report_id}/name", response_model=IndustryReportOut)
def rename_industry_report(
    report_id: int, body: IndustryReportRenameIn, db: Session = Depends(get_industry_db_with_query)
):
    try:
        return IndustryAnalysisService(db).rename_report(report_id, body.report_name)
    except ValueError as exc:
        status_code = 404 if str(exc) == "报告不存在" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/industry/reports/{report_id}/evidence/extract")
def extract_industry_evidence(
    report_id: int, body: EvidenceExtractRequest | None = None, db: Session = Depends(get_industry_db_with_query)
):
    try:
        run = EvidenceCardService(db).extract(report_id, body.source_id if body else None)
    except ValueError as exc:
        status = 404 if str(exc) in {"report_not_found", "source_not_found_for_report"} else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except EvidenceExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return evidence_run_to_dict(run)


@router.get("/industry/reports/{report_id}/evidence")
def list_industry_evidence(
    report_id: int,
    source_id: int | None = Query(None),
    validation_status: str | None = Query(None),
    claim_type: str | None = Query(None),
    risk_tag: str | None = Query(None),
    db: Session = Depends(get_industry_db_with_query),
):
    try:
        rows = EvidenceCardService(db).list_cards(
            report_id, source_id=source_id, validation_status=validation_status,
            claim_type=claim_type, risk_tag=risk_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [evidence_card_to_dict(row) for row in rows]


@router.get("/industry/reports/{report_id}/evidence/{evidence_code}")
def get_industry_evidence_card(
    report_id: int, evidence_code: str, db: Session = Depends(get_industry_db_with_query)
):
    service = EvidenceCardService(db)
    try:
        service._scope(report_id, None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    service.refresh_stale(report_id)
    row = db.query(IndustryEvidenceCard).filter(
        IndustryEvidenceCard.report_id == report_id,
        IndustryEvidenceCard.evidence_code == evidence_code,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="evidence_not_found")
    return evidence_card_to_dict(row)


@router.get("/industry/reports/{report_id}/evidence-runs")
def list_industry_evidence_runs(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    if not db.query(IndustryReport).filter(IndustryReport.id == report_id).first():
        raise HTTPException(status_code=404, detail="report_not_found")
    rows = db.query(IndustryEvidenceExtractionRun).filter(
        IndustryEvidenceExtractionRun.report_id == report_id
    ).order_by(IndustryEvidenceExtractionRun.id.desc()).all()
    return [evidence_run_to_dict(row) for row in rows]


@router.post("/industry/reports/{report_id}/conflicts/detect")
def detect_industry_conflicts(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        run = ConflictDetectionService(db).detect(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictDetectionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return conflict_run_to_dict(run)


@router.get("/industry/reports/{report_id}/conflicts")
def list_industry_conflicts(
    report_id: int,
    conflict_type: str | None = Query(None),
    severity: str | None = Query(None),
    resolution_status: str | None = Query(None),
    source_id: int | None = Query(None),
    evidence_code: str | None = Query(None),
    db: Session = Depends(get_industry_db_with_query),
):
    try:
        rows = ConflictDetectionService(db).list_conflicts(
            report_id, conflict_type=conflict_type, severity=severity,
            resolution_status=resolution_status, source_id=source_id,
            evidence_code=evidence_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [conflict_to_dict(row) for row in rows]


@router.get("/industry/reports/{report_id}/conflicts/{conflict_code}")
def get_industry_conflict(
    report_id: int, conflict_code: str, db: Session = Depends(get_industry_db_with_query)
):
    try:
        row = ConflictDetectionService(db).get_conflict(report_id, conflict_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="conflict_not_found")
    return conflict_to_dict(row, include_members=True)


@router.patch("/industry/reports/{report_id}/conflicts/{conflict_code}")
def resolve_industry_conflict(
    report_id: int, conflict_code: str, body: ConflictResolutionRequest,
    db: Session = Depends(get_industry_db_with_query),
):
    try:
        row = ConflictDetectionService(db).resolve(
            report_id, conflict_code, body.resolution_status,
            body.resolution_note, body.selected_evidence_code,
        )
    except ValueError as exc:
        status = 404 if str(exc) in {"report_not_found", "conflict_not_found"} else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return conflict_to_dict(row, include_members=True)


@router.get("/industry/reports/{report_id}/conflict-runs")
def list_industry_conflict_runs(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    if not db.query(IndustryReport).filter(IndustryReport.id == report_id).first():
        raise HTTPException(status_code=404, detail="report_not_found")
    rows = db.query(IndustryConflictDetectionRun).filter(
        IndustryConflictDetectionRun.report_id == report_id
    ).order_by(IndustryConflictDetectionRun.id.desc()).all()
    return [conflict_run_to_dict(row) for row in rows]


@router.post("/industry/reports/{report_id}/grounded-runs/generate")
def generate_grounded_report_shadow(
    report_id: int, db: Session = Depends(get_industry_db_with_query),
    prompt_version: str = GROUNDED_REPORT_PROMPT_VERSION,
):
    if prompt_version not in {
        GROUNDED_REPORT_PROMPT_VERSION, STRUCTURED_GROUNDED_REPORT_PROMPT_VERSION,
    }:
        raise HTTPException(status_code=400, detail="unsupported_grounded_prompt_version")
    try:
        run = GroundedReportService(db).generate(report_id, prompt_version=prompt_version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GroundedReportError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return grounded_run_to_dict(run)


@router.get("/industry/reports/{report_id}/grounded-runs")
def list_grounded_report_runs(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    try:
        rows = GroundedReportService(db).list_runs(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [grounded_run_to_dict(row) for row in rows]


@router.get("/industry/reports/{report_id}/grounded-runs/{run_id}")
def get_grounded_report_run(
    report_id: int, run_id: int, db: Session = Depends(get_industry_db_with_query)
):
    try:
        row = GroundedReportService(db).get_run(report_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="grounded_run_not_found")
    return grounded_run_to_dict(row, include_candidate=True)


@router.get("/industry/reports/{report_id}/grounded-runs/{run_id}/validation")
def get_grounded_report_validation(
    report_id: int, run_id: int, db: Session = Depends(get_industry_db_with_query)
):
    try:
        row = GroundedReportService(db).get_run(report_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="grounded_run_not_found")
    return grounded_run_to_dict(row, include_validation=True)


@router.post(
    "/industry/reports/{report_id}/grounded-runs/{run_id}/promote",
    response_model=IndustryReportOut,
)
def promote_grounded_report_run(
    report_id: int, run_id: int, body: GroundedPromotionRequest,
    db: Session = Depends(get_industry_db_with_query),
):
    if get_settings().industry_report_generation_mode != "grounded":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "GROUNDED_MODE_DISABLED",
                "message": "当前服务端配置为legacy模式，不能晋升grounded候选。",
                "next_step": "switch server configuration and restart",
            },
        )
    try:
        return GroundedReportService(db).promote(
            report_id, run_id, promotion_type="manual", promotion_note=body.promotion_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GroundedPromotionError as exc:
        raise HTTPException(
            status_code=412,
            detail={"code": exc.code, "message": str(exc), "next_step": exc.next_step},
        ) from exc


def _citation_error(exc: CitationPresentationError, status_code: int = 412) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message, "next_step": exc.next_step},
    )


@router.get("/industry/reports/{report_id}/citations")
def get_industry_report_citations(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    report = db.get(IndustryReport, report_id)
    if not report or report.status != "completed":
        raise HTTPException(status_code=404, detail="report_not_found")
    if report.generation_mode != "grounded":
        return {
            "report_id": report_id,
            "generation_mode": report.generation_mode or "legacy",
            "citations": [],
            "warnings": [{
                "code": "LEGACY_REPORT_NOT_GROUNDED",
                "message": "该报告不是证据约束报告，没有可验证的引用详情。",
            }],
        }
    try:
        context = build_citation_context(db, report)
    except CitationPresentationError as exc:
        raise _citation_error(exc) from exc
    return {
        "report_id": report.id,
        "generation_mode": "grounded",
        "citations": context["citations"],
        "warnings": context["warnings"],
        "coverage": context["coverage"],
        "limitations": context["limitations"],
        "unresolved_conflicts": context["unresolved_conflicts"],
        "metadata": context["metadata"],
    }


@router.get("/industry/reports/{report_id}/citations/{evidence_code}")
def get_industry_report_citation(
    report_id: int, evidence_code: str, db: Session = Depends(get_industry_db_with_query),
):
    report = db.get(IndustryReport, report_id)
    if not report or report.status != "completed" or report.generation_mode != "grounded":
        raise HTTPException(status_code=404, detail="citation_not_found")
    try:
        context = build_citation_context(db, report)
        return citation_detail(context, evidence_code)
    except CitationPresentationError as exc:
        raise _citation_error(exc, status_code=404) from exc


@router.get(
    "/industry/reports/{report_id}/grounded-runs/{run_id}/citations/{evidence_code}"
)
def get_grounded_candidate_citation(
    report_id: int, run_id: int, evidence_code: str, db: Session = Depends(get_industry_db_with_query),
):
    report = db.get(IndustryReport, report_id)
    try:
        run = GroundedReportService(db).get_run(report_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="citation_not_found") from exc
    if (
        not report or report.status != "awaiting_approval" or not run
        or run.status != "validated" or not run.candidate_report_json
    ):
        raise HTTPException(status_code=404, detail="citation_not_found")
    try:
        context = build_citation_context(db, report, run.candidate_report_json)
        return citation_detail(context, evidence_code)
    except CitationPresentationError as exc:
        raise _citation_error(exc, status_code=404) from exc


@router.get("/industry/reports/{report_id}/data-sources")
def get_industry_data_sources(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    report = IndustryAnalysisService(db).get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    rows = list_industry_sources(db, report_id)
    return [
        {
            "id": r.id,
            "report_id": r.report_id,
            "name": r.name,
            "source_type": r.source_type,
            "origin_label": _industry_source_origin_label(r.source_type),
            "url": r.url,
            "original_filename": r.original_filename,
            "text_preview": (r.extracted_text or "")[:200],
            "chars": len(r.extracted_text or ""),
            "source_origin": r.source_origin,
            "evidence_grade": r.evidence_grade,
            "registry_state": source_registry_state(r),
            "is_full_text": r.is_full_text,
            "is_truncated": r.is_truncated,
            "parse_warning": r.parse_warning,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/industry/reports/{report_id}/data-sources/{source_id}")
def get_industry_data_source_detail(
    report_id: int, source_id: int, db: Session = Depends(get_industry_db_with_query)
):
    row = get_industry_source_by_id(db, source_id)
    if not row or row.report_id != report_id:
        raise HTTPException(status_code=404, detail="行业数据源不存在")
    text = row.extracted_text or ""
    return {
        "id": row.id,
        "report_id": row.report_id,
        "name": row.name,
        "source_type": row.source_type,
        "origin_label": _industry_source_origin_label(row.source_type),
        "url": row.url,
        "original_filename": row.original_filename,
        "extracted_text": text,
        "chars": len(text),
        "raw_content_hash": row.raw_content_hash,
        "extracted_text_hash": row.extracted_text_hash,
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "source_origin": row.source_origin,
        "source_publisher": row.source_publisher,
        "published_at": row.published_at,
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "is_full_text": row.is_full_text,
        "is_truncated": row.is_truncated,
        "parse_status": row.parse_status,
        "registry_state": source_registry_state(row),
        "parse_warning": row.parse_warning,
        "used_ocr": row.used_ocr,
        "page_count": row.page_count,
        "slide_count": row.slide_count,
        "sheet_count": row.sheet_count,
        "evidence_grade": row.evidence_grade,
        "chunk_count": len(row.chunks),
        "created_at": row.created_at.isoformat(),
    }


@router.get("/industry/reports/{report_id}/data-sources/{source_id}/chunks")
def get_industry_data_source_chunks(
    report_id: int, source_id: int, db: Session = Depends(get_industry_db_with_query)
):
    row = get_industry_source_by_id(db, source_id)
    if not row or row.report_id != report_id:
        raise HTTPException(status_code=404, detail="行业数据源不存在")
    return [
        {
            "id": chunk.id,
            "report_id": chunk.report_id,
            "source_id": chunk.source_id,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "locator": chunk.locator,
            "page_number": chunk.page_number,
            "slide_number": chunk.slide_number,
            "sheet_name": chunk.sheet_name,
            "cell_range": chunk.cell_range,
            "row_range": chunk.row_range,
            "paragraph_index": chunk.paragraph_index,
            "table_index": chunk.table_index,
            "table_row_index": chunk.table_row_index,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "content_hash": chunk.content_hash,
        }
        for chunk in list_industry_source_chunks(db, report_id, source_id)
    ]


@router.post("/industry/reports/{report_id}/data-sources/upload")
async def upload_industry_data_source(
    report_id: int,
    name: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_industry_db_with_query),
):
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，允许: {', '.join(SUPPORTED_EXTENSIONS)}")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB 上传上限")
    try:
        row = save_industry_file_source(db, report_id, name or filename, filename, content)
    except IndustryReportNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "行业数据源已上传", "id": row.id}


@router.post("/industry/reports/{report_id}/data-sources/url")
def add_industry_url_source(
    report_id: int, body: IndustryDataSourceUrlIn, db: Session = Depends(get_industry_db_with_query)
):
    try:
        row = save_industry_url_source(db, report_id, body.name, body.url)
    except IndustryReportNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "行业网址已添加", "id": row.id}


@router.delete("/industry/reports/{report_id}/data-sources/{source_id}")
def remove_industry_data_source(
    report_id: int, source_id: int, db: Session = Depends(get_industry_db_with_query)
):
    try:
        deleted = delete_industry_source(db, report_id, source_id)
    except IndustryReportNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {"message": "已删除"}


@router.get("/industry/export/docx/{report_id}")
def export_industry_docx(report_id: int, db: Session = Depends(get_industry_db_with_query)):
    row = db.query(IndustryReport).filter(IndustryReport.id == report_id).first()
    if not row or row.status != "completed":
        raise HTTPException(status_code=404, detail="报告不可用")
    out_dir = Path("data/exports")
    safe_name = "".join(
        "_" if char in '<>:"/\\|?*' else char
        for char in (row.report_name or row.industry_name)
    ).strip(" ._")[:180] or f"行业分析_{report_id}"
    filename = f"{safe_name}_v{row.version}.docx"
    citation_context = None
    if row.generation_mode == "grounded":
        try:
            citation_context = build_citation_context(db, row, enforce_export_gate=True)
        except CitationPresentationError as exc:
            raise _citation_error(exc) from exc
    path = export_industry_report_to_path(
        row,
        out_dir / filename,
        citation_context=citation_context,
        sources=list_industry_sources(db, report_id),
    )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# 切片 2：资讯 / 主体公开信息预警 API
# ---------------------------------------------------------------------------


@router.get("/news", response_model=list[NewsArticleOut])
def list_news_articles(
    report_date: date | None = Query(None),
    module_code: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(NewsArticle).order_by(NewsArticle.created_at.desc())
    if report_date:
        q = q.filter(NewsArticle.report_date == report_date)
    if module_code:
        q = q.filter(NewsArticle.module_code == module_code.upper())
    return q.limit(500).all()


@router.get("/entities", response_model=list[TargetEntityOut])
def list_target_entities(
    monitor_status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(TargetEntity).order_by(TargetEntity.name.asc())
    if monitor_status:
        q = q.filter(TargetEntity.monitor_status == monitor_status)
    return q.all()


@router.get("/entities/{entity_id}", response_model=TargetEntityOut)
def get_target_entity(entity_id: int, db: Session = Depends(get_db)):
    row = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="主体不存在")
    return row


@router.get("/entities/{entity_id}/risks", response_model=list[EntityRiskOut])
def list_entity_risks(
    entity_id: int,
    report_date: date | None = Query(None),
    include_demo: bool = Query(False, description="是否包含显式演示数据"),
    db: Session = Depends(get_db),
):
    if not db.query(TargetEntity).filter(TargetEntity.id == entity_id).first():
        raise HTTPException(status_code=404, detail="主体不存在")
    q = (
        db.query(EntityRisk)
        .filter(EntityRisk.entity_id == entity_id)
        .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
    )
    if not include_demo:
        q = q.filter(EntityRisk.provenance != "demo")
    if report_date:
        q = q.filter(EntityRisk.report_date == report_date)
    return q.limit(200).all()


@router.get("/entities/{entity_id}/source-catalog")
def get_entity_source_catalog(entity_id: int, db: Session = Depends(get_db)):
    entity = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="主体不存在")
    profile = configured_entity_catalog().find(
        (entity.name, entity.display_name, entity.aliases)
    )
    return {
        "entity_id": entity.id,
        "entity_name": entity.display_name or entity.name,
        "sources": [source.as_dict() for source in profile.sources if source.enabled]
        if profile
        else [],
    }


@router.get("/entities/{entity_id}/export/docx")
def export_entity_assessment_docx(
    entity_id: int,
    report_date: date | None = Query(None, description="评估日期，缺省为今天"),
    db: Session = Depends(get_db),
):
    """导出当前主体近三个月《企业公开信息风险监测简报》。"""
    entity = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="主体不存在")
    rd = report_date or tokyo_today()
    cutoff = news_lookback_start(rd)
    risks = (
        db.query(EntityRisk)
        .filter(
            EntityRisk.entity_id == entity_id,
            EntityRisk.report_date >= cutoff,
            EntityRisk.report_date <= rd,
            EntityRisk.provenance != "demo",
        )
        .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
        .limit(200)
        .all()
    )
    profile = configured_entity_catalog().find(
        (entity.name, entity.display_name, entity.aliases)
    )
    risks = [
        risk
        for risk in risks
        if is_monitored_public_event(risk, entity=entity, profile=profile)
    ]
    display = entity.display_name or entity.name
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in display).strip() or "entity"
    filename = f"企业公开信息风险监测简报_{safe_name}_{rd.isoformat()}.docx"
    out_path = Path("data/exports") / filename
    export_entity_assessment_to_path(
        entity,
        report_date=rd,
        risks=risks,
        output_path=out_path,
    )
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@router.post("/admin/seed-entity-demo")
def seed_entity_demo(force: bool = False, db: Session = Depends(get_db)):
    from app.services.entity_mock import seed_entity_demo_data

    stats = seed_entity_demo_data(db, force=force)
    return {"message": "主体演示数据已写入", **stats}


@router.get("/credit-levels")
def list_credit_levels():
    return {"levels": CREDIT_LEVELS}


# ---------------------------------------------------------------------------
# 域名规则与导出
# ---------------------------------------------------------------------------


@router.get("/domains/whitelist")
def get_whitelist(db: Session = Depends(get_db)):
    rows = db.query(DomainWhitelist).filter(DomainWhitelist.is_active.is_(True)).all()
    return [{"id": r.id, "domain": r.domain, "module_code": r.module_code, "note": r.note} for r in rows]


@router.post("/domains/whitelist")
def add_whitelist(body: DomainRuleIn, db: Session = Depends(get_db)):
    if db.query(DomainWhitelist).filter(DomainWhitelist.domain == body.domain).first():
        raise HTTPException(status_code=400, detail="域名已存在于白名单")
    row = DomainWhitelist(domain=body.domain, module_code=body.module_code, note=body.note)
    db.add(row)
    db.commit()
    return {"message": "白名单已添加", "id": row.id}


@router.get("/domains/blacklist")
def get_blacklist(db: Session = Depends(get_db)):
    rows = db.query(DomainBlacklist).filter(DomainBlacklist.is_active.is_(True)).all()
    return [{"id": r.id, "domain": r.domain, "reason": r.reason} for r in rows]


@router.post("/domains/blacklist")
def add_blacklist(body: DomainRuleIn, db: Session = Depends(get_db)):
    if db.query(DomainBlacklist).filter(DomainBlacklist.domain == body.domain).first():
        raise HTTPException(status_code=400, detail="域名已存在于黑名单")
    row = DomainBlacklist(domain=body.domain, reason=body.reason or body.note)
    db.add(row)
    db.commit()
    return {"message": "黑名单已添加", "id": row.id}


@router.post("/admin/seed-domains")
def seed_domains(db: Session = Depends(get_db)):
    seed_default_domains(db)
    return {"message": "默认域名规则已写入"}


@router.get("/export/docx")
def export_docx(
    report_date: date = Query(..., description="报告日期"),
    module_codes: str | None = Query(
        None, description="逗号分隔，如 B,C,D（中东日报/日本企业/宏观）；缺省导出全部"
    ),
    window_hours: int = Query(
        NEWS_WINDOW_HOURS_24, ge=1, le=168, description="时效窗：24 或 168（7×24）"
    ),
    db: Session = Depends(get_db),
):
    codes: list[str] | None = None
    if module_codes:
        codes = [c.strip().upper() for c in module_codes.split(",") if c.strip()]
        invalid = [c for c in codes if c not in MODULE_CODES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"未知模块: {', '.join(invalid)}")

    q = db.query(DailyRiskEntry).filter(
        DailyRiskEntry.report_date == report_date,
        DailyRiskEntry.window_hours == window_hours,
    )
    if codes:
        q = q.filter(DailyRiskEntry.module_code.in_(codes))
    entries = q.order_by(DailyRiskEntry.module_code, DailyRiskEntry.id).all()

    out_dir = Path("data/exports")
    suffix = "_" + "".join(codes) if codes else ""
    label = news_window_label(window_hours).replace("×", "x")
    filename = f"{label}核心新闻情报汇总_{report_date.isoformat()}{suffix}.docx"
    path = export_daily_report_to_path(
        entries,
        report_date,
        out_dir / filename,
        module_codes=codes,
        window_hours=window_hours,
    )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@router.get("/export/pdf")
def export_pdf(
    report_date: date = Query(...),
    module_codes: str | None = Query(None),
    window_hours: int = Query(NEWS_WINDOW_HOURS_24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    codes = [c.strip().upper() for c in (module_codes or "").split(",") if c.strip()] or None
    if codes and any(c not in MODULE_CODES for c in codes):
        raise HTTPException(status_code=400, detail="未知模块")
    q = db.query(DailyRiskEntry).filter(DailyRiskEntry.report_date == report_date, DailyRiskEntry.window_hours == window_hours)
    if codes:
        q = q.filter(DailyRiskEntry.module_code.in_(codes))
    path = export_daily_pdf(q.order_by(DailyRiskEntry.module_code, DailyRiskEntry.id).all(), report_date, Path("data/exports") / f"每日风险情报汇总_{report_date.isoformat()}.pdf")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


# ---------------------------------------------------------------------------
# 国际评级
# ---------------------------------------------------------------------------


@router.get("/intl-ratings", response_model=IntlRatingsSnapshotOut)
def get_intl_ratings():
    """返回最新评级快照；若尚未跑流水线则为占位行。"""
    snap = load_snapshot()
    rows = []
    for r in snap.get("rows") or []:
        try:
            rows.append(IntlRatingRowOut.model_validate(r))
        except Exception:
            continue
    return IntlRatingsSnapshotOut(
        updated_at=snap.get("updated_at"),
        source=str(snap.get("source") or ""),
        message=str(snap.get("message") or ""),
        running=bool(snap.get("running")),
        rows=rows,
    )


@router.post("/intl-ratings/refresh", response_model=IntlRatingsRefreshOut)
def refresh_intl_ratings(
    limit: int = Query(0, ge=0, le=500, description="0=全部；调试可限制家数"),
    quick: bool = Query(
        True,
        description="true=跳过 Playwright/OpenFIGI/SEC（适合网页手动更新）",
    ),
):
    """后台启动评级流水线刷新。"""
    started = start_refresh_job(limit=limit, quick=quick)
    return IntlRatingsRefreshOut(
        job_id=started["job_id"],
        status=started["status"],
        message=started["message"],
        accepted=bool(started.get("accepted", True)),
    )


@router.get("/intl-ratings/jobs/{job_id}", response_model=IntlRatingsJobOut)
def intl_ratings_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return IntlRatingsJobOut.model_validate(job)
