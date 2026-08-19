"""行业/授信分析独立模块。"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from html import escape
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.models import IndustryReport
from app.services.chart_generator import extract_and_build_charts, to_echarts_option
from app.services.data_source_service import (
    INDUSTRY_UPLOAD_ROOT,
    append_industry_network_search_sources,
    build_industry_authoritative_text,
    clone_industry_sources,
    list_industry_sources,
)
from app.services.deepseek_analyzer import (
    GROUNDED_REPORT_PROMPT_VERSION,
    DeepSeekAnalyzer,
    LEGACY_INDUSTRY_PROMPT_VERSION,
)
from app.services.gemini_analyzer import gemini_for
from app.services.grounded_readiness import check_grounded_readiness
from app.services.grounded_report import (
    GroundedPromotionError,
    GroundedReportError,
    GroundedReportService,
)
from app.services.mita_search import MitaSearchClient

logger = logging.getLogger(__name__)


class IndustryGenerationError(RuntimeError):
    def __init__(self, code: str, message: str, next_step: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_step = next_step

GENERIC_TEMPLATE = """
通用行业分析框架：
1. 行业概况与产业链
2. 市场规模与竞争格局
3. 主要企业财务与信用状况
4. 政策监管与合规环境
5. 关键风险因素
6. 结论与授信建议
"""


def _source_kind_label(source: Any) -> str:
    origin = str(getattr(source, "source_origin", None) or "")
    source_type = str(getattr(source, "source_type", None) or "")
    if origin == "network_search" or source_type == "network_search":
        return "网络搜索"
    if origin == "customer_url" or source_type == "url":
        return "网址"
    if origin == "customer_file" or source_type == "file":
        return "文件"
    return source_type or "数据源"


def _external_href(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return "https:" + value
    return "https://" + value


def source_list_html(sources: Optional[list[Any]] = None) -> str:
    """按已保存数据源生成来源列表，不依赖模型是否输出该章节。"""
    parts = ['<section class="report-section report-source-list"><h2>来源列表</h2>']
    items = list(sources or [])
    if not items:
        parts.append('<p class="hint">本次生成未写入可用数据源。</p></section>')
        return "".join(parts)
    parts.append('<ol class="report-source-list-items">')
    for src in items:
        name = escape(str(getattr(src, "name", None) or "未命名来源"))
        kind = escape(_source_kind_label(src))
        href = _external_href(str(getattr(src, "url", None) or ""))
        if href:
            parts.append(
                f'<li><span class="source-kind">{kind}</span> '
                f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer">{name}</a></li>'
            )
        else:
            parts.append(f'<li><span class="source-kind">{kind}</span> {name}</li>')
    parts.append("</ol></section>")
    return "".join(parts)


def report_json_to_html(report: dict[str, Any], sources: Optional[list[Any]] = None) -> str:
    parts: list[str] = []
    title = escape(str(report.get("title") or "行业分析报告"))
    parts.append(f'<h1 class="report-title">{title}</h1>')
    if report.get("summary"):
        parts.append(f'<section class="report-section"><h2>执行摘要</h2><p>{escape(str(report["summary"]))}</p></section>')
    for sec in report.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        heading = escape(str(sec.get("heading") or ""))
        content = escape(str(sec.get("content") or "")).replace("\n", "<br/>")
        parts.append(f'<section class="report-section"><h2>{heading}</h2><p>{content}</p></section>')
    if report.get("risk_outlook"):
        parts.append(
            f'<section class="report-section"><h2>风险展望</h2><p>{escape(str(report["risk_outlook"]))}</p></section>'
        )
    if sources is not None:
        parts.append(source_list_html(sources))
    return "\n".join(parts)


def build_report_chart_specs(
    report: dict[str, Any], source_text: str = "",
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Build existing chart payloads without requiring source text in grounded mode."""
    combined_text = source_text + "\n" + json.dumps(report, ensure_ascii=False)
    specs, chart_json = extract_and_build_charts(combined_text)
    metrics = report.get("key_metrics") or []
    if metrics and not specs:
        labels = [str(item.get("name", "")) for item in metrics if isinstance(item, dict)]
        values = []
        for item in metrics:
            if not isinstance(item, dict):
                continue
            try:
                values.append(float(str(item.get("value", "0")).replace("%", "")))
            except ValueError:
                values.append(0)
        if labels and values:
            spec = {
                "id": "chart_metrics", "type": "bar", "title": "关键指标",
                "labels": labels, "series": [{"name": "指标值", "data": values}],
            }
            specs = [spec]
            chart_json = json.dumps(
                [{"id": spec["id"], "option": to_echarts_option(spec)}], ensure_ascii=False,
            )
    return specs, chart_json


