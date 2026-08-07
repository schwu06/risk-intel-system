from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from sqlalchemy import create_engine, inspect

from app.api.routes import export_entity_assessment_docx, list_credit_updates
from app.database.models import CreditUpdate, EntityRisk, ReportRun, TargetEntity
from app.database.session import _migrate_sqlite_columns
from app.main import _entity_assessment_context
from app.services.deepseek_analyzer import build_system_prompt
from app.services.entity_catalog import configured_entity_catalog
from app.services.entity_credit import (
    refresh_entity_credit,
    seed_default_entities,
    suggested_credit_for_entity,
)
from app.services.entity_mock import seed_entity_demo_data
from app.services.entity_relevance import prepare_entity_row
from app.services.pipeline import RiskPipeline
from tests.helpers import isolated_session


class EntityCatalogTests(unittest.TestCase):
    def test_catalog_expands_targets_and_sources(self) -> None:
        catalog = configured_entity_catalog()
        self.assertGreaterEqual(len(catalog.profiles), 10)
        self.assertIn("Godiva", {profile.key for profile in catalog.profiles})
        self.assertIn("大和証券", {profile.key for profile in catalog.profiles})
        for profile in catalog.profiles:
            self.assertGreaterEqual(len(profile.sources), 4, profile.key)
            self.assertTrue(any(source.source_type == "official" for source in profile.sources))

    def test_seed_is_idempotent_and_uses_catalog(self) -> None:
        with isolated_session() as db:
            first = seed_default_entities(db)
            second = seed_default_entities(db)
            active = db.query(TargetEntity).filter(TargetEntity.monitor_status == "active").all()
            self.assertGreaterEqual(first, 10)
            self.assertEqual(second, 0)
            self.assertGreaterEqual(len(active), 10)


