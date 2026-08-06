"""APScheduler：东京整点刷新近24小时新闻；保留可选每日全量 cron。"""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import NEWS_WINDOW_HOURS_24, PAGE_MODULES, get_settings
from app.services.pipeline_runner import run_modules_sync, start_pipeline_job
from app.timeutil import TOKYO, tokyo_today

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

_NEWS_MODULES = list(PAGE_MODULES.get("daily_news", ("B", "C", "D")))


def _daily_job():
    settings = get_settings()
    logger.info("定时任务启动: %s", settings.app_name)
    try:
        outcome = run_modules_sync(report_date=date.today())
        logger.info("定时流水线结束: %s", outcome.get("message"))
    except Exception:
        logger.exception("定时流水线失败")


def _hourly_news_job():
    """东京整点：仅采集近24小时新闻（B/C/D）。作用域忙碌时跳过，不打断界面操作。"""
    rd = tokyo_today()
    logger.info("整点新闻采集启动 report_date=%s", rd.isoformat())
    try:
        started = start_pipeline_job(
            report_date=rd,
            module_codes=_NEWS_MODULES,
            window_hours=NEWS_WINDOW_HOURS_24,
        )
        if not started.get("accepted"):
            logger.info(
                "整点新闻采集跳过（作用域忙）: %s job_id=%s",
                started.get("message"),
                started.get("job_id"),
            )
            return
        logger.info("整点新闻采集已排队 job_id=%s", started.get("job_id"))
    except Exception:
        logger.exception("整点新闻采集失败")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    _scheduler = BackgroundScheduler(timezone=TOKYO)

    # 东京整点：近24小时新闻日报（结果即 7×24「今天」快照）
    _scheduler.add_job(
        _hourly_news_job,
        trigger=CronTrigger(minute=0, timezone=TOKYO),
        id="hourly_news_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    logger.info("已注册东京整点新闻采集 cron: 每小时 :00 Asia/Tokyo")

    parts = settings.daily_pipeline_cron.strip().split()
    if len(parts) == 5:
        minute, hour, day, month, dow = parts
        _scheduler.add_job(
            _daily_job,
            trigger=CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=dow,
                timezone=TOKYO,
            ),
            id="daily_risk_pipeline",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("已注册每日流水线 cron: %s Asia/Tokyo", settings.daily_pipeline_cron)
    else:
        logger.warning("无效的 cron 表达式，跳过每日调度: %s", settings.daily_pipeline_cron)

    _scheduler.start()
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
