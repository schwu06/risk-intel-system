"""Pydantic 请求/响应模型。"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


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


class PipelineRunResponse(BaseModel):
    report_date: date
    results: dict[str, int] = Field(default_factory=dict)
    message: str
    job_id: Optional[str] = None
    async_mode: bool = False
    status: Optional[str] = None


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


class ManualEntryIn(BaseModel):
    module_code: str
    report_date: date
    entries: list[dict[str, str]]


class DataSourceOut(BaseModel):
    id: int
    module_code: str
    name: str
    source_type: str
    original_filename: Optional[str] = None
    url: Optional[str] = None
    priority: int
    created_at: datetime
    text_preview: Optional[str] = None
    extracted_text: Optional[str] = None

    model_config = {"from_attributes": True}


class DataSourceUrlIn(BaseModel):
    name: str
    url: str = Field(..., min_length=8)
    priority: int = 0
    module_code: Optional[str] = None  # 兼容旧客户端，忽略


class IndustryAnalysisRequest(BaseModel):
    industry_name: str = Field(..., min_length=1, max_length=256)
    company_name: Optional[str] = Field(None, max_length=256)
    supplement_search: bool = True


class IndustryDataSourceUrlIn(BaseModel):
    industry_name: str
    name: str
    url: str = Field(..., min_length=8)


class IndustryReportOut(BaseModel):
    id: int
    industry_name: str
    company_name: Optional[str] = None
    status: str
    report_html: Optional[str] = None
    chart_specs: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime

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