class EntitySignalTests(unittest.TestCase):
    def _entity(self, db, name: str = "Godiva") -> TargetEntity:
        seed_default_entities(db)
        return db.query(TargetEntity).filter(TargetEntity.name == name).one()

    def test_positive_high_importance_news_cannot_raise_warning(self) -> None:
        with isolated_session() as db:
            entity = self._entity(db)
            profile = configured_entity_catalog().find((entity.name,))
            row = {
                "标题": "Godiva 成功偿还到期债务",
                "关联企业": "Godiva",
                "风险等级": "极高",
                "资讯重要度": "极高",
                "影响方向": "positive",
                "信用风险信号": "high",
                "主体相关性": "direct",
                "核心摘要": "公司公告称已按期完成偿还。",
                "来源链接": "https://www.godiva.com/blogs/media/example",
                "来源名称": "Godiva Media Center",
                "_source_item": {
                    "title": "Godiva repays debt",
                    "entity_key": "Godiva",
                    "relation": "direct",
                    "source_type": "official",
                },
            }
            self.assertTrue(prepare_entity_row(entity, row, profile))
            self.assertEqual(row["信用风险信号"], "none")
            self.assertEqual(row["风险等级"], "低")

    def test_only_valid_direct_negative_signal_affects_warning(self) -> None:
        with isolated_session() as db:
            entity = self._entity(db)
            today = date.today()
            rows = [
                EntityRisk(
                    entity_id=entity.id,
                    report_date=today,
                    title="演示负面事件",
                    risk_level="极高",
                    summary="演示",
                    provenance="demo",
                    relevance="direct",
                    sentiment_direction="negative",
                    credit_impact="critical",
                    confidence=0.99,
                    review_status="pending",
                ),
                EntityRisk(
                    entity_id=entity.id,
                    report_date=today,
                    title="可可行业背景",
                    risk_level="极高",
                    summary="行业背景",
                    provenance="real",
                    relevance="contextual",
                    sentiment_direction="negative",
                    credit_impact="critical",
                    confidence=0.99,
                    review_status="pending",
                ),
                EntityRisk(
                    entity_id=entity.id,
                    report_date=today,
                    title="主体出现流动性压力",
                    risk_level="高",
                    summary="主体直接负面信号",
                    provenance="real",
                    relevance="direct",
                    sentiment_direction="negative",
                    credit_impact="medium",
                    confidence=0.8,
                    review_status="pending",
                ),
            ]
            db.add_all(rows)
            db.flush()
            self.assertEqual(suggested_credit_for_entity(db, entity.id), "预警")
            refresh_entity_credit(db, entity, force=True)
            self.assertEqual(entity.credit_level, "预警")

    def test_cross_entity_and_contextual_sources_are_separated(self) -> None:
        with isolated_session() as db:
            godiva = self._entity(db)
            glp = self._entity(db, "普洛斯")
            profile = configured_entity_catalog().find((godiva.name,))
            source_item = {
                "title": "COCOBOD publishes cocoa crop update",
                "snippet": "The cocoa board published a crop update for the industry.",
                "entity_key": "Godiva",
                "relation": "contextual",
                "source_type": "industry",
            }
            godiva_row = {
                "标题": "加纳可可局发布产量更新",
                "核心摘要": "行业供应背景信息。",
                "影响方向": "negative",
                "信用风险信号": "high",
                "_source_item": source_item,
            }
            self.assertTrue(prepare_entity_row(godiva, godiva_row, profile))
            self.assertEqual(godiva_row["主体相关性"], "contextual")
            self.assertEqual(godiva_row["信用风险信号"], "none")

            glp_profile = configured_entity_catalog().find((glp.name,))
            glp_row = dict(godiva_row)
            glp_row["_source_item"] = source_item
            self.assertFalse(prepare_entity_row(glp, glp_row, glp_profile))

    def test_untraceable_model_row_is_rejected(self) -> None:
        with isolated_session() as db:
            entity = self._entity(db)
            profile = configured_entity_catalog().find((entity.name,))
            row = {
                "标题": "Godiva 据称出现重大流动性问题",
                "关联企业": "Godiva",
                "核心摘要": "模型输出没有对应采集候选。",
                "影响方向": "negative",
                "信用风险信号": "critical",
                "置信度": 0.99,
            }
            self.assertFalse(prepare_entity_row(entity, row, profile))

    def test_real_signal_without_confidence_does_not_raise_warning(self) -> None:
        with isolated_session() as db:
            entity = self._entity(db)
            db.add(
                EntityRisk(
                    entity_id=entity.id,
                    report_date=date.today(),
                    title="置信度缺失的负面事件",
                    risk_level="极高",
                    summary="没有可信度字段。",
                    provenance="real",
                    relevance="direct",
                    sentiment_direction="negative",
                    credit_impact="critical",
                    confidence=None,
                    review_status="pending",
                )
            )
            db.flush()
            self.assertEqual(suggested_credit_for_entity(db, entity.id), "正常")


