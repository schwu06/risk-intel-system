"""命令行：初始化数据库并写入演示条目（无外部 API）。"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database.session import SessionLocal, init_db
from app.services.domain_rules import seed_default_domains
from app.services.pipeline import RiskPipeline


def main():
    init_db()
    db = SessionLocal()
    try:
        seed_default_domains(db)
        today = date.today()
        demo = [
            {
                "标题": "示例：品牌舆情监测",
                "关联企业": "Godiva",
                "风险类别": "公开舆论与社交媒体",
                "风险等级": "中",
                "核心摘要": "此为系统初始化演示条目，用于验证仪表盘与 Word 导出。接入 MiTa 与 DeepSeek 后将由流水线自动替换。",
                "影响分析": "对授信组合无即时实质影响，需持续跟踪。",
                "来源链接": "https://example.com/demo",
            }
        ]
        RiskPipeline(db).ingest_manual_entries("A", today, demo)
        # 修正历史演示数据中的品牌名
        from app.database.models import DailyRiskEntry

        for row in db.query(DailyRiskEntry).filter(DailyRiskEntry.related_company == "歌帝梵").all():
            row.related_company = "Godiva"
            if "歌帝梵" in (row.summary or ""):
                row.summary = row.summary.replace("歌帝梵", "Godiva")
        db.commit()
        print(f"初始化完成，演示条目日期: {today.isoformat()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