class IndustryAnalysisService:
    def __init__(
        self,
        db: Session,
        deepseek: Optional[DeepSeekAnalyzer] = None,
        mita: Optional[MitaSearchClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.db = db
        # 行业分析默认走 Gemini；测试可注入 FakeAnalyzer / DeepSeekAnalyzer
        self.deepseek = deepseek or gemini_for("industry")
        self.mita = mita or MitaSearchClient()
        self.settings = settings or get_settings()

    def create_draft(
        self,
        industry_name: str,
        company_name: Optional[str] = None,
        supplement_search: bool = True,
    ) -> IndustryReport:
        industry_name = industry_name.strip()
        if not industry_name:
            raise ValueError("行业名称不能为空")
        company_name = company_name.strip() if company_name else None
        row = IndustryReport(
            industry_name=industry_name,
            company_name=company_name,
            status="draft",
            supplement_search=supplement_search,
            version=1,
        )
        self.db.add(row)
        self.db.flush()
        row.root_report_id = row.id
        self.db.commit()
        self.db.refresh(row)
        return row

    def fork_report(self, report_id: int) -> IndustryReport:
        parent = self.get_report(report_id)
        if not parent:
            raise ValueError("报告不存在")
        if parent.status != "completed":
            raise ValueError("只有已完成的报告可以创建新版")
        root_id = parent.root_report_id or parent.id
        latest_version = (
            self.db.query(IndustryReport.version)
            .filter(IndustryReport.root_report_id == root_id)
            .order_by(IndustryReport.version.desc())
            .first()
        )
        version = (latest_version[0] if latest_version else parent.version) + 1
        row = IndustryReport(
            parent_report_id=parent.id,
            root_report_id=root_id,
            version=version,
            report_name=parent.report_name,
            industry_name=parent.industry_name,
            company_name=parent.company_name,
            status="draft",
            supplement_search=parent.supplement_search,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        try:
            clone_industry_sources(self.db, parent.id, row.id)
        except Exception:
            self.db.rollback()
            self.db.delete(row)
            self.db.commit()
            shutil.rmtree(INDUSTRY_UPLOAD_ROOT / str(row.id), ignore_errors=True)
            raise
        self.db.refresh(row)
        return row

    def rename_report(self, report_id: int, report_name: str) -> IndustryReport:
        row = self.get_report(report_id)
        if not row:
            raise ValueError("报告不存在")
        normalized = " ".join(report_name.split())
        if not normalized:
            raise ValueError("报告名称不能为空")
        if len(normalized) > 256:
            raise ValueError("报告名称不能超过 256 个字符")
        row.report_name = normalized
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def generate_report(self, report_id: int) -> IndustryReport:
        mode = self.settings.industry_report_generation_mode
        logger.info("行业正式报告生成模式: %s report_id=%s", mode, report_id)
        if mode == "grounded":
            return self._generate_grounded_report(report_id)
        return self._generate_legacy_report(report_id)

    def _generate_legacy_report(self, report_id: int) -> IndustryReport:
        changed = (
            self.db.query(IndustryReport)
            .filter(
                IndustryReport.id == report_id,
                IndustryReport.status.in_(["draft", "failed", "awaiting_approval"]),
            )
            .update(
                {
                    IndustryReport.status: "running",
                    IndustryReport.error_message: None,
                    IndustryReport.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if not changed:
            row = self.get_report(report_id)
            if not row:
                raise ValueError("报告不存在")
            raise ValueError("该报告当前不可生成；已完成报告请先创建新版")
        row = self.get_report(report_id)
        assert row is not None

        try:
            network_query = ""
            network_added = 0
            network_error = ""
            network_provider = ""
            if row.supplement_search:
                network_query = (
                    f"{row.industry_name} {row.company_name or ''} 行业分析 授信 信用风险"
                ).strip()
                try:
                    response = self.mita.search(query=network_query, max_results=8)
                    network_provider = getattr(response, "provider", None) or "mita"
                    translator = getattr(self.deepseek, "translate_network_source_to_chinese", None)
                    network_added = len(
                        append_industry_network_search_sources(
                            self.db,
                            row.id,
                            response.items,
                            translator=translator if callable(translator) else None,
                            require_translation=False,
                        )
                    )
                except Exception as exc:
                    network_error = " ".join(str(exc).split())
                    if len(network_error) > 240:
                        network_error = network_error[:240] + "…"
                    logger.warning("行业分析网络补充检索失败: %s", exc)

            authority_text, manifest = build_industry_authoritative_text(self.db, row.id)
            network_in_manifest = sum(
                1 for item in manifest if item.get("source_type") == "network_search"
            )
            row.source_manifest_json = json.dumps(manifest, ensure_ascii=False)
            row.generation_config_json = json.dumps(
                {
                    "supplement_search": row.supplement_search,
                    "network_search_query": network_query,
                    "network_search_added": network_added,
                    "network_search_sources": network_in_manifest,
                    "network_search_error": network_error or None,
                    "network_search_provider": network_provider or None,
                    "authority_max_chars": 100_000,
                    "source_count": len(manifest),
                    "generation_mode": "legacy",
                    "prompt_version": LEGACY_INDUSTRY_PROMPT_VERSION,
                },
                ensure_ascii=False,
            )
            self.db.commit()

            raw_input = (
                f"=== 当前报告专属数据源 ===\n{authority_text or '（无可用数据源，请使用通用模板）'}\n\n"
                f"=== 分析模板 ===\n{GENERIC_TEMPLATE}\n\n"
            )

            report = self.deepseek.analyze_industry(
                raw_input,
                industry_name=row.industry_name,
                company_name=row.company_name,
                context={
                    "supplement_used": network_in_manifest > 0,
                    "network_search_added": network_added,
                    "report_id": row.id,
                    "source_manifest": manifest,
                },
            )

            specs, chart_json = build_report_chart_specs(report, authority_text)

            report_json = json.dumps(report, ensure_ascii=False)
            report_html = report_json_to_html(report, sources=list_industry_sources(self.db, row.id))
            now = datetime.utcnow()

            row.report_json = report_json
            row.report_html = report_html
            row.chart_specs = chart_json
            row.status = "completed"
            row.generation_mode = "legacy"
            row.grounded_run_id = None
            row.prompt_version = LEGACY_INDUSTRY_PROMPT_VERSION
            row.evidence_snapshot_hash = None
            row.conflict_snapshot_hash = None
            row.citation_validation_status = "not_applicable"
            row.promoted_at = None
            row.promotion_type = None
            row.promotion_note = None
            row.grounded_generation_metadata = json.dumps(
                {
                    "generation_mode": "legacy",
                    "prompt_version": LEGACY_INDUSTRY_PROMPT_VERSION,
                    "citation_validation_status": "not_applicable",
                },
                ensure_ascii=False,
            )
            row.updated_at = now
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception as exc:
            logger.exception("行业分析失败: report_id=%s", report_id)
            row.status = "failed"
            row.error_message = str(exc)
            row.updated_at = datetime.utcnow()
            self.db.commit()
            raise

    def _generate_grounded_report(self, report_id: int) -> IndustryReport:
        readiness = check_grounded_readiness(self.db, report_id)
        if not readiness["ready"]:
            first = readiness["blocking_errors"][0]
            raise IndustryGenerationError(first["code"], first["message"], first["next_step"])

        changed = (
            self.db.query(IndustryReport)
            .filter(
                IndustryReport.id == report_id,
                IndustryReport.status.in_(["draft", "failed"]),
            )
            .update(
                {
                    IndustryReport.status: "running",
                    IndustryReport.error_message: None,
                    IndustryReport.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        if not changed:
            raise IndustryGenerationError(
                "REPORT_NOT_GENERATABLE",
                "该报告当前不可生成；已完成报告请先创建新版。",
                "create report revision",
            )
        row = self.get_report(report_id)
        assert row is not None
        grounded = GroundedReportService(self.db, analyzer=self.deepseek)
        try:
            run = grounded.generate(report_id)
            if run.status != "validated":
                raise IndustryGenerationError(
                    "GROUNDING_VALIDATION_FAILED",
                    "证据约束候选未通过引用校验，且未回退legacy流程。",
                    "grounded generation",
                )
            audit = {
                "generation_mode": "grounded",
                "prompt_version": run.prompt_version,
                "grounded_run_id": run.id,
                "evidence_snapshot_hash": run.evidence_snapshot_hash,
                "conflict_snapshot_hash": run.conflict_snapshot_hash,
                "citation_validation_status": "validated",
                "approval_required": bool(self.settings.grounded_report_require_approval),
            }
            if self.settings.grounded_report_require_approval:
                row.generation_mode = "grounded"
                row.grounded_run_id = run.id
                row.prompt_version = run.prompt_version
                row.evidence_snapshot_hash = run.evidence_snapshot_hash
                row.conflict_snapshot_hash = run.conflict_snapshot_hash
                row.citation_validation_status = "validated"
                row.grounded_generation_metadata = json.dumps(audit, ensure_ascii=False)
                row.generation_config_json = json.dumps(
                    {
                        "generation_mode": "grounded",
                        "prompt_version": GROUNDED_REPORT_PROMPT_VERSION,
                        "approval_required": True,
                        "legacy_fallback_allowed": False,
                    },
                    ensure_ascii=False,
                )
                row.status = "awaiting_approval"
                row.error_message = None
                row.updated_at = datetime.utcnow()
                self.db.commit()
                self.db.refresh(row)
                return row
            return grounded.promote(
                report_id, run.id, promotion_type="automatic",
                promotion_note="配置允许validated候选自动晋升",
            )
        except IndustryGenerationError as exc:
            row.status = "failed"
            row.error_message = f"{exc.code}: {exc}"
            row.updated_at = datetime.utcnow()
            self.db.commit()
            raise
        except GroundedPromotionError as exc:
            row.status = "failed"
            row.error_message = f"{exc.code}: {exc}"
            row.updated_at = datetime.utcnow()
            self.db.commit()
            raise IndustryGenerationError(exc.code, str(exc), exc.next_step) from exc
        except GroundedReportError as exc:
            row.status = "failed"
            row.error_message = "GROUNDING_VALIDATION_FAILED: grounded generation failed"
            row.updated_at = datetime.utcnow()
            self.db.commit()
            raise IndustryGenerationError(
                "GROUNDING_VALIDATION_FAILED", str(exc), "grounded generation",
            ) from exc
        except Exception as exc:
            row.status = "failed"
            row.error_message = "GROUNDING_VALIDATION_FAILED: grounded generation failed"
            row.updated_at = datetime.utcnow()
            self.db.commit()
            raise IndustryGenerationError(
                "GROUNDING_VALIDATION_FAILED", "证据约束生成失败，未执行legacy回退。",
                "grounded generation",
            ) from exc

    def run_analysis(
        self,
        industry_name: str,
        company_name: Optional[str] = None,
        supplement_search: bool = True,
    ) -> IndustryReport:
        """兼容旧调用：创建无共享数据源的草稿并立即生成。"""
        row = self.create_draft(industry_name, company_name, supplement_search)
        return self.generate_report(row.id)

    def get_report(self, report_id: int) -> Optional[IndustryReport]:
        return self.db.query(IndustryReport).filter(IndustryReport.id == report_id).first()

    def delete_report(self, report_id: int) -> bool:
        """删除一份历史报告及其级联来源、切片和证据记录。"""
        row = self.get_report(report_id)
        if not row:
            return False
        if row.status == "running":
            raise ValueError("报告正在生成，暂不能删除")
        self.db.delete(row)
        self.db.commit()
        shutil.rmtree(INDUSTRY_UPLOAD_ROOT / str(report_id), ignore_errors=True)
        return True

    def list_reports(self, limit: int = 20) -> list[IndustryReport]:
        return (
            self.db.query(IndustryReport)
            .order_by(IndustryReport.created_at.desc())
            .limit(limit)
            .all()
        )
