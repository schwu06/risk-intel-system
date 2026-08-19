"""国际评级：快照读写与后台刷新任务。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from intl_ratings.config import get_intl_config
from intl_ratings.io import load_issuer_records
from intl_ratings.models import NR
from intl_ratings.pipeline import IntlRatingsPipeline

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "data" / "intl_ratings" / "latest.json"
JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_RUNNING = False

CATEGORY_SIMPLE = "简易分类债券"
CATEGORY_NON_SIMPLE = "非简易分类债券"

# 与前端原清单对齐的分类兜底（CSV 无分类时使用）
_CATEGORY_FALLBACK: dict[str, str] = {
    "ABU DHABI COMMERCIAL BANK, ABU DHABI": CATEGORY_SIMPLE,
    "AGRICULTURAL DEVELOPMENT BANK OF CHINA, THE, BEIJING": CATEGORY_SIMPLE,
    "BARCLAYS BANK PLC (ALL U.K. OFFICES)": CATEGORY_SIMPLE,
    "CCBL(Cayman)1 Corporation Limited": CATEGORY_SIMPLE,
    "CDBL FUNDING 1": CATEGORY_SIMPLE,
    "CHINA CINDA FINANCE (2017) I LIMITED": CATEGORY_SIMPLE,
    "CSI_MTN_LIMITED": CATEGORY_SIMPLE,
    "DBS Bank Ltd, Australia Branch": CATEGORY_SIMPLE,
    "EMIRATES NBD BANK PJSC": CATEGORY_SIMPLE,
    "EXPORT-IMPORT BANK OF CHINA, THE, BEIJING": CATEGORY_SIMPLE,
    "EXPORT-IMPORT BANK OF KOREA, THE, SEOUL": CATEGORY_SIMPLE,
    "FIRST ABU DHABI BANK PJSC H.O.": CATEGORY_SIMPLE,
    "ICBCIL FINANCE CO. LIMITED": CATEGORY_SIMPLE,
    "INDUSTRIAL BANK OF KOREA": CATEGORY_SIMPLE,
    "KEB HANA BANK": CATEGORY_SIMPLE,
    "KOREA DEVELOPMENT BANK, THE, SEOUL": CATEGORY_SIMPLE,
    "MITSUBISHI HC CAPITAL INC": CATEGORY_SIMPLE,
    "MITSUBISHI HC CAPITAL UK PLC": CATEGORY_SIMPLE,
    "MIZUHO BANK, LTD": CATEGORY_SIMPLE,
    "NORINCHUKIN BANK,THE,TOKYO": CATEGORY_SIMPLE,
    "QNB Finance Ltd": CATEGORY_SIMPLE,
    "SHINHAN BANK, SEOUL": CATEGORY_SIMPLE,
    "SNB Funding Limited": CATEGORY_SIMPLE,
    "SOCIETE GENERALE, PARIS": CATEGORY_SIMPLE,
    "STANDARD CHARTERED BANK LONDON (ALL U.K. OFFICES)": CATEGORY_SIMPLE,
    "Sumitomo Mitsui Finance and Leasing Company, Limited": CATEGORY_SIMPLE,
    "WESTPAC BANKING CORPORATION": CATEGORY_SIMPLE,
    "交银租赁管理香港有限公司": CATEGORY_SIMPLE,
    "沙特阿拉伯王国政府": CATEGORY_SIMPLE,
    "三井住友信托银行股份有限公司": CATEGORY_SIMPLE,
    "中国光大银行股份有限公司卢森堡分行": CATEGORY_SIMPLE,
    "中银航空租赁有限公司": CATEGORY_SIMPLE,
    "法国BPCE银行": CATEGORY_SIMPLE,
    "法国国民互助信贷银行": CATEGORY_SIMPLE,
    "韩国政府": CATEGORY_SIMPLE,
    "CCCI TREASURE LIMITED": CATEGORY_NON_SIMPLE,
    "CHINA HUANENG GROUP CO., LTD.": CATEGORY_NON_SIMPLE,
    "CHINA SOUTHERN POWER GRID CO., LTD": CATEGORY_NON_SIMPLE,
    "CHINA THREE GORGES CORPORATION": CATEGORY_NON_SIMPLE,
    "CNOOC Limited": CATEGORY_NON_SIMPLE,
    "DENSO CORPORATION": CATEGORY_NON_SIMPLE,
    "Haitong UT Brilliant Limited": CATEGORY_NON_SIMPLE,
    "ITOCHU CORPORATION": CATEGORY_NON_SIMPLE,
    "MARUBENI CORPORATION": CATEGORY_NON_SIMPLE,
    "Mitsubishi Corporation": CATEGORY_NON_SIMPLE,
    "MITSUI & CO.,LTD.": CATEGORY_NON_SIMPLE,
    "ORIX CORPORATION": CATEGORY_NON_SIMPLE,
    "SUMITOMO CORPORATION": CATEGORY_NON_SIMPLE,
    "Suntory Holdings Limited": CATEGORY_NON_SIMPLE,
    "TAKEDA PHARMACEUTICAL COMPANY LIMITED": CATEGORY_NON_SIMPLE,
}


def _resolve_category(name: str, raw: str = "") -> str:
    if raw in (CATEGORY_SIMPLE, CATEGORY_NON_SIMPLE):
        return raw
    if "非简易" in (raw or ""):
        return CATEGORY_NON_SIMPLE
    if "简易" in (raw or ""):
        return CATEGORY_SIMPLE
    return _CATEGORY_FALLBACK.get(name, CATEGORY_NON_SIMPLE)


def _placeholder_row(idx: int, name: str, category: str) -> dict[str, Any]:
    return {
        "id": f"ir-{idx}",
        "issuer": name,
        "category": _resolve_category(name, category),
        "moodys": NR,
        "sp": NR,
        "fitch": NR,
        "loss": "[需人工复核]",
        "listed": "否",
        "delisted": "未上市",
        "priceDrop": "无公开交易数据",
        "noRatingReason": "",
        "ratingChanged": "否",
        "rssUrl": "",
    }


def load_snapshot() -> dict[str, Any]:
    if SNAPSHOT_PATH.is_file():
        try:
            with SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                data["running"] = _is_running()
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取评级快照失败: %s", exc)
    return _build_skeleton_snapshot()


def _build_skeleton_snapshot() -> dict[str, Any]:
    """尚无流水线结果时，用清单生成占位行，避免前端空白。"""
    cfg = get_intl_config()
    rows: list[dict[str, Any]] = []
    try:
        input_dir = cfg.resolve(cfg.paths.input_dir)
        _, records = load_issuer_records(input_dir, cfg.input_files)
        for i, rec in enumerate(records, start=1):
            rows.append(_placeholder_row(i, rec["name"], rec.get("category") or ""))
    except Exception:
        # 回退：分类表全量占位
        for i, (name, cat) in enumerate(_CATEGORY_FALLBACK.items(), start=1):
            rows.append(_placeholder_row(i, name, cat))
    return {
        "updated_at": None,
        "source": "skeleton",
        "message": "尚未运行评级流水线，当前为占位数据。请点击「手动更新」。",
        "rows": rows,
        "running": _is_running(),
    }


def save_snapshot(rows: list[dict[str, Any]], *, source: str = "pipeline") -> Path:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "message": "",
        "rows": rows,
    }
    with SNAPSHOT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return SNAPSHOT_PATH


def _is_running() -> bool:
    with _LOCK:
        return _RUNNING


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def start_refresh_job(*, limit: int = 0, quick: bool = False) -> dict[str, Any]:
    global _RUNNING
    with _LOCK:
        if _RUNNING:
            for jid, job in JOBS.items():
                if job.get("status") in {"queued", "running"}:
                    return {
                        "job_id": jid,
                        "status": job["status"],
                        "message": "已有刷新任务在运行",
                        "accepted": False,
                    }
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "任务已排队",
            "total": 0,
            "done": 0,
            "error": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
        _RUNNING = True

    t = threading.Thread(
        target=_run_job,
        kwargs={"job_id": job_id, "limit": limit, "quick": quick},
        daemon=True,
        name=f"intl-ratings-{job_id}",
    )
    t.start()
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "已开始刷新国际评级",
        "accepted": True,
    }


def _run_job(*, job_id: str, limit: int, quick: bool) -> None:
    global _RUNNING
    try:
        with _LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["message"] = "正在抓取与分析…"

        get_intl_config.cache_clear()
        cfg = get_intl_config().model_copy(deep=True)
        if limit and limit > 0:
            cfg.runtime.max_issuers = int(limit)
        if quick:
            # 网页快速刷新：跳过耗时 Playwright / OpenFIGI
            cfg.sources.playwright_ratings = False
            cfg.sources.openfigi = False
            cfg.sources.sec_edgar = False
            cfg.runtime.sleep_between_issuers = 0.1

        # 分类映射
        input_dir = cfg.resolve(cfg.paths.input_dir)
        try:
            _, records = load_issuer_records(input_dir, cfg.input_files)
            cat_map = {r["name"]: r.get("category") or "" for r in records}
            names = [r["name"] for r in records]
        except Exception:
            cat_map = {}
            names = None

        if limit and limit > 0 and names:
            names = names[:limit]

        pipeline = IntlRatingsPipeline(cfg)
        report_rows, excel_path = pipeline.run(issuers=names, export=True)

        api_rows: list[dict[str, Any]] = []
        for i, row in enumerate(report_rows, start=1):
            d = row.to_excel_dict()
            name = d.get("发行体") or ""
            api_rows.append(
                {
                    "id": f"ir-{i}",
                    "issuer": name,
                    "category": _resolve_category(name, cat_map.get(name, "")),
                    "moodys": d.get("穆迪评级") or NR,
                    "sp": d.get("标普评级") or NR,
                    "fitch": d.get("惠誉评级") or NR,
                    "loss": d.get("债务人最近一期決算是否亏损(是/否)") or "",
                    "listed": d.get("是否上市（是/否）") or "",
                    "delisted": d.get("若上市，债务人是否被上市废止(是/否)") or "",
                    "priceDrop": d.get("债券价格是否大幅下跌（月环比跌幅超过5%）等") or "",
                    "noRatingReason": d.get("皆无评级的話请写明理由") or "",
                    "ratingChanged": d.get("评级是否变化") or "",
                    "rssUrl": "",
                }
            )

        save_snapshot(api_rows, source="pipeline_quick" if quick else "pipeline")
        with _LOCK:
            JOBS[job_id].update(
                {
                    "status": "succeeded",
                    "message": f"完成 {len(api_rows)} 家"
                    + (f"；Excel: {excel_path.name}" if excel_path else ""),
                    "total": len(api_rows),
                    "done": len(api_rows),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "excel": str(excel_path) if excel_path else "",
                }
            )
    except Exception as exc:
        logger.exception("国际评级刷新失败")
        with _LOCK:
            JOBS[job_id].update(
                {
                    "status": "failed",
                    "message": "刷新失败",
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    finally:
        with _LOCK:
            _RUNNING = False
