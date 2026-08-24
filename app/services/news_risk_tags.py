"""新闻汇总的银行风险标签与审慎等级规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass

RISK_TYPES = (
    "信贷风险",
    "市场风险",
    "流动性与资产负债",
    "合规与反洗钱",
    "国别与地缘",
    "操作与网络安全",
    "治理与信息披露",
)

_KEYWORDS = {
    "信贷风险": ("违约", "重组", "评级", "展望", "坏账", "减值", "核销", "担保失效", "偿债", "现金流", "盈利预警"),
    "市场风险": ("利率", "汇率", "股指", "债价", "油价", "原油", "黄金", "贵金属", "大宗商品", "日元", "美元"),
    "流动性与资产负债": ("融资冻结", "流动性", "挤兑", "存款流失", "利差", "再融资", "债发", "期限错配", "融资成本"),
    "合规与反洗钱": ("制裁", "罚单", "罚款", "吊销", "许可", "反洗钱", "aml", "kyc", "出口管制", "监管整改", "制裁名单"),
    "国别与地缘": ("战争", "冲突", "航道", "港口", "管道", "能源供应中断", "资本管制", "国有化", "政权"),
    "操作与网络安全": ("网络攻击", "黑客", "系统故障", "支付清算", "结算中断", "服务中断", "营运停摆", "数据泄露"),
    "治理与信息披露": ("会计更正", "造假", "重大遗漏", "信息披露", "审计", "高管辞任", "董事辞任", "财务更正"),
}

_PRIORITY = ("合规与反洗钱", "信贷风险", "流动性与资产负债", "操作与网络安全", "治理与信息披露", "国别与地缘", "市场风险")
_POSITIVE_OR_ROUTINE = ("合作", "活动", "营销", "获奖", "任命", "推出", "增长", "上调", "利润增长", "例行声明")
_RUMOUR = ("传闻", "据悉", "消息人士", "未经证实", "或将", "可能", "拟")
_EXTREME = ("违约已", "已违约", "重组已", "已重组", "停牌", "挤兑", "融资冻结", "制裁生效", "吊销许可", "航道关闭", "支付清算中断")
_HIGH = ("盈利预警", "展望负面", "下调展望", "制裁宣布", "军事升级", "航道紧张", "大幅下跌", "重大系统故障")


@dataclass(frozen=True)
class NewsRiskAssessment:
    tags: tuple[str, ...]
    level: str


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in text for term in terms)


def assess_news_risk(*, title: str | None, summary: str | None, impact: str | None, stored_level: str | None = None) -> NewsRiskAssessment:
    """仅按已抓取内容打标签；主类型 1 个、辅类型最多 1 个。"""
    body = " ".join(str(value or "") for value in (title, summary, impact)).casefold()
    scores = {kind: sum(1 for term in terms if term.casefold() in body) for kind, terms in _KEYWORDS.items()}
    matched = [kind for kind, score in scores.items() if score]
    matched.sort(key=lambda kind: (-scores[kind], _PRIORITY.index(kind)))
    tags = tuple(matched[:2])

    # 无风险事实的普通资讯只标为低；不为凑标签硬贴类型。
    if not tags:
        return NewsRiskAssessment(tags=(), level="低")

    content_missing = not (summary or "").strip() or "正文未取得" in (summary or "")
    if _contains(body, _EXTREME) and not _contains(body, _RUMOUR):
        level = "极高"
    elif _contains(body, _HIGH) or ("制裁" in body and _contains(body, ("宣布", "公布", "实施"))):
        level = "高"
    elif _contains(body, _RUMOUR):
        level = "中"
    elif any(scores[kind] >= 1 for kind in tags):
        level = "中"
    else:
        level = "低"

    # 标题或正文缺失不能超过中；传闻最高高（当前无正文时已被上限进一步收窄）。
    if content_missing and level in {"高", "极高"}:
        level = "中"
    if _contains(body, _RUMOUR) and level == "极高":
        level = "高"
    # “宣布/发布”本身不是中性信号：例如“宣布制裁”仍须按合规风险处理。
    if _contains(body, _POSITIVE_OR_ROUTINE) and not _contains(body, _HIGH + _EXTREME):
        level = "低"
    return NewsRiskAssessment(tags=tags, level=level)
