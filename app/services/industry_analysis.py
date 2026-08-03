"""行业/授信分析独立模块。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from html import escape
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.database.models import IndustryAnalysisReport, IndustryReport
from app.services.chart_generator import extract_and_build_charts, to_echarts_option
from app.services.data_source_service import get_industry_authoritative_text
from app.services.deepseek_analyzer import DeepSeekAnalyzer
from app.services.mita_search import MitaSearchClient

logger = logging.getLogger(__name__)

GENERIC_TEMPLATE = """
通用行业分析框架：
1. 行业概况与产业链
2. 市场规模与竞争格局
3. 主要企业财务与信用状况
4. 政策监管与合规环境
5. 关键风险因素
6. 结论与授信建议
"""


def report_json_to_html(report: dict[str, Any]) -> str:
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
    return "\n".join(parts)


class IndustryAnalysisService:
    def __init__(
        self,
        db: Session,
        deepseek: Optional[DeepSeekAnalyzer] = None,
        mita: Optional[MitaSearchClient] = None,
    ) -> None:
        self.db = db
        self.deepseek = deepseek or DeepSeekAnalyzer()
        self.mita = mita or MitaSearchClient()

    def run_analysis(
        self,
        industry_name: str,
        company_name: Optional[str] = None,
        supplement_search: bool = True,
    ) -> IndustryReport:
        # 双写：新表为主，旧表兼容
        legacy = IndustryAnalysisReport(
            industry_name=industry_name,
            company_name=company_name,
            status="running",
        )
        self.db.add(legacy)
        self.db.flush()

        row = IndustryReport(
            industry_name=industry_name,
            company_name=company_name,
            status="running",
            legacy_report_id=legacy.id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        self.db.refresh(legacy)

        try:
            authority_text = get_industry_authoritative_text(self.db, industry_name)
            supplement = ""
            if supplement_search and len(authority_text) < 3000:
                try:
                    query = f"{industry_name} {company_name or ''} 行业分析 授信 信用风险"
                    resp = self.mita.search(query=query, max_results=8)
                    supplement = json.dumps(
                        [{"title": i.title, "url": i.url, "snippet": i.snippet} for i in resp.items],
                        ensure_ascii=False,
                        indent=2,
                    )
                except Exception as exc:
                    logger.warning("行业分析网络补充检索失败: %s", exc)

            raw_input = (
                f"=== 权威/本地数据源 ===\n{authority_text or '（无上传数据源，请使用通用模板）'}\n\n"
                f"=== 分析模板 ===\n{GENERIC_TEMPLATE}\n\n"
            )
            if supplement:
                raw_input += f"=== 网络检索补充（广度参考，优先级低于本地源）===\n{supplement}\n"

            report = self.deepseek.analyze_industry(
                raw_input,
                industry_name=industry_name,
                company_name=company_name,
                context={"supplement_used": bool(supplement)},
            )

            combined_text = authority_text + "\n" + json.dumps(report, ensure_ascii=False)
            specs, chart_json = extract_and_build_charts(combined_text)
            metrics = report.get("key_metrics") or []
            if metrics and not specs:
                labels = [str(m.get("name", "")) for m in metrics if isinstance(m, dict)]
                values = []
                for m in metrics:
                    if not isinstance(m, dict):
                        continue
                    try:
                        values.append(float(str(m.get("value", "0")).replace("%", "")))
                    except ValueError:
                        values.append(0)
                if labels and values:
                    spec = {
                        "id": "chart_metrics",
                        "type": "bar",
                        "title": "关键指标",
                        "labels": labels,
                        "series": [{"name": "指标值", "data": values}],
                    }
                    specs = [spec]
                    chart_json = json.dumps(
                        [{"id": spec["id"], "option": to_echarts_option(spec)}],
                        ensure_ascii=False,
                    )

            report_json = json.dumps(report, ensure_ascii=False)
            report_html = report_json_to_html(report)
            now = datetime.utcnow()

            for target in (row, legacy):
                target.report_json = report_json
                target.report_html = report_html
                target.chart_specs = chart_json
                target.status = "completed"
                target.updated_at = now
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception as exc:
            logger.exception("行业分析失败: %s", industry_name)
            now = datetime.utcnow()
            for target in (row, legacy):
                target.status = "failed"
                target.error_message = str(exc)
                target.updated_at = now
            self.db.commit()
            raise

    def get_report(self, report_id: int) -> Optional[IndustryReport]:
        return self.db.query(IndustryReport).filter(IndustryReport.id == report_id).first()

    def list_reports(self, limit: int = 20) -> list[IndustryReport]:
        return (
            self.db.query(IndustryReport)
            .order_by(IndustryReport.created_at.desc())
            .limit(limit)
            .all()
        )
