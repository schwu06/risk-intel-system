"""异步流水线任务：防重入 + 后台线程执行 + 状态可轮询。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import date, datetime
from typing import Any, Optional

from app.config import MODULE_CODES
from app.database.models import PipelineJob, ReportRun
from app.database.session import SessionLocal
from app.services.pipeline import RiskPipeline

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running_job_id: Optional[str] = None


def get_running_job_id() -> Optional[str]:
    return _running_job_id


def start_pipeline_job(
    *,
    report_date: date,
    module_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """启动异步任务；若已有任务在跑则返回冲突信息。"""
    global _running_job_id

    codes = [c.upper() for c in (module_codes or list(MODULE_CODES.keys()))]
    invalid = [c for c in codes if c not in MODULE_CODES]
    if invalid:
        raise ValueError(f"未知模块: {', '.join(invalid)}")

    if not _lock.acquire(blocking=False):
        return {
            "accepted": False,
            "job_id": _running_job_id,
            "status": "conflict",
            "message": "已有采集任务在运行，请稍后再试或轮询当前任务状态",
        }

    job_id = uuid.uuid4().hex[:16]
    _running_job_id = job_id
    db = SessionLocal()
    try:
        row = PipelineJob(
            job_id=job_id,
            report_date=report_date,
            module_codes=json.dumps(codes, ensure_ascii=False),
            status="queued",
            message="任务已排队",
        )
        db.add(row)
        db.commit()
    except Exception:
        _running_job_id = None
        _lock.release()
        db.close()
        raise
    db.close()

    thread = threading.Thread(
        target=_execute_job,
        args=(job_id, report_date, codes),
        name=f"pipeline-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return {
        "accepted": True,
        "job_id": job_id,
        "status": "queued",
        "report_date": report_date.isoformat(),
        "module_codes": codes,
        "message": "采集任务已启动，请轮询状态",
    }


def get_job_status(job_id: str) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if not row:
            return None
        results = {}
        funnel = None
        try:
            results = json.loads(row.results_json or "{}")
        except json.JSONDecodeError:
            results = {}
        try:
            funnel = json.loads(row.funnel_json) if row.funnel_json else None
        except json.JSONDecodeError:
            funnel = None
        return {
            "job_id": row.job_id,
            "report_date": row.report_date.isoformat(),
            "module_codes": json.loads(row.module_codes or "[]"),
            "status": row.status,
            "results": results,
            "funnel": funnel,
            "message": row.message or "",
            "error_message": row.error_message,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "running_job_id": _running_job_id,
        }
    finally:
        db.close()


def _execute_job(job_id: str, report_date: date, codes: list[str]) -> None:
    global _running_job_id
    db = SessionLocal()
    try:
        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if row:
            row.status = "running"
            row.started_at = datetime.utcnow()
            row.message = "采集进行中…"
            db.commit()

        pipeline = RiskPipeline(db, job_id=job_id)
        results: dict[str, int] = {}
        funnels: dict[str, Any] = {}
        errors: list[str] = []
        for code in codes:
            try:
                results[code] = pipeline.run_module(code, report_date)
                run = (
                    db.query(ReportRun)
                    .filter(
                        ReportRun.report_date == report_date,
                        ReportRun.module_code == code,
                    )
                    .first()
                )
                if run and run.funnel_json:
                    try:
                        funnels[code] = json.loads(run.funnel_json)
                    except json.JSONDecodeError:
                        pass
            except Exception as exc:
                results[code] = -1
                errors.append(f"{code}: {exc}")
                logger.exception("异步任务模块失败 %s", code)

        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if row:
            row.results_json = json.dumps(results, ensure_ascii=False)
            row.funnel_json = json.dumps(funnels, ensure_ascii=False)
            row.finished_at = datetime.utcnow()
            if errors and all(v == -1 for v in results.values()):
                row.status = "failed"
                row.error_message = "; ".join(errors)
                row.message = "采集失败（已尽量保留上次结果）"
            else:
                row.status = "completed"
                row.message = "近24小时资讯采集完成" + (
                    f"（部分失败: {'; '.join(errors)}）" if errors else ""
                )
            db.commit()
    except Exception as exc:
        logger.exception("异步流水线任务崩溃: %s", job_id)
        try:
            row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
            if row:
                row.status = "failed"
                row.error_message = str(exc)
                row.message = "任务异常结束"
                row.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
        _running_job_id = None
        try:
            _lock.release()
        except RuntimeError:
            pass


def run_modules_sync(
    *,
    report_date: date,
    module_codes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """同步执行（调度器 / 调试用）。仍受全局锁保护，避免与异步任务重叠。"""
    global _running_job_id
    codes = [c.upper() for c in (module_codes or list(MODULE_CODES.keys()))]
    if not _lock.acquire(blocking=False):
        return {
            "ok": False,
            "results": {c: -1 for c in codes},
            "message": f"已有任务在运行: {_running_job_id}",
        }
    job_id = f"sync-{uuid.uuid4().hex[:10]}"
    _running_job_id = job_id
    db = SessionLocal()
    try:
        pipeline = RiskPipeline(db, job_id=job_id)
        results: dict[str, int] = {}
        errors: list[str] = []
        for code in codes:
            try:
                results[code] = pipeline.run_module(code, report_date)
            except Exception as exc:
                results[code] = -1
                errors.append(f"{code}: {exc}")
        return {
            "ok": not (errors and all(v == -1 for v in results.values())),
            "results": results,
            "message": "近24小时资讯采集完成"
            + (f"（部分失败: {'; '.join(errors)}）" if errors else ""),
            "errors": errors,
        }
    finally:
        db.close()
        _running_job_id = None
        try:
            _lock.release()
        except RuntimeError:
            pass
