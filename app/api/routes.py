"""FastAPI 路由。"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import CREDIT_LEVELS, MODULE_CODES, NEWS_WINDOW_HOURS_24, get_settings, news_window_label
from app.database.models import (
    CreditUpdate,
    DailyRiskEntry,
    DomainBlacklist,
    DomainWhitelist,
    EntityRisk,
    IndustryReport,
    NewsArticle,
    SearchLog,
    TargetEntity,
)
from app.database.session import get_db
from app.exporters.docx_report import (
    export_daily_report_to_path,
    export_entity_assessment_to_path,
    export_industry_report_to_path,
)
from app.schemas import (
    CreditUpdateOut,
    DataSourceOut,
    DataSourceUrlIn,
    DomainRuleIn,
    EntityRiskOut,
    IndustryAnalysisRequest,
    IndustryDataSourceUrlIn,
    IndustryReportOut,
    ManualEntryIn,
    NewsArticleOut,
    PipelineJobStatusOut,
    PipelineRunRequest,
    PipelineRunResponse,
    RiskEntryOut,
    RssSourceItemOut,
    TargetEntityOut,
)
from app.services.data_source_parser import SUPPORTED_EXTENSIONS
from app.services.data_source_service import (
    delete_industry_source,
    delete_module_source,
    get_source_by_id,
    list_all_sources,
    list_industry_sources,
    list_module_sources,
    save_industry_file_source,
    save_industry_url_source,
    save_module_file_source,
    save_module_url_source,
)
from app.services.domain_rules import seed_default_domains
from app.services.industry_analysis import IndustryAnalysisService
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


@router.post("/data-sources/upload")
async def upload_module_data_source(
    name: str = Form(""),
    priority: int = Form(0),
    file: UploadFile = File(...),
    module_code: str = Form(""),  # 兼容旧表单，忽略
    entity_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，允许: {', '.join(SUPPORTED_EXTENSIONS)}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if entity_id is not None and not db.query(TargetEntity).filter(TargetEntity.id == entity_id).first():
        raise HTTPException(status_code=404, detail="主体不存在")
    try:
        row = save_module_file_source(
            db, None, name or filename, filename, content, priority, entity_id=entity_id
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _source_mutation_payload(row, message="数据源已上传")


@router.post("/data-sources/url")
def add_module_url_source(body: DataSourceUrlIn, db: Session = Depends(get_db)):
    if body.entity_id is not None and not db.query(TargetEntity).filter(
        TargetEntity.id == body.entity_id
    ).first():
        raise HTTPException(status_code=404, detail="主体不存在")
    try:
        row = save_module_url_source(
            db, None, body.name, body.url, body.priority, entity_id=body.entity_id
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


@router.post("/industry/analyze", response_model=IndustryReportOut)
def run_industry_analysis(body: IndustryAnalysisRequest, db: Session = Depends(get_db)):
    svc = IndustryAnalysisService(db)
    try:
        report = svc.run_analysis(
            body.industry_name,
            company_name=body.company_name,
            supplement_search=body.supplement_search,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return report


@router.get("/industry/reports", response_model=list[IndustryReportOut])
def list_industry_reports(limit: int = 20, db: Session = Depends(get_db)):
    rows = IndustryAnalysisService(db).list_reports(limit=limit)
    return rows


@router.get("/industry/reports/{report_id}", response_model=IndustryReportOut)
def get_industry_report(report_id: int, db: Session = Depends(get_db)):
    row = IndustryAnalysisService(db).get_report(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    return row


@router.get("/industry/data-sources/{industry_name}")
def get_industry_data_sources(industry_name: str, db: Session = Depends(get_db)):
    rows = list_industry_sources(db, industry_name)
    return [
        {
            "id": r.id,
            "industry_name": r.industry_name,
            "name": r.name,
            "source_type": r.source_type,
            "url": r.url,
            "original_filename": r.original_filename,
            "text_preview": (r.extracted_text or "")[:200],
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/industry/data-sources/upload")
async def upload_industry_data_source(
    industry_name: str = Form(...),
    name: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，允许: {', '.join(SUPPORTED_EXTENSIONS)}")
    content = await file.read()
    try:
        row = save_industry_file_source(db, industry_name, name or filename, filename, content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "行业数据源已上传", "id": row.id}


@router.post("/industry/data-sources/url")
def add_industry_url_source(body: IndustryDataSourceUrlIn, db: Session = Depends(get_db)):
    try:
        row = save_industry_url_source(db, body.industry_name, body.name, body.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "行业网址已添加", "id": row.id}


@router.delete("/industry/data-sources/{source_id}")
def remove_industry_data_source(source_id: int, db: Session = Depends(get_db)):
    if not delete_industry_source(db, source_id):
        raise HTTPException(status_code=404, detail="数据源不存在")
    return {"message": "已删除"}


@router.get("/industry/export/docx/{report_id}")
def export_industry_docx(report_id: int, db: Session = Depends(get_db)):
    row = db.query(IndustryReport).filter(IndustryReport.id == report_id).first()
    if not row or row.status != "completed":
        raise HTTPException(status_code=404, detail="报告不可用")
    out_dir = Path("data/exports")
    filename = f"行业分析_{row.industry_name}_{report_id}.docx"
    path = export_industry_report_to_path(row, out_dir / filename)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# 切片 2：资讯 / 主体 / 授信 API
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
    db: Session = Depends(get_db),
):
    if not db.query(TargetEntity).filter(TargetEntity.id == entity_id).first():
        raise HTTPException(status_code=404, detail="主体不存在")
    q = (
        db.query(EntityRisk)
        .filter(EntityRisk.entity_id == entity_id)
        .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
    )
    if report_date:
        q = q.filter(EntityRisk.report_date == report_date)
    return q.limit(200).all()


@router.get("/entities/{entity_id}/credit-updates", response_model=list[CreditUpdateOut])
def list_credit_updates(entity_id: int, limit: int = 50, db: Session = Depends(get_db)):
    if not db.query(TargetEntity).filter(TargetEntity.id == entity_id).first():
        raise HTTPException(status_code=404, detail="主体不存在")
    return (
        db.query(CreditUpdate)
        .filter(CreditUpdate.entity_id == entity_id)
        .order_by(CreditUpdate.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )


@router.get("/entities/{entity_id}/export/docx")
def export_entity_assessment_docx(
    entity_id: int,
    report_date: date | None = Query(None, description="评估日期，缺省为今天"),
    db: Session = Depends(get_db),
):
    """导出当前主体的《企业主体风险评估简报》。"""
    entity = db.query(TargetEntity).filter(TargetEntity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="主体不存在")
    rd = report_date or date.today()
    risks = (
        db.query(EntityRisk)
        .filter(EntityRisk.entity_id == entity_id)
        .order_by(EntityRisk.report_date.desc(), EntityRisk.id.desc())
        .limit(100)
        .all()
    )
    credit_logs = (
        db.query(CreditUpdate)
        .filter(CreditUpdate.entity_id == entity_id)
        .order_by(CreditUpdate.created_at.desc())
        .limit(50)
        .all()
    )
    display = entity.display_name or entity.name
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in display).strip() or "entity"
    filename = f"企业主体风险评估简报_{safe_name}_{rd.isoformat()}.docx"
    out_path = Path("data/exports") / filename
    export_entity_assessment_to_path(
        entity,
        report_date=rd,
        risks=risks,
        credit_logs=credit_logs,
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