class EntityPipelineTests(unittest.TestCase):
    def test_contextual_items_do_not_satisfy_direct_source_target(self) -> None:
        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "Godiva").one()
            pipeline = RiskPipeline(db, entity_id=entity.id, window_hours=24)
            items = [
                {
                    "title": "COCOBOD releases cocoa crop update",
                    "snippet": "The cocoa industry crop forecast has been updated today.",
                    "url": "https://cocobod.gh/news/crop-update",
                    "entity_key": "Godiva",
                    "relation": "contextual",
                    "source_type": "industry",
                },
                {
                    "title": "Godiva publishes a company update",
                    "snippet": "Godiva published a new company update for stakeholders today.",
                    "url": "https://www.godiva.com/blogs/media/company-update",
                    "entity_key": "Godiva",
                    "relation": "direct",
                    "source_type": "official",
                },
            ]
            self.assertEqual(pipeline._count_valid_items(items, "A"), 1)

    def test_stale_degraded_item_is_not_saved(self) -> None:
        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "Godiva").one()
            pipeline = RiskPipeline(db, entity_id=entity.id, window_hours=24)
            old = datetime.now() - timedelta(days=30)
            count = pipeline._save_structured_entries(
                [
                    {
                        "标题": "Godiva 过期材料",
                        "关联企业": "Godiva",
                        "风险类别": "企业经营",
                        "风险等级": "低",
                        "核心摘要": "这是一条带有明确旧发布时间的原始摘要。",
                        "影响分析": "待复核",
                        "来源链接": "https://example.org/old",
                        "来源名称": "旧材料",
                        "发布时间": old.isoformat(),
                        "主体相关性": "direct",
                        "影响方向": "unknown",
                        "信用风险信号": "none",
                        "_degraded": True,
                    }
                ],
                module_code="A",
                report_date=date.today(),
                metadata={},
                search_log_id=None,
                raw_context="{}",
            )
            self.assertEqual(count, 0)
            self.assertEqual(db.query(EntityRisk).count(), 0)

    def test_empty_collection_does_not_create_mock_by_default(self) -> None:
        class EmptyPipeline(RiskPipeline):
            def _collect_rss(self, module_code, funnel):
                return {"source": "rss", "items": [], "metadata": {}, "error": None}

            def _collect_direct_sites(self, module_code, funnel):
                return {"source": "direct_site", "items": [], "metadata": {}, "error": "未配置直连站点"}

            def _analyze_merged(self, *args, **kwargs):
                return []

        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "普洛斯").one()
            pipeline = EmptyPipeline(db, entity_id=entity.id, window_hours=24)
            original = (
                pipeline.settings.entity_demo_mode,
                pipeline.settings.mita_api_key,
                pipeline.settings.gemini_api_key,
            )
            try:
                pipeline.settings.entity_demo_mode = False
                pipeline.settings.mita_api_key = ""
                pipeline.settings.gemini_api_key = ""
                with patch("app.services.api_keys.is_placeholder_key", return_value=True):
                    count = pipeline.run_module("A", date.today())
            finally:
                (
                    pipeline.settings.entity_demo_mode,
                    pipeline.settings.mita_api_key,
                    pipeline.settings.gemini_api_key,
                ) = original
            self.assertEqual(count, 0)
            self.assertEqual(db.query(EntityRisk).count(), 0)
            run = db.query(ReportRun).filter(ReportRun.module_code == "A").one()
            funnel = json.loads(run.funnel_json)
            self.assertNotIn("demo_fallback", funnel)


