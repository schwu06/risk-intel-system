import json
from types import SimpleNamespace
from unittest.mock import patch

from app.services.display_zh import build_display_cards, has_publishable_content_detail


def test_hides_news_without_real_content_detail() -> None:
    assert not has_publishable_content_detail("WTI油价维持高位", "Google News")
    assert not has_publishable_content_detail("市场新闻", "市场新闻")
    assert not has_publishable_content_detail("市场新闻", "短摘要")
    assert not has_publishable_content_detail(
        "随着BTC价格保持稳定，国债收益率飙升，FOMC利率会议纪要成为焦点：Crypto Daily – CoinDesk",
        "随着BTC价格保持稳定，国债收益率飙升，FOMC利率会议纪要成为焦点：Crypto Daily &nbsp;&nbsp; CoinDesk。",
    )
    assert not has_publishable_content_detail(
        "中东航司利润预期骤降115亿美元，从盈利72亿到亏损43亿，地缘政治冲击全球中转模式 – 虎嗅",
        "中东航司利润预期骤降115亿美元，从盈利72亿到亏损43亿，地缘政治冲击全球中转模式 &nbsp;&nbsp; 虎嗅。",
    )


def test_keeps_news_with_substantive_detail() -> None:
    detail = (
        "报道披露了事件发生的时间、涉及主体和主要过程，并说明相关市场价格变化、"
        "机构回应及后续安排。目前部分数据仍有待官方进一步确认，原始来源提供了完整说明。"
    )
    assert has_publishable_content_detail("市场新闻", detail)
    assert not has_publishable_content_detail("市场新闻", "正文未取得")


def test_display_card_shows_recovered_body() -> None:
    detail = (
        "住友商事社长上野表示，公司将持续出售低效资产并提高资本效率。"
        "声明提到将检视现有投资组合、加快资产周转，并评估对融资成本和股东回报的影响。"
        "目前尚未公布具体出售清单或金额。"
    )
    entry = SimpleNamespace(
        id=2,
        module_code="C",
        title="住友商事社长谈资产流动",
        summary="正文未取得",
        impact_analysis="",
        related_company="住友商事",
        risk_category="金融与经营数据",
        category_tag=None,
        risk_level="低",
        source_url="https://example.com/sumitomo",
        source_title="日本经济新闻",
        published_at=None,
        structured_json='{"source_body": %s}' % json.dumps(detail, ensure_ascii=False),
    )
    with patch("app.services.display_zh.get_cached_items", return_value=None):
        with patch("app.services.display_zh._translate_batch", return_value={}):
            card = build_display_cards(object(), [entry], social_resolver=lambda _: {})[0]
    assert "资本效率" in card.overview
    assert card.title == entry.title


def test_keeps_title_card_when_content_detail_is_unavailable() -> None:
    entry = SimpleNamespace(
        id=1,
        module_code="D",
        title="市场新闻标题 – 示例媒体",
        summary="市场新闻标题 &nbsp;&nbsp; 示例媒体",
        impact_analysis="",
        related_company=None,
        risk_category="市场风险",
        category_tag=None,
        risk_level="低",
        source_url="https://example.com/news",
        source_title="示例媒体",
        published_at=None,
    )

    with patch("app.services.display_zh.get_cached_items", return_value=None):
        with patch("app.services.display_zh._translate_batch", return_value={}):
            card = build_display_cards(object(), [entry], social_resolver=lambda _: {})[0]

    assert card.title == entry.title
    assert card.overview == ""
    assert card.source_url == entry.source_url
