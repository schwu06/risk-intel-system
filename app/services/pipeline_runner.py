"""异步流水线任务：防重入 + 后台线程执行 + 启动时冻结数据源快照。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Optional

from app.config import MODULE_CODES, NEWS_WINDOW_HOURS_24, get_settings, news_window_label
from app.database.models import PipelineJob, ReportRun
from app.database.session import SessionLocal
from app.services.pipeline import RiskPipeline
from app.services.rss_news import RssNewsCollector
from app.timeutil import tokyo_isoformat, tokyo_now

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_running_job_id: Optional[str] = None
_job_snapshots: dict[str, dict[str, Any]] = {}


def get_running_job_id() -> Optional[str]:
    return _running_job_id


def recover_stale_jobs() -> int:
    """进程重启后，将库中遗留的 queued/running 标记为失败，避免前端一直等待幽灵任务。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(PipelineJob)
            .filter(PipelineJob.status.in_(("queued", "running")))
            .all()
        )
        count = 0
        for row in rows:
            row.status = "failed"
            row.error_message = "服务重启，任务中断"
            row.message = "任务已中断，请重新采集"
            row.finished_at = tokyo_now()
            count += 1
        if count:
            db.commit()
            logger.warning("已清理 %s 个遗留流水线任务", count)
        return count
    finally:
        db.close()


def get_current_job() -> Optional[dict[str, Any]]:
    """返回当前运行中的任务状态；无任务时返回 None。"""
    jid = _running_job_id
    if not jid:
        return None
    return get_job_status(jid)


def _build_job_snapshot(db, entity_id: Optional[int] = None) -> dict[str, Any]:
    """任务启动快照。

    风险日报 / 主体评估不再读取用户上传的权威数据源；
    权威材料仅供深度研报使用。
    """
    return {
        "frozen_at": datetime.utcnow().isoformat() + "Z",
        "entity_id": entity_id,
        "source_ids": [],
        "source_names": [],
        "authority_chars": 0,
        "authority_text": "",
    }


def _snapshot_for_storage(snapshot: dict[str, Any]) -> str:
    """落库时去掉大文本，避免 pipeline_jobs 膨胀；完整文本仅驻留内存。"""
    meta = {
        "frozen_at": snapshot.get("frozen_at"),
        "entity_id": snapshot.get("entity_id"),
        "source_ids": snapshot.get("source_ids") or [],
        "source_names": snapshot.get("source_names") or [],
        "authority_chars": snapshot.get("authority_chars") or 0,
    }
    return json.dumps(meta, ensure_ascii=False)


