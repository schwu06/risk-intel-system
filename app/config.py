"""应用配置与业务模块 taxonomy。"""

from functools import lru_cache
from typing import Any, Literal

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

    # 界面二（主体评估）结构化分析
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Existing deployments keep the original report path until explicitly switched.
    industry_report_generation_mode: Literal["legacy", "grounded"] = "legacy"
    grounded_report_require_approval: bool = True
    # Automatic grounded -> legacy fallback is intentionally unsupported.
    grounded_report_allow_legacy_fallback: Literal[False] = False

    daily_pipeline_cron: str = "0 6 * * *"
    request_timeout_seconds: int = 120
    news_window_hours: int = 24
    news_fetch_body: bool = False
    news_max_body_items: int = 8
    # 外网偶发 DNS/超时：自动重试，提高一次采集成功率
    network_retry_attempts: int = 2
    network_retry_backoff_seconds: float = 1.2
    # 流水线韧性：先粗筛再 LLM、缓存、降级入库、异步默认开启
    pipeline_async_default: bool = True
    # LLM 单批处理条数（不是最终展示上限；展示不设上限）
    pipeline_llm_top_k: int = 12
    pipeline_llm_cache_hours: int = 168
    pipeline_mita_query_pause_seconds: float = 0.3
    # 秘塔「不足才补」目标条数。0 = 默认 24（与展示全量策略对齐，不再跟 LLM 批大小挂钩）
    pipeline_mita_min_items: int = 0
    # 主源整类失败（RSS/TDnet 硬故障）时强制跑秘塔补缺
    pipeline_mita_force_on_primary_fail: bool = True
    # SQLite 下默认串行，避免并行写库拖慢侧栏/上传等其它接口
    pipeline_module_parallel: bool = False
    pipeline_merge_llm: bool = True
    # 单模块主源采集上限（RSS/直连/新浪/TDnet）；0 或负数按 80
    pipeline_collect_max_items: int = 80
    rss_config_path: str = "config/rss_feeds.yaml"
    direct_sites_config_path: str = "config/direct_sites.yaml"
    entity_targets_config_path: str = "config/entity_targets.yaml"
    # 演示数据必须显式开启；正常运行不再因空结果写入 Mock。
    entity_demo_mode: bool = False
    # 舆情预警仅考虑观察期内的有效负面信用信号。
    entity_warning_lookback_days: int = 90


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

# 新闻日报时间窗（小时）
NEWS_WINDOW_HOURS_24 = 24
NEWS_WINDOW_HOURS_7X24 = 168  # 7×24


def news_window_label(hours: int) -> str:
    """界面/导出用时间窗文案。"""
    h = int(hours or NEWS_WINDOW_HOURS_24)
    if h >= NEWS_WINDOW_HOURS_7X24:
        return "7×24小时"
    return f"{h}小时"


# 三级页面与模块映射（深度研报走 IndustryAnalysis*，不占用 DailyRiskEntry）
PAGE_MODULES = {
    "daily_news": ("B", "C", "D"),
    "news_7x24": ("B", "C", "D"),
    "entity_assessment": ("A",),
}

