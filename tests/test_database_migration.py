from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.api.routes import router
from app.database.models import Base, IndustryDataSource, IndustryReport
from app.database.session import _ensure_sqlite_indexes, _migrate_sqlite_columns


class DatabaseMigrationTests(unittest.TestCase):
    def test_fresh_database_initializes_from_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'fresh.db'}")
            try:
                Base.metadata.create_all(engine)
                _migrate_sqlite_columns(engine)
                _ensure_sqlite_indexes(engine)
                names = set(inspect(engine).get_table_names())
                self.assertIn("industry_data_sources", names)
                self.assertIn("industry_source_chunks", names)
                self.assertIn("industry_evidence_extraction_runs", names)
                self.assertIn("industry_evidence_cards", names)
            finally:
                engine.dispose()

    def test_old_database_upgrade_is_idempotent_and_preserves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'old.db'}")
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "CREATE TABLE industry_reports ("
                        "id INTEGER PRIMARY KEY, industry_name VARCHAR(256) NOT NULL, "
                        "company_name VARCHAR(256), status VARCHAR(32), report_html TEXT, "
                        "report_json TEXT, chart_specs TEXT, error_message TEXT, "
                        "legacy_report_id INTEGER, created_at DATETIME, updated_at DATETIME)"
                    )
                    conn.exec_driver_sql(
                        "CREATE TABLE industry_data_sources ("
                        "id INTEGER PRIMARY KEY, industry_name VARCHAR(256), name VARCHAR(256), "
                        "source_type VARCHAR(32), file_path VARCHAR(1024), "
                        "original_filename VARCHAR(512), url VARCHAR(1024), extracted_text TEXT, "
                        "is_active BOOLEAN, created_at DATETIME)"
                    )
                    conn.exec_driver_sql(
                        "INSERT INTO industry_reports "
                        "(id, industry_name, status, report_json) VALUES (1, '旧行业', 'completed', '{}')"
                    )
                    conn.exec_driver_sql(
                        "INSERT INTO industry_data_sources "
                        "(id, industry_name, name, source_type, extracted_text) "
                        "VALUES (1, '旧行业', '旧来源', 'file', '旧正文')"
                    )
                Base.metadata.create_all(engine)
                _migrate_sqlite_columns(engine)
                Base.metadata.create_all(engine)
                _ensure_sqlite_indexes(engine)
                _migrate_sqlite_columns(engine)
                _ensure_sqlite_indexes(engine)
                with engine.connect() as conn:
                    report = conn.exec_driver_sql(
                        "SELECT industry_name, report_json FROM industry_reports WHERE id=1"
                    ).one()
                    source = conn.exec_driver_sql(
                        "SELECT name, extracted_text, report_id, parse_status "
                        "FROM industry_data_sources WHERE id=1"
                    ).one()
                self.assertEqual(report, ("旧行业", "{}"))
                self.assertEqual(source[0:2], ("旧来源", "旧正文"))
                self.assertIsNone(source.report_id)
                self.assertIsNone(source.parse_status)
                with Session(engine) as session:
                    orm_report = session.get(IndustryReport, 1)
                    orm_source = session.get(IndustryDataSource, 1)
                    self.assertEqual(orm_report.industry_name, "旧行业")
                    self.assertEqual(orm_source.extracted_text, "旧正文")
            finally:
                engine.dispose()

    def test_existing_upload_and_generation_paths_are_unchanged(self) -> None:
        paths = {route.path for route in router.routes}
        self.assertIn(
            "/industry/reports/{report_id}/data-sources/upload", paths
        )
        self.assertIn("/industry/reports/{report_id}/data-sources/url", paths)
        self.assertIn("/industry/reports/{report_id}/generate", paths)
        self.assertIn("/industry/reports/{report_id}/evidence/extract", paths)


if __name__ == "__main__":
    unittest.main()
