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
        cursor.execute("PRAGMA busy_timeout=30000")
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
    ]
    with engine.begin() as conn:
        for table, column, col_type in alterations:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