PAGE_META = {
    "daily_news": {
        "path": "/daily-news",
        "title": "新闻日报",
        "subtitle": "近24小时重要资讯 · 区域 · 机构 · 宏观",
        "window_hours": NEWS_WINDOW_HOURS_24,
        "collect_label": "采集近24小时资讯",
        "empty_hint": "当前筛选条件下暂无条目。可通过侧边栏运行流水线采集近 24 小时资讯。",
    },
    "news_7x24": {
        "path": "/daily-news-7x24",
        "title": "新闻日报 · 7×24",
        "subtitle": "近七日按日快照 · 区域 · 机构 · 宏观",
        # 与近24小时共用 window_hours=24 按日快照，避免再跑 168 小时全量采集
        "window_hours": NEWS_WINDOW_HOURS_24,
        "collect_label": "采集当日资讯",
        "empty_hint": "该日暂无快照。将自动补采；也可点击侧边栏手动采集。",
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
    "intl_ratings": {
        "path": "/intl-ratings",
        "title": "国际评级",
        "subtitle": "发行体国际信用评级与监测",
    },
}


def modules_for_page(page_key: str) -> dict[str, str]:
    """返回某页面允许展示的模块 code→名称 子集。"""
    codes = PAGE_MODULES.get(page_key, ())
    return {c: MODULE_CODES[c] for c in codes if c in MODULE_CODES}

MODULE_A_TARGETS = [
    "Godiva",
    "普洛斯",
    "三菱商事",
    "三井物産",
    "伊藤忠商事",
    "住友商事",
    "丸紅",
    "デンソー",
    "日本郵船",
    "大和証券",
]
MODULE_A_CATEGORIES = [
    "司法与行政监管",
    "金融与经营数据",
    "公开舆论与社交媒体",
    "供应链与关联方",
]

MODULE_B_REGION_HINT = (
    "中东区域全量动态跟踪：严格限定中东地理范围（沙特、阿联酋、伊朗、以色列、卡塔尔、科威特等）；"
    "收录国家政策、官方声明、地缘政治与区域市场；有明确新信息即收，不要求必须是高风险。"
    "与宏观板块交叉时：常规外交/局部冲突/例行声明归本板块；"
    "若同时引发全球资产剧烈波动，可双投至每日宏观与市场情报。"
)

MODULE_C_TARGETS = [
    "三菱商事",
    "三井物産",
    "伊藤忠商事",
    "住友商事",
    "丸紅",
    "デンソー",
    "Denso",
    "日本郵船",
    "NYK",
    "大和証券",
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

# 新闻日报三板块路由说明（供提示词 / 文档）
DAILY_NEWS_ROUTE_HINT = (
    "分类路由优先级："
    "1) 主语为日本监控九企之一→C；"
    "2) 大宗商品价格/供需/通胀→D，仅中东产油国本土政策声明且未涉全球价格→B；"
    "3) 中东常规动态→B；全球（含中东）重大地缘且引发资产强反应→D；可双投[B,D]。"
)

MODULE_E_TEMPLATES = [
    "授信报告模板",
    "航空业风险分析",
    "行业信用基准分析",
]

RISK_LEVELS = ["低", "中", "高", "极高"]

# 兼容现有数据库字段的公开信息预警灯号。
CREDIT_LEVELS = ["正常", "关注", "预警", "高风险"]
CREDIT_LEVEL_ORDER = {"正常": 1, "关注": 2, "预警": 3, "高风险": 4}

# 仅保留给旧调用兼容；新主体规则使用独立的 credit_impact 字段。
RISK_TO_CREDIT = {
    "低": "正常",
    "中": "关注",
    "高": "预警",
    "极高": "高风险",
}

# 默认监控主体与主体专属信源统一维护在 config/entity_targets.yaml。

STRUCTURED_FIELDS_CN = [
    "标题",
    "关联企业",
    "风险类别",
    "风险等级",
    "核心摘要",
    "影响分析",
    "来源链接",
    "来源名称",
    "发布时间",
    # 主体评估专用；其它模块可留空。
    "资讯重要度",
    "影响方向",
    "信用风险信号",
    "主体相关性",
    "置信度",
]


def module_search_queries(
    module: str,
    report_date: str,
    *,
    entity_targets: list[str] | None = None,
    window_hours: int = NEWS_WINDOW_HOURS_24,
) -> list[dict[str, Any]]:
    """为各模块生成近 N 小时资讯检索查询。

    entity_targets: 主体评估（模块 A）可限定只搜指定主体名称列表。
    window_hours: 时效窗口；168 时按近一周检索。
    """
    queries: list[dict[str, Any]] = []
    # 避免把具体日期塞进检索词（易导致秘塔“未找到相关数据”）
    if int(window_hours or NEWS_WINDOW_HOURS_24) >= NEWS_WINDOW_HOURS_7X24:
        recency = "最新 过去一周 OR past week OR 近7天 OR 7 days OR 速报"
    else:
        recency = "最新 过去24小时 OR today OR 速报"
    if module == "A":
        targets = entity_targets or MODULE_A_TARGETS
        for target in targets:
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
        # 日语正式社名 + 適時開示/IR，减少中文转载噪音
        companies = [
            ("三菱商事", "Mitsubishi Corporation"),
            ("三井物産", "Mitsui & Co"),
            ("伊藤忠商事", "Itochu"),
            ("住友商事", "Sumitomo Corporation"),
            ("丸紅", "Marubeni"),
            ("デンソー", "Denso"),
            ("日本郵船", "NYK Line"),
            ("大和証券", "Daiwa Securities"),
        ]
        for jp_name, en_name in companies:
            queries.append(
                {
                    "module": "C",
                    "query": (
                        f"{jp_name} OR {en_name} "
                        f"適時開示 OR ニュースリリース OR IR OR 決算 {recency}"
                    ),
                    "metadata": {"company": jp_name},
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
