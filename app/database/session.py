"""数据库引擎与会话。"""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base


def _ensure_sqlite_dir(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        rel = database_url.replace("sqlite:///", "", 1)
        if rel != ":memory:" and not rel.startswith("file:"):
            path = Path(rel)
            if not path.is_absolute():
                path = Path.cwd() / path
            path.parent.mkdir(parents=True, exist_ok=True)


_settings = get_settings()
_ensure_sqlite_dir(_settings.database_url)

connect_args = {}
if _settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.database_url,
    connect_args=connect_args,
    echo=os.environ.get("SQL_ECHO", "").lower() in ("1", "true"),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_connection, connection_record):
    if _settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        # 较短超时，避免上传/侧栏接口在采集写库时卡住数十秒
        cursor.execute("PRAGMA busy_timeout=8000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def init_db() -> None:
    _ensure_sqlite_dir(_settings.database_url)
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()


def _migrate_sqlite_columns() -> None:
    """为已有 SQLite 库补充新增列（create_all 不会 ALTER）。"""
    if not _settings.database_url.startswith("sqlite"):
        return
    alterations = [
        ("daily_risk_entries", "published_at", "DATETIME"),
        ("news_articles", "published_at", "DATETIME"),
        ("report_runs", "phase", "VARCHAR(32)"),
        ("report_runs", "funnel_json", "TEXT"),
        ("report_runs", "job_id", "VARCHAR(64)"),
        ("report_runs", "kept_previous", "BOOLEAN DEFAULT 0"),
        ("pipeline_jobs", "snapshot_json", "TEXT"),
        ("pipeline_jobs", "window_hours", "INTEGER DEFAULT 24"),
        ("module_data_sources", "entity_id", "INTEGER"),
        ("news_articles", "window_hours", "INTEGER DEFAULT 24"),
        ("daily_risk_entries", "window_hours", "INTEGER DEFAULT 24"),
    ]
    with engine.begin() as conn:
        for table, column, col_type in alterations:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )
        for table in ("news_articles", "daily_risk_entries", "pipeline_jobs"):
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            if "window_hours" in {r[1] for r in rows}:
                conn.exec_driver_sql(
                    f"UPDATE {table} SET window_hours = 24 WHERE window_hours IS NULL"
                )
        _migrate_report_runs_window_hours(conn)


def _migrate_report_runs_window_hours(conn) -> None:
    """为 report_runs 增加 window_hours，并重建唯一约束 (date, module, window)。"""
    rows = conn.exec_driver_sql("PRAGMA table_info(report_runs)").fetchall()
    if not rows:
        return
    colnames = {r[1] for r in rows}
    if "window_hours" in colnames:
        conn.exec_driver_sql(
            "UPDATE report_runs SET window_hours = 24 WHERE window_hours IS NULL"
        )
        return

    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS report_runs_v2 (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            report_date DATE NOT NULL,
            module_code VARCHAR(8) NOT NULL,
            window_hours INTEGER NOT NULL DEFAULT 24,
            status VARCHAR(32) NOT NULL,
            started_at DATETIME,
            finished_at DATETIME,
            entry_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            phase VARCHAR(32),
            funnel_json TEXT,
            job_id VARCHAR(64),
            kept_previous BOOLEAN NOT NULL DEFAULT 0,
            CONSTRAINT uq_report_run_date_module_window
                UNIQUE (report_date, module_code, window_hours)
        )
        """
    )
    # 清空临时表，避免重复迁移半成品
    conn.exec_driver_sql("DELETE FROM report_runs_v2")
    select_cols = [
        "id",
        "report_date",
        "module_code",
        "status",
        "started_at",
        "finished_at",
        "entry_count",
        "notes",
    ]
    wh_expr = "24"
    insert_cols = (
        "id, report_date, module_code, window_hours, status, started_at, finished_at, "
        "entry_count, notes"
    )
    select_sql = (
        f"id, report_date, module_code, {wh_expr}, status, started_at, finished_at, "
        "entry_count, notes"
    )
    for col in ("phase", "funnel_json", "job_id"):
        if col in colnames:
            insert_cols += f", {col}"
            select_sql += f", {col}"
    if "kept_previous" in colnames:
        insert_cols += ", kept_previous"
        select_sql += ", COALESCE(kept_previous, 0)"
    else:
        insert_cols += ", kept_previous"
        select_sql += ", 0"
    conn.exec_driver_sql(
        f"INSERT INTO report_runs_v2 ({insert_cols}) SELECT {select_sql} FROM report_runs"
    )
    conn.exec_driver_sql("DROP TABLE report_runs")
    conn.exec_driver_sql("ALTER TABLE report_runs_v2 RENAME TO report_runs")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_report_runs_job_id ON report_runs (job_id)"
    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
