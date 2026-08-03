"""应用配置与业务模块 taxonomy。"""

from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "企业风险情报日报系统"
    database_url: str = "sqlite:///./data/risk_intel.db"
    secret_key: str = "dev-secret-key"

    mita_api_base_url: str = "https://metaso.cn/api/v1"
    mita_api_key: str = ""

    deepseek_api_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    daily_pipeline_cron: str = "0 6 * * *"
    request_timeout_seconds: int = 120
    news_window_hours: int = 24
    news_fetch_body: bool = True
    news_max_body_items: int = 8
    # 外网偶发 DNS/超时：自动重试，提高一次采集成功率
    network_retry_attempts: int = 3
    network_retry_backoff_seconds: float = 1.5
    # 流水线韧性：先粗筛再 LLM、缓存、降级入库、异步默认开启
    pipeline_async_default: bool = True
    pipeline_llm_top_k: int = 12
    pipeline_llm_cache_hours: int = 168
    pipeline_mita_query_pause_seconds: float = 0.8
    rss_config_path: str = "config/rss_feeds.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# 业务模块与监控 taxonomy（中文）
# ---------------------------------------------------------------------------

MODULE_CODES = {
    "A": "企业与品牌风险",
    "B": "中东日报",
    "C": "日本重点大型企业",
    "D": "每日宏观与市场情报",
    "E": "授信报告与行业分析",
}

# 三级页面与模块映射（深度研报走 IndustryAnalysis*，不占用 DailyRiskEntry）
PAGE_MODULES = {
    "daily_news": ("B", "C", "D"),
    "entity_assessment": ("A",),
}

PAGE_META = {
    "daily_news": {
        "path": "/daily-news",
        "title": "风险日报",
        "subtitle": "近24小时重要资讯 · 区域 · 机构 · 宏观",
    },
    "entity_assessment": {
        "path": "/entity-assessment",
        "title": "主体评估",
        "subtitle": "重点企业动态风险监测",
    },
    "deep_reports": {
        "path": "/deep-reports",
        "title": "深度研报",
        "subtitle": "行业与授信长篇结构化分析",
    },
}


def modules_for_page(page_key: str) -> dict[str, str]:
    """返回某页面允许展示的模块 code→名称 子集。"""
    codes = PAGE_MODULES.get(page_key, ())
    return {c: MODULE_CODES[c] for c in codes if c in MODULE_CODES}

MODULE_A_TARGETS = ["Godiva", "普洛斯", "GLP"]
MODULE_A_CATEGORIES = [
    "司法与行政监管",
    "金融与经营数据",
    "公开舆论与社交媒体",
    "供应链与关联方",
]

MODULE_B_REGION_HINT = (
    "中东地区近24小时重要资讯：国家政策、官方声明、地缘政治与区域市场动态；"
    "有明确新信息即收录，不要求必须是高风险事件；无材料则留空，不编造。"
)

MODULE_C_TARGETS = [
    "三菱商事",
    "三井物产",
    "伊藤忠商事",
    "住友商事",
    "丸红",
    "电装",
    "Denso",
    "日本邮船",
    "NYK",
    "大和证券",
    "Daiwa Securities",
]

MODULE_C_PILLARS = [
    "核心业务与市场周期",
    "运营与供应链",
    "强合规、监管处分与法律纠纷",
    "转型与搁浅资产",
    "集团治理与内部风险",
]

MODULE_D_TOPICS = [
    "原油",
    "LNG",
    "贵金属",
    "股市资本市场异动",
    "日本央行货币政策",
    "美联储货币政策",
    "重大地缘事件",
]

MODULE_E_TEMPLATES = [
    "授信报告模板",
    "航空业风险分析",
    "行业信用基准分析",
]

RISK_LEVELS = ["低", "中", "高", "极高"]

# 授信风险等级（递进，避免「预警」与「高风险」语义重叠）
CREDIT_LEVELS = ["正常", "关注", "预警", "高风险"]
CREDIT_LEVEL_ORDER = {"正常": 1, "关注": 2, "预警": 3, "高风险": 4}

# 事件风险等级 → 授信建议等级
RISK_TO_CREDIT = {
    "低": "正常",
    "中": "关注",
    "高": "预警",
    "极高": "高风险",
}

# 默认监控主体（主体评估页种子数据）
DEFAULT_TARGET_ENTITIES = [
    {
        "name": "Godiva",
        "display_name": "歌帝梵 Godiva",
        "aliases": "歌帝梵,Godiva Chocolatier",
        "industry": "消费品 / 巧克力",
        "region": "全球",
    },
    {
        "name": "普洛斯",
        "display_name": "普洛斯 GLP",
        "aliases": "GLP,Global Logistic Properties,普洛斯",
        "industry": "物流地产",
        "region": "亚太 / 全球",
    },
    {
        "name": "GLP",
        "display_name": "GLP",
        "aliases": "普洛斯,Global Logistic Properties",
        "industry": "物流地产",
        "region": "亚太 / 全球",
    },
]

STRUCTURED_FIELDS_CN = [
    "标题",
    "关联企业",
    "风险类别",
    "风险等级",
    "核心摘要",
    "影响分析",
    "来源链接",
    "发布时间",
]


def module_search_queries(module: str, report_date: str) -> list[dict[str, Any]]:
    """为各模块生成近 24 小时资讯检索查询。"""
    queries: list[dict[str, Any]] = []
    # 避免把具体日期塞进检索词（易导致秘塔“未找到相关数据”）
    recency = "最新 过去24小时 OR today OR 速报"
    if module == "A":
        for target in ["Godiva", "普洛斯 GLP"]:
            for cat in MODULE_A_CATEGORIES:
                queries.append(
                    {
                        "module": "A",
                        "query": f"{target} {cat} 新闻 动态 {recency}",
                        "metadata": {"target": target, "category": cat},
                    }
                )
    elif module == "B":
        queries.append(
            {
                "module": "B",
                "query": f"中东 新闻 政策 官方声明 地缘政治 {recency}",
                "metadata": {"region": "中东"},
            }
        )
        queries.append(
            {
                "module": "B",
                "query": f"Middle East news geopolitics official statement {recency}",
                "metadata": {"region": "中东"},
            }
        )
    elif module == "C":
        # 仅中文主体名，避免中英重复打爆配额
        companies = [
            "三菱商事",
            "三井物产",
            "伊藤忠商事",
            "住友商事",
            "丸红",
            "电装",
            "日本邮船",
            "大和证券",
        ]
        for company in companies:
            queries.append(
                {
                    "module": "C",
                    "query": f"{company} 新闻 披露 经营 动态 {recency}",
                    "metadata": {"company": company},
                }
            )
    elif module == "D":
        for topic in MODULE_D_TOPICS:
            queries.append(
                {
                    "module": "D",
                    "query": f"{topic} 市场 新闻 {recency}",
                    "metadata": {"topic": topic},
                }
            )
    elif module == "E":
        queries.append(
            {
                "module": "E",
                "query": f"行业风险 授信 分析 {recency}",
                "metadata": {"topic": "industry"},
            }
        )
        queries.append(
            {
                "module": "E",
                "query": f"授信 行业信用 航空业 风险分析 {recency}",
                "metadata": {"template": "行业分析"},
            }
        )
    return queries
