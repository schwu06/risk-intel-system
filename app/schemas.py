"""Pydantic 请求/响应模型。"""

from datetime import date, datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


EvidenceClaimType = Literal["fact", "reported_opinion", "forecast", "inference"]
EvidenceRiskTag = Literal[
    "market_size", "market_growth", "policy_regulation", "business_model",
    "revenue_model", "profitability", "cost_capex", "financing_debt",
    "project_pipeline", "capacity_output", "technology_performance",
    "supplier_dependency", "customer_concentration", "competition_market_share",
    "pricing", "safety_accident", "legal_litigation", "environmental",
    "governance", "management_guidance", "risk_event", "other_material_information",
]


class EvidenceCandidate(BaseModel):
    original_quote: str = Field(..., min_length=1)
    normalized_claim: str = Field(..., min_length=1)
    claim_type: EvidenceClaimType
    subject: Optional[str] = None
    metric_name: Optional[str] = None
    raw_value: Optional[str] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    period: Optional[str] = None
    as_of_date: Optional[str] = None
    speaker: Optional[str] = None
    importance_score: int = Field(..., ge=1, le=5)
    importance_reason: Optional[str] = None
    risk_tags: list[EvidenceRiskTag] = Field(default_factory=list)
    extraction_confidence: Optional[float] = Field(None, ge=0, le=1)

    model_config = {"extra": "forbid"}


class EvidenceCandidatePayload(BaseModel):
    candidates: list[EvidenceCandidate] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class EvidenceExtractRequest(BaseModel):
    source_id: Optional[int] = Field(None, ge=1)


ConflictResolutionStatus = Literal[
    "resolved_disclosed", "resolved_selected", "not_a_conflict"
]


class ConflictResolutionRequest(BaseModel):
    resolution_status: ConflictResolutionStatus
    resolution_note: str = Field(..., min_length=1, max_length=4000)
    selected_evidence_code: Optional[str] = Field(None, pattern=r"^E\d+$")

    model_config = {"extra": "forbid"}


class GroundedReportSection(BaseModel):
    heading: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)

    model_config = {"extra": "forbid"}


class GroundedReportCitation(BaseModel):
    evidence_code: str = Field(..., pattern=r"^E\d+$")
    location: str = Field(..., min_length=1, max_length=256)

    model_config = {"extra": "forbid"}