def start_pipeline_job(
    *,
    report_date: date,
    module_codes: Optional[list[str]] = None,
    entity_id: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> dict[str, Any]:
    """启动异步任务；若已有任务在跑则返回冲突信息。"""
    global _running_job_id

    codes = [c.upper() for c in (module_codes or list(MODULE_CODES.keys()))]
    invalid = [c for c in codes if c not in MODULE_CODES]
    if invalid:
        raise ValueError(f"未知模块: {', '.join(invalid)}")

    settings = get_settings()
    hours = int(
        window_hours
        if window_hours is not None
        else (getattr(settings, "news_window_hours", NEWS_WINDOW_HOURS_24) or NEWS_WINDOW_HOURS_24)
    )
    if hours < 1:
        hours = NEWS_WINDOW_HOURS_24

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
        snapshot = _build_job_snapshot(db, entity_id=entity_id)
        snapshot["window_hours"] = hours
        _job_snapshots[job_id] = snapshot
        row = PipelineJob(
            job_id=job_id,
            report_date=report_date,
            module_codes=json.dumps(codes, ensure_ascii=False),
            window_hours=hours,
            status="queued",
            message=(
                f"任务已排队（主体#{entity_id}，近{news_window_label(hours)}）"
                if entity_id
                else f"任务已排队（近{news_window_label(hours)}）"
            ),
            snapshot_json=_snapshot_for_storage(snapshot),
        )
        db.add(row)
        db.commit()
    except Exception:
        _running_job_id = None
        _job_snapshots.pop(job_id, None)
        _lock.release()
        db.close()
        raise
    db.close()

    thread = threading.Thread(
        target=_execute_job,
        args=(job_id, report_date, codes, hours),
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
        "entity_id": entity_id,
        "window_hours": hours,
        "message": f"采集任务已启动（近{news_window_label(hours)}），请轮询状态",
        "snapshot": {
            "source_ids": snapshot.get("source_ids") or [],
            "authority_chars": snapshot.get("authority_chars") or 0,
            "frozen_at": snapshot.get("frozen_at"),
            "entity_id": entity_id,
            "window_hours": hours,
        },
    }


def get_job_status(job_id: str) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if not row:
            return None
        results = {}
        funnel = None
        snapshot_meta = None
        try:
            results = json.loads(row.results_json or "{}")
        except json.JSONDecodeError:
            results = {}
        try:
            funnel = json.loads(row.funnel_json) if row.funnel_json else None
        except json.JSONDecodeError:
            funnel = None
        try:
            snapshot_meta = json.loads(row.snapshot_json) if row.snapshot_json else None
        except json.JSONDecodeError:
            snapshot_meta = None
        return {
            "job_id": row.job_id,
            "report_date": row.report_date.isoformat(),
            "module_codes": json.loads(row.module_codes or "[]"),
            "window_hours": int(getattr(row, "window_hours", None) or NEWS_WINDOW_HOURS_24),
            "status": row.status,
            "results": results,
            "funnel": funnel,
            "message": row.message or "",
            "error_message": row.error_message,
            "started_at": tokyo_isoformat(row.started_at),
            "finished_at": tokyo_isoformat(row.finished_at),
            "running_job_id": _running_job_id,
            "snapshot": snapshot_meta,
            "entity_id": (snapshot_meta or {}).get("entity_id") if isinstance(snapshot_meta, dict) else None,
        }
    finally:
        db.close()


def _run_one_module(
    job_id: str,
    report_date: date,
    code: str,
    authority_text: str,
    rss: RssNewsCollector,
    entity_id: Optional[int] = None,
    window_hours: int = NEWS_WINDOW_HOURS_24,
) -> tuple[str, int, Optional[dict], Optional[str]]:
    """每个模块独立 Session；权威文本与 RSS 配置来自任务快照。"""
    db = SessionLocal()
    try:
        pipeline = RiskPipeline(
            db,
            job_id=job_id,
            authority_text=authority_text,
            rss=rss,
            entity_id=entity_id,
            window_hours=window_hours,
        )
        count = pipeline.run_module(code, report_date)
        funnel = None
        run = (
            db.query(ReportRun)
            .filter(
                ReportRun.report_date == report_date,
                ReportRun.module_code == code,
                ReportRun.window_hours == window_hours,
            )
            .first()
        )
        if run and run.funnel_json:
            try:
                funnel = json.loads(run.funnel_json)
            except json.JSONDecodeError:
                funnel = None
        return code, count, funnel, None
    except Exception as exc:
        logger.exception("模块并行执行失败 %s", code)
        return code, -1, None, str(exc)
    finally:
        db.close()


def _execute_modules(
    job_id: str,
    report_date: date,
    codes: list[str],
    authority_text: str,
    entity_id: Optional[int] = None,
    window_hours: int = NEWS_WINDOW_HOURS_24,
) -> tuple[dict[str, int], dict[str, Any], list[str]]:
    settings = get_settings()
    parallel = bool(getattr(settings, "pipeline_module_parallel", False)) and len(codes) > 1
    results: dict[str, int] = {}
    funnels: dict[str, Any] = {}
    errors: list[str] = []

    # 任务级冻结 RSS 配置（热加载不影响本次）
    rss = RssNewsCollector(
        retry_attempts=int(getattr(settings, "network_retry_attempts", 3) or 3),
        retry_backoff=float(getattr(settings, "network_retry_backoff_seconds", 1.5) or 1.5),
    )

    if parallel:
        workers = min(4, len(codes))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _run_one_module,
                    job_id,
                    report_date,
                    code,
                    authority_text,
                    rss,
                    entity_id,
                    window_hours,
                ): code
                for code in codes
            }
            for fut in as_completed(futures):
                code, count, funnel, err = fut.result()
                results[code] = count
                if funnel:
                    funnels[code] = funnel
                if err:
                    errors.append(f"{code}: {err}")
    else:
        for code in codes:
            code, count, funnel, err = _run_one_module(
                job_id,
                report_date,
                code,
                authority_text,
                rss,
                entity_id,
                window_hours,
            )
            results[code] = count
            if funnel:
                funnels[code] = funnel
            if err:
                errors.append(f"{code}: {err}")
    return results, funnels, errors