class EntityPresentationAndMigrationTests(unittest.TestCase):
    def test_page_date_is_strict_and_demo_is_hidden(self) -> None:
        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "Godiva").one()
            target_day = date(2026, 8, 7)
            db.add_all(
                [
                    EntityRisk(
                        entity_id=entity.id,
                        report_date=target_day,
                        title="目标日真实事件",
                        risk_level="低",
                        summary="真实来源",
                        provenance="real",
                    ),
                    EntityRisk(
                        entity_id=entity.id,
                        report_date=target_day - timedelta(days=1),
                        title="其它日期事件",
                        risk_level="低",
                        summary="其它日期",
                        provenance="real",
                    ),
                    EntityRisk(
                        entity_id=entity.id,
                        report_date=target_day,
                        title="目标日演示事件",
                        risk_level="高",
                        summary="演示",
                        provenance="demo",
                    ),
                ]
            )
            db.commit()
            request = Request({"type": "http", "method": "GET", "path": "/entity-assessment", "headers": []})
            context = _entity_assessment_context(
                request=request,
                report_date=target_day.isoformat(),
                entity_id=entity.id,
                db=db,
            )
            self.assertEqual([risk.title for risk in context["risks"]], ["目标日真实事件"])
            self.assertGreaterEqual(len(context["entity_sources"]), 4)

    def test_force_demo_seed_never_deletes_real_events(self) -> None:
        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "Godiva").one()
            db.add(
                EntityRisk(
                    entity_id=entity.id,
                    report_date=date.today(),
                    title="必须保留的真实事件",
                    risk_level="低",
                    summary="真实来源",
                    provenance="real",
                )
            )
            db.commit()
            seed_entity_demo_data(db, force=True)
            real_titles = [
                row.title
                for row in db.query(EntityRisk)
                .filter(
                    EntityRisk.entity_id == entity.id,
                    EntityRisk.provenance == "real",
                )
                .all()
            ]
            self.assertEqual(real_titles, ["必须保留的真实事件"])

    def test_old_entity_table_gets_provenance_columns_and_demo_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'old-entity.db'}")
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "CREATE TABLE entity_risks ("
                        "id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, report_date DATE NOT NULL, "
                        "title VARCHAR(512) NOT NULL, risk_level VARCHAR(16) NOT NULL, summary TEXT NOT NULL, "
                        "source_url VARCHAR(1024), structured_json TEXT)"
                    )
                    conn.exec_driver_sql(
                        "INSERT INTO entity_risks "
                        "(id, entity_id, report_date, title, risk_level, summary, source_url) "
                        "VALUES (1, 1, '2026-08-06', '演示', '高', '演示', "
                        "'https://example.com/mock/item')"
                    )
                _migrate_sqlite_columns(engine)
                columns = {col["name"] for col in inspect(engine).get_columns("entity_risks")}
                with engine.connect() as conn:
                    row = conn.exec_driver_sql(
                        "SELECT provenance, review_status, credit_impact FROM entity_risks WHERE id=1"
                    ).one()
                self.assertTrue({"published_at", "provenance", "credit_impact"}.issubset(columns))
                self.assertEqual(row, ("demo", "rejected", "none"))
            finally:
                engine.dispose()

    def test_entity_prompt_separates_importance_from_credit_signal(self) -> None:
        prompt = build_system_prompt(
            module_code="A", target_entity="Godiva", window_hours=24
        )
        self.assertIn("资讯重要度", prompt)
        self.assertIn("信用风险信号", prompt)
        self.assertIn("不得因新闻重要就判为负面", prompt)

    def test_api_and_export_hide_demo_warning_history(self) -> None:
        with isolated_session() as db:
            seed_default_entities(db)
            entity = db.query(TargetEntity).filter(TargetEntity.name == "Godiva").one()
            demo_risk = EntityRisk(
                entity_id=entity.id,
                report_date=date.today(),
                title="演示风险",
                risk_level="高",
                summary="演示",
                provenance="demo",
            )
            real_risk = EntityRisk(
                entity_id=entity.id,
                report_date=date.today(),
                title="真实风险",
                risk_level="低",
                summary="真实",
                provenance="real",
            )
            db.add_all([demo_risk, real_risk])
            db.flush()
            db.add_all(
                [
                    CreditUpdate(
                        entity_id=entity.id,
                        previous_level="正常",
                        new_level="预警",
                        reason="演示：生成样本",
                        trigger_risk_id=demo_risk.id,
                    ),
                    CreditUpdate(
                        entity_id=entity.id,
                        previous_level="正常",
                        new_level="关注",
                        reason="真实公开信息复核",
                        trigger_risk_id=real_risk.id,
                    ),
                ]
            )
            db.commit()

            logs = list_credit_updates(
                entity.id, limit=50, include_demo=False, db=db
            )
            self.assertEqual([log.reason for log in logs], ["真实公开信息复核"])

            with patch("app.api.routes.export_entity_assessment_to_path") as exporter:
                response = export_entity_assessment_docx(
                    entity.id, report_date=date.today(), db=db
                )
            self.assertIn("企业公开信息风险监测简报", response.filename)
            exported_logs = exporter.call_args.kwargs["credit_logs"]
            exported_risks = exporter.call_args.kwargs["risks"]
            self.assertEqual([log.reason for log in exported_logs], ["真实公开信息复核"])
            self.assertEqual([risk.title for risk in exported_risks], ["真实风险"])


if __name__ == "__main__":
    unittest.main()