class GroundedReportMetric(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    value: str = Field(..., min_length=1, max_length=256)
    evidence_code: str = Field(..., pattern=r"^E\d+$")

    model_config = {"extra": "forbid"}


class GroundedReportCandidate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    sections: list[GroundedReportSection] = Field(default_factory=list)
    summary: str = ""
    risk_outlook: str = ""
    key_metrics: list[GroundedReportMetric] = Field(default_factory=list)
    citations: list[GroundedReportCitation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    evidence_coverage: dict = Field(default_factory=dict)
    generation_metadata: dict = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class StructuredEvidenceFactSentence(BaseModel):
    sentence_type: Literal["evidence_fact"]
    text: str = Field(..., min_length=1, max_length=4000)
    evidence_codes: list[str] = Field(..., min_length=1, max_length=20)

    model_config = {"extra": "forbid"}


class StructuredBoundedAnalysisSentence(BaseModel):
    sentence_type: Literal["bounded_analysis"]
    text: str = Field(..., min_length=1, max_length=4000)
    evidence_codes: list[str] = Field(..., min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)

    model_config = {"extra": "forbid"}


StructuredReportSentence = Union[
    StructuredEvidenceFactSentence, StructuredBoundedAnalysisSentence,
]


class StructuredGroundedReportSection(BaseModel):
    heading: str = Field(..., min_length=1, max_length=500)
    sentences: list[StructuredReportSentence] = Field(..., min_length=1, max_length=200)

    model_config = {"extra": "forbid"}


class StructuredGroundedReportCandidate(BaseModel):
    """V2 shadow-only candidate; citations and audit fields are compiler-owned."""

    title: str = Field(..., min_length=1, max_length=500)
    structured_sections: list[StructuredGroundedReportSection] = Field(default_factory=list)
    key_metrics: list[GroundedReportMetric] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class GroundedPromotionRequest(BaseModel):
    promotion_note: str = Field(..., min_length=1, max_length=4000)

    model_config = {"extra": "forbid"}


class RiskEntryOut(BaseModel):
    id: int
    report_date: date
    module_code: str
    title: str
    related_company: Optional[str] = None
    risk_category: Optional[str] = None
    risk_level: str
    summary: str
    impact_analysis: Optional[str] = None
    source_url: Optional[str] = None
    target_entity: Optional[str] = None
    country_or_region: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DomainRuleIn(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255)
    module_code: Optional[str] = None
    note: Optional[str] = None
    reason: Optional[str] = None


class PipelineRunRequest(BaseModel):
    report_date: Optional[date] = None
    module_codes: Optional[list[str]] = None
    # None = 跟随配置 pipeline_async_default
    async_mode: Optional[bool] = None
    # 主体评估页：仅采集/更新指定主体
    entity_id: Optional[int] = None
    # 新闻时效窗：24=近24小时，168=近7×24小时；缺省跟配置 news_window_hours
    window_hours: Optional[int] = Field(None, ge=1, le=168)


class PipelineRunResponse(BaseModel):
    report_date: date
    results: dict[str, int] = Field(default_factory=dict)
    message: str
    job_id: Optional[str] = None
    async_mode: bool = False
    status: Optional[str] = None
    entity_id: Optional[int] = None
    window_hours: Optional[int] = None


class PipelineJobStatusOut(BaseModel):
    job_id: str
    report_date: str
    module_codes: list[str]
    status: str
    results: dict[str, int] = Field(default_factory=dict)
    funnel: Optional[dict] = None
    message: str = ""
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    running_job_id: Optional[str] = None
    snapshot: Optional[dict] = None
    entity_id: Optional[int] = None
    window_hours: Optional[int] = None
    scope: Optional[str] = None


class ManualEntryIn(BaseModel):
    module_code: str
    report_date: date
    entries: list[dict[str, str]]


class DataSourceOut(BaseModel):
    id: int
    module_code: str
    entity_id: Optional[int] = None
    name: str
    source_type: str
    original_filename: Optional[str] = None
    url: Optional[str] = None
    priority: int
    created_at: datetime
    text_preview: Optional[str] = None
    extracted_text: Optional[str] = None
    chars: int = 0

    model_config = {"from_attributes": True}


class RssSourceItemOut(BaseModel):
    """近 24 小时 RSS 管道抓取条目（API 查询用）。"""

    id: str
    title: str
    source_name: str
    published_at: Optional[datetime] = None
    type: str = "rss"
    is_selected: bool = True
    url: Optional[str] = None


class DataSourceUrlIn(BaseModel):
    name: str
    url: str = Field(..., min_length=8)
    priority: int = 0
    module_code: Optional[str] = None  # 兼容旧客户端，忽略
    entity_id: Optional[int] = None


class IndustryAnalysisRequest(BaseModel):
    industry_name: str = Field(..., min_length=1, max_length=256)
    company_name: Optional[str] = Field(None, max_length=256)
    supplement_search: bool = True


class IndustryDataSourceUrlIn(BaseModel):
    name: str = Field(..., max_length=256)
    url: str = Field(..., min_length=8)


class IndustryReportRenameIn(BaseModel):
    report_name: str = Field(..., min_length=1, max_length=256)


class IndustryReportOut(BaseModel):
    id: int
    parent_report_id: Optional[int] = None
    root_report_id: Optional[int] = None
    version: int = 1
    report_name: Optional[str] = None
    industry_name: str
    company_name: Optional[str] = None
    status: str
    supplement_search: bool = True
    report_html: Optional[str] = None
    chart_specs: Optional[str] = None
    source_manifest_json: Optional[str] = None
    error_message: Optional[str] = None
    generation_mode: Optional[str] = None
    grounded_run_id: Optional[int] = None
    prompt_version: Optional[str] = None
    evidence_snapshot_hash: Optional[str] = None
    conflict_snapshot_hash: Optional[str] = None
    citation_validation_status: Optional[str] = None
    promoted_at: Optional[datetime] = None
    promotion_type: Optional[str] = None
    promotion_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NewsArticleOut(BaseModel):
    id: int
    report_date: date
    module_code: str
    category_tag: Optional[str] = None
    country_or_region: Optional[str] = None
    target_entity: Optional[str] = None
    title: str
    related_company: Optional[str] = None
    risk_category: Optional[str] = None
    risk_level: str
    summary: str
    impact_analysis: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TargetEntityOut(BaseModel):
    id: int
    name: str
    display_name: Optional[str] = None
    aliases: Optional[str] = None
    industry: Optional[str] = None
    region: Optional[str] = None
    monitor_status: str
    credit_level: str
    notes: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class EntityRiskOut(BaseModel):
    id: int
    entity_id: int
    report_date: date
    title: str
    risk_category: Optional[str] = None
    risk_level: str
    summary: str
    impact_analysis: Optional[str] = None
    source_url: Optional[str] = None
    related_company: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreditUpdateOut(BaseModel):
    id: int
    entity_id: int
    previous_level: str
    new_level: str
    reason: Optional[str] = None
    trigger_risk_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IntlRatingRowOut(BaseModel):
    id: str
    issuer: str
    category: str = ""
    moodys: str = "NR"
    sp: str = "NR"
    fitch: str = "NR"
    loss: str = ""
    listed: str = ""
    delisted: str = ""
    priceDrop: str = ""
    noRatingReason: str = ""
    ratingChanged: str = ""
    rssUrl: str = ""


class IntlRatingsSnapshotOut(BaseModel):
    updated_at: Optional[str] = None
    source: str = ""
    message: str = ""
    running: bool = False
    rows: list[IntlRatingRowOut] = Field(default_factory=list)


class IntlRatingsRefreshOut(BaseModel):
    job_id: str
    status: str
    message: str
    accepted: bool = True


class IntlRatingsJobOut(BaseModel):
    job_id: str
    status: str
    message: str = ""
    total: int = 0
    done: int = 0
    error: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    excel: str = ""
