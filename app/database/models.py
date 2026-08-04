"""SQLAlchemy ORM 模型。"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DomainWhitelist(Base):
    __tablename__ = "domain_whitelist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    module_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DomainBlacklist(Base):
    __tablename__ = "domain_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ModuleDataSource(Base):
    """权威数据源（文件/模板/网址）。

    - entity_id 为空：全站共用（风险日报等）
    - entity_id 有值：主体评估页当前主体的专属参考素材
    """

    __tablename__ = "module_data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("target_entities.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # file, url, template
    file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class IndustryDataSource(Base):
    """行业分析模块独立数据源。"""

    __tablename__ = "industry_data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class IndustryAnalysisReport(Base):
    """行业/授信分析报告（与常规日报解耦）。"""

    __tablename__ = "industry_analysis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    report_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    report_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chart_specs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SearchLog(Base):
    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    domains_whitelist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domains_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    entries: Mapped[list["DailyRiskEntry"]] = relationship(back_populates="search_log")


class ReportRun(Base):
    __tablename__ = "report_runs"
    __table_args__ = (UniqueConstraint("report_date", "module_code", name="uq_report_run_date_module"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    entry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # collect | analyze | publish | done
    phase: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    funnel_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    kept_previous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PipelineJob(Base):
    """异步流水线任务（前端轮询）。"""

    __tablename__ = "pipeline_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    module_codes: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    results_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    funnel_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 任务启动时冻结的权威数据源等快照；运行中上传/删除不影响本次采集
    snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineArtifact(Base):
    """collect 阶段中间落盘，analyze 可独立重试。"""

    __tablename__ = "pipeline_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)  # collect | analyze
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LlmResponseCache(Base):
    """DeepSeek 结构化结果缓存。"""

    __tablename__ = "llm_response_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    material_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ContentFingerprint(Base):
    """已发布内容指纹，跨次运行去重。"""

    __tablename__ = "content_fingerprints"
    __table_args__ = (
        UniqueConstraint("module_code", "fingerprint", name="uq_content_fp_module"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    report_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DailyRiskEntry(Base):
    __tablename__ = "daily_risk_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    country_or_region: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    target_entity: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    related_company: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    risk_category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    source_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    pillar_or_topic: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    structured_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    search_log_id: Mapped[Optional[int]] = mapped_column(ForeignKey("search_logs.id"), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    search_log: Mapped[Optional[SearchLog]] = relationship(back_populates="entries")


# ---------------------------------------------------------------------------
# 切片 2：三页业务表（与旧表共存，同一数据库）
# ---------------------------------------------------------------------------


class NewsArticle(Base):
    """风险日报资讯（中东日报 / 日本重点大型企业 / 每日宏观与市场情报）。"""

    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    module_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    category_tag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    country_or_region: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    target_entity: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    related_company: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    risk_category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    source_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    structured_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    legacy_entry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("daily_risk_entries.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TargetEntity(Base):
    """主体评估 — 重点监控主体。"""

    __tablename__ = "target_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    monitor_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    credit_level: Mapped[str] = mapped_column(String(16), default="正常", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    risks: Mapped[list["EntityRisk"]] = relationship(back_populates="entity")
    credit_updates: Mapped[list["CreditUpdate"]] = relationship(back_populates="entity")


class EntityRisk(Base):
    """主体风险事件（司法/经营/舆情等）。"""

    __tablename__ = "entity_risks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("target_entities.id"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    risk_category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    related_company: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    structured_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    legacy_entry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("daily_risk_entries.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    entity: Mapped["TargetEntity"] = relationship(back_populates="risks")


class CreditUpdate(Base):
    """授信等级变更日志：正常 | 关注 | 预警 | 高风险。"""

    __tablename__ = "credit_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("target_entities.id"), nullable=False, index=True)
    previous_level: Mapped[str] = mapped_column(String(16), nullable=False)
    new_level: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_risk_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("entity_risks.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    entity: Mapped["TargetEntity"] = relationship(back_populates="credit_updates")


class IndustryReport(Base):
    """深度研报 — 行业/企业长篇结构化报告。"""

    __tablename__ = "industry_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    report_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    report_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chart_specs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    legacy_report_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("industry_analysis_reports.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
