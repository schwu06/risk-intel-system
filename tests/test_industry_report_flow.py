from __future__ import annotations

import unittest

from app.database.models import IndustryReport
from app.services.industry_analysis import IndustryAnalysisService
from tests.helpers import isolated_session


class _FakeAnalyzer:
    def analyze_industry(self, raw_text, industry_name, company_name=None, context=None):
        if "测试信源正文" not in raw_text:
            raise AssertionError("现有报告生成流程未收到信源正文")
        return {
            "title": f"{industry_name}测试报告",
            "summary": "固定摘要",
            "sections": [{"heading": "行业概况", "content": "固定正文"}],
            "risk_outlook": "固定展望",
            "key_metrics": [],
        }


class IndustryReportCompatibilityTests(unittest.TestCase):
    def test_history_report_delete_removes_report(self) -> None:
        with isolated_session() as db:
            report = IndustryReport(industry_name="能源", status="completed", version=1)
            db.add(report)
            db.commit()
            report_id = report.id
            self.assertTrue(IndustryAnalysisService(db).delete_report(report_id))
            self.assertIsNone(db.get(IndustryReport, report_id))

    def test_existing_report_generation_and_html_remain_compatible(self) -> None:
        from app.database.models import IndustryDataSource

        with isolated_session() as db:
            report = IndustryReport(
                industry_name="测试行业",
                company_name="测试企业",
                status="draft",
                supplement_search=False,
                version=1,
            )
            db.add(report)
            db.flush()
            db.add(
                IndustryDataSource(
                    report_id=report.id,
                    name="测试来源",
                    source_type="file",
                    extracted_text="测试信源正文",
                    char_count=6,
                )
            )
            db.commit()
            result = IndustryAnalysisService(db, deepseek=_FakeAnalyzer()).generate_report(
                report.id
            )
            self.assertEqual(result.status, "completed")
            self.assertIn("固定摘要", result.report_html)
            self.assertIn("行业概况", result.report_html)
            self.assertIn('"title": "测试行业测试报告"', result.report_json)


if __name__ == "__main__":
    unittest.main()
