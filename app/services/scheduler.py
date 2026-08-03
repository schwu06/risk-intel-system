"""可选：APScheduler 每日自动跑流水线。"""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.services.pipeline_runner import run_modules_sync

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _daily_job():
    settings = get_settings()
    logger.info("定时任务启动: %s", settings.app_name)
    try:
        outcome = run_modules_sync(report_date=date.today())
        logger.info("定时流水线结束: %s", outcome.get("message"))
    except Exception:
        logger.exception("定时流水线失败")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    parts = settings.daily_pipeline_cron.strip().split()
    if len(parts) != 5:
        logger.warning("无效的 cron 表达式，跳过调度器: %s", settings.daily_pipeline_cron)
        return None

    minute, hour, day, month, dow = parts
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _daily_job,
        trigger=CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow),
        id="daily_risk_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("已注册每日流水线 cron: %s", settings.daily_pipeline_cron)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