def _execute_job(
    job_id: str,
    report_date: date,
    codes: list[str],
    window_hours: int = NEWS_WINDOW_HOURS_24,
) -> None:
    global _running_job_id
    snapshot = _job_snapshots.get(job_id) or {}
    authority_text = snapshot.get("authority_text") or ""
    entity_id = snapshot.get("entity_id")
    hours = int(snapshot.get("window_hours") or window_hours or NEWS_WINDOW_HOURS_24)
    db = SessionLocal()
    try:
        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if row:
            row.status = "running"
            row.started_at = tokyo_now()
            n = len(snapshot.get("source_ids") or [])
            row.message = (
                f"采集进行中…（近{news_window_label(hours)}，已冻结 {n} 个数据源）"
            )
            db.commit()

        results, funnels, errors = _execute_modules(
            job_id,
            report_date,
            codes,
            authority_text,
            entity_id=entity_id,
            window_hours=hours,
        )

        # 新闻日报三板块跑完后补齐双投镜像，避免模块互清
        news_codes = [c for c in codes if c in ("B", "C", "D")]
        if news_codes and not (errors and all(results.get(c, -1) == -1 for c in news_codes)):
            try:
                sync_db = SessionLocal()
                try:
                    mirrored = RiskPipeline(
                        sync_db, window_hours=hours
                    ).sync_dual_route_mirrors(report_date)
                    if mirrored:
                        funnels["_dual_sync"] = {"mirrored": mirrored}
                        logger.info("双投同步完成：镜像 %s 条", mirrored)
                finally:
                    sync_db.close()
            except Exception:
                logger.exception("双投同步失败（不影响主采集结果）")

        row = db.query(PipelineJob).filter(PipelineJob.job_id == job_id).first()
        if row:
            row.results_json = json.dumps(results, ensure_ascii=False)
            row.funnel_json = json.dumps(funnels, ensure_ascii=False)
            row.finished_at = tokyo_now()
            if errors and all(v == -1 for v in results.values()):
                row.status = "failed"
                row.error_message = "; ".join(errors)
                row.message = "采集失败（已尽量保留上次结果）"
            else:
                row.status = "completed"
                row.message = f"近{news_window_label(hours)}资讯采集完成" + (
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
                row.finished_at = tokyo_now()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
        _job_snapshots.pop(job_id, None)
        _running_job_id = None
        try:
            _lock.release()
        except RuntimeError:
            pass


def run_modules_sync(
    *,
    report_date: date,
    module_codes: Optional[list[str]] = None,
    entity_id: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> dict[str, Any]:
    """同步执行（调度器 / 调试用）。仍受全局锁保护，避免与异步任务重叠。"""
    global _running_job_id
    codes = [c.upper() for c in (module_codes or list(MODULE_CODES.keys()))]
    settings = get_settings()
    hours = int(
        window_hours
        if window_hours is not None
        else (getattr(settings, "news_window_hours", NEWS_WINDOW_HOURS_24) or NEWS_WINDOW_HOURS_24)
    )
    if hours < 1:
        hours = NEWS_WINDOW_HOURS_24
    if not _lock.acquire(blocking=False):
        return {
            "ok": False,
            "results": {c: -1 for c in codes},
            "message": f"已有任务在运行: {_running_job_id}",
        }
    job_id = f"sync-{uuid.uuid4().hex[:10]}"
    _running_job_id = job_id
    try:
        db = SessionLocal()
        try:
            snapshot = _build_job_snapshot(db, entity_id=entity_id)
            snapshot["window_hours"] = hours
        finally:
            db.close()
        _job_snapshots[job_id] = snapshot
        results, _funnels, errors = _execute_modules(
            job_id,
            report_date,
            codes,
            snapshot.get("authority_text") or "",
            entity_id=entity_id,
            window_hours=hours,
        )
        return {
            "ok": not (errors and all(v == -1 for v in results.values())),
            "results": results,
            "message": f"近{news_window_label(hours)}资讯采集完成"
            + (f"（部分失败: {'; '.join(errors)}）" if errors else ""),
            "errors": errors,
            "entity_id": entity_id,
            "window_hours": hours,
        }
    finally:
        _job_snapshots.pop(job_id, None)
        _running_job_id = None
        try:
            _lock.release()
        except RuntimeError:
            pass
