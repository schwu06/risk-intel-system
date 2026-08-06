"""DeepSeek 结构化分析 API 封装。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.config import MODULE_CODES, RISK_LEVELS, STRUCTURED_FIELDS_CN, get_settings
from app.services.api_keys import validate_deepseek_key
from app.services.http_retry import with_retries

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """你是一名企业与市场资讯编辑。根据提供的检索结果或原文，提取近 {hours_label}内的相关新闻资讯条目。
输出必须是合法 JSON 对象（json），且仅包含一个键 "items"，其值为数组。数组每个元素包含以下中文字段（键名必须与下列完全一致）：
标题、关联企业、风险类别、风险等级、核心摘要、影响分析、来源链接、来源名称、发布时间

规则：
1. 全部使用简体中文，但品牌名 Godiva 必须保持英文原名，不得翻译为歌帝梵或其他中文。日文/英文材料必须先理解再写成中文标题与摘要，禁止直接粘贴外文原文作为标题或摘要。
2. 「风险等级」在此表示资讯重要度，只能是：低、中、高、极高 之一（重要新闻用中/高，一般动态用低，重大突发用极高）。
3. 「风险类别」填写资讯主题分类即可（如：政策动态、企业经营、市场行情、地缘政治、监管披露等），不必是负面风险。
4. 目标是尽量完整覆盖材料中的相关新闻；政策更新、经营披露、市场异动、官方声明、人事与投资等均可纳入，不必非要有“风险/危机/处罚”。
5. 仅保留「最近 {hours_label}内」或材料中明确标注为最新/刚刚发布的信息；无法判断时效且内容明显陈旧的不要输出。
6. 同一事件若材料中出现多条（含不同媒体/社媒转述），只输出一条最完整来源；其余重复不要重复输出。不要因“重要度一般”而丢弃不重复的相关新闻。
7. 「中东日报」：严格中东区域动态，低门槛有新信息即输出；与宏观交叉时按路由规则（常规→中东；重大市场冲击可双投）。
8. 发布时间尽量保留材料中的原始时间字符串；若无则留空字符串。
9. 不要编造材料中不存在的事实；若材料为空或全无可用资讯，返回 {{"items": []}}。
10. 不要输出 markdown 代码块，只输出 JSON 对象（json）。
11. 忽略「披露检索 / 法定披露 / TDnet检索入口 / EDINET」等仅为检索入口、没有具体公告正文或 PDF 的占位链接；但带具体 PDF/公告标题的適時開示应正常提取。
12. 标题必须是可阅读的新闻标题（事件/政策/经营动态），不要输出“某某 披露检索”这类模板标题。
13. 「日本重点大型企业」：主语须为监控企业（三菱商事、三井物産、伊藤忠、住友商事、丸紅、デンソー、日本郵船、大和証券）或其核心业务关联方；保留 IR/披露/经营/监管；丢弃无关元数据。
14. 「每日宏观与市场情报」：大宗商品价格/供需、美联储/日银、股市资本市场，以及可能剧烈冲击金融市场的重大地缘（含中东重大冲击）。
15. 「核心摘要」「影响分析」须基于材料中的具体事实撰写；材料较短时可简写，不要因此省略整条新闻。
16. 「来源名称」填写媒体/机构中文惯用名（如：路透社、日本经济新闻、NHK、TDnet）；可根据链接域名判断；无法判断时填空字符串，禁止臆造。
17. 尽量一一对应材料中的候选条目输出；不要为了“精选”而大量删减不重复条目。
18. 若材料明显不属于当前模块口径（例如宏观源里出现监控企业主语新闻），仍可提取，系统后续会按路由规则改投。"""

AUTHORITY_FIRST_PROMPT_TEMPLATE = """你是一名企业与市场资讯编辑。你将收到「权威数据源」与可选的「网络检索补充」。
必须优先从权威数据源提取事实；仅当权威数据源信息不足时，才使用网络检索补充，并在摘要中注明信息来源层级。
只保留近 {hours_label}内（或材料明确为最新）的相关新闻资讯；尽量完整覆盖，仅合并同一事件的重复报道。
输出必须是合法 JSON 对象（json），字段与常规分析任务完全一致（含「发布时间」「来源名称」等中文字段）；外文材料须译成简体中文后再输出。
不要编造权威数据源中不存在的内容。不要输出 markdown 代码块，只输出 json。"""


def _hours_label(window_hours: int | None) -> str:
    h = int(window_hours or 24)
    if h >= 168:
        return "7×24小时（168小时）"
    return f"{h}小时"


def build_system_prompt(*, window_hours: int | None = None, authority_first: bool = False) -> str:
    label = _hours_label(window_hours)
    tmpl = AUTHORITY_FIRST_PROMPT_TEMPLATE if authority_first else SYSTEM_PROMPT_TEMPLATE
    return tmpl.format(hours_label=label)


# 兼容旧引用
SYSTEM_PROMPT = build_system_prompt(window_hours=24)
AUTHORITY_FIRST_PROMPT = build_system_prompt(window_hours=24, authority_first=True)


INDUSTRY_ANALYSIS_PROMPT = """你是一名银行授信与行业研究分析师。根据提供的行业数据源与模板，撰写结构化长篇分析报告。
输出必须是合法 JSON 对象（json），包含以下键：
- title: 报告标题（字符串）
- sections: 数组，每个元素含 heading（章节标题）与 content（正文，可多段）
- summary: 执行摘要（字符串）
- risk_outlook: 风险展望（字符串）
- key_metrics: 数组，可选，元素含 name 与 value（用于图表）
规则：简体中文、客观审慎、不编造数据；若模板缺失则采用通用行业分析框架（行业概况、竞争格局、财务与信用、政策与监管、风险因素、结论与建议）。不要输出 markdown 代码块，只输出 json。"""



class DeepSeekAnalyzer:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.deepseek_api_base_url).rstrip("/")
        self.api_key = api_key or settings.deepseek_api_key
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.request_timeout_seconds
        self.retry_attempts = int(getattr(settings, "network_retry_attempts", 3) or 3)
        self.retry_backoff = float(getattr(settings, "network_retry_backoff_seconds", 1.5) or 1.5)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def analyze_raw(
        self,
        raw_text: str,
        module_code: str,
        context: Optional[dict[str, Any]] = None,
        *,
        authority_first: bool = False,
    ) -> list[dict[str, Any]]:
        system = build_system_prompt(
            window_hours=(context or {}).get("window_hours"),
            authority_first=authority_first,
        )
        module_name = MODULE_CODES.get(module_code.upper(), module_code)
        user_content = (
            f"模块: {module_name}\n"
            f"附加上下文: {json.dumps(context or {}, ensure_ascii=False)}\n\n"
            f"原始材料:\n{raw_text}"
        )
        return self._chat_json_list(system, user_content)

    def analyze_industry(
        self,
        raw_text: str,
        industry_name: str,
        company_name: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        user_content = (
            f"行业: {industry_name}\n"
            f"企业: {company_name or '未指定'}\n"
            f"附加上下文: {json.dumps(context or {}, ensure_ascii=False)}\n\n"
            f"原始材料:\n{raw_text}"
        )
        return self._chat_json_object(INDUSTRY_ANALYSIS_PROMPT, user_content)

    def _chat_json_list(self, system_prompt: str, user_content: str) -> list[dict[str, Any]]:
        content = self._request_chat(system_prompt, user_content)
        return self._parse_structured_list(content)

    def _chat_json_object(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        content = self._request_chat(system_prompt, user_content)
        text = content.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("行业分析 JSON 解析失败")
            return {"title": "分析报告", "sections": [], "summary": text[:2000], "risk_outlook": ""}
        if not isinstance(parsed, dict):
            return {"title": "分析报告", "sections": [], "summary": str(parsed), "risk_outlook": ""}
        return parsed

    def _request_chat(self, system_prompt: str, user_content: str) -> str:
        validate_deepseek_key(self.api_key)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.base_url}/v1/chat/completions"

        def _do_chat() -> dict[str, Any]:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=self._headers(), json=body)
                    resp.raise_for_status()
                    try:
                        return resp.json()
                    except ValueError as exc:
                        snippet = (resp.text or "")[:500]
                        logger.error("DeepSeek 返回非 JSON: %s", snippet)
                        raise RuntimeError(f"DeepSeek 响应解析失败: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                body_text = (exc.response.text or "")[:300]
                if exc.response.status_code in (408, 425, 429, 500, 502, 503, 504):
                    raise
                logger.error("DeepSeek HTTP %s: %s", exc.response.status_code, body_text)
                raise RuntimeError(
                    f"DeepSeek 分析失败 (HTTP {exc.response.status_code}): {body_text or exc}"
                ) from exc
            except httpx.HTTPError as exc:
                logger.error("DeepSeek 请求失败: %s", exc)
                raise RuntimeError(f"DeepSeek 分析失败: {exc}") from exc

        try:
            data = with_retries(
                _do_chat,
                attempts=self.retry_attempts,
                backoff_seconds=self.retry_backoff,
                label="DeepSeek",
            )
        except httpx.HTTPStatusError as exc:
            body_text = (exc.response.text or "")[:300]
            raise RuntimeError(
                f"DeepSeek 分析失败 (HTTP {exc.response.status_code}): {body_text or exc}"
            ) from exc

        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "[]")
        )

    def _parse_structured_list(self, content: str) -> list[dict[str, Any]]:
        text = content.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                logger.warning("无法解析 DeepSeek 输出为 JSON: %s", text[:200])
                return []
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("DeepSeek 输出二次 JSON 解析失败")
                return []

        if isinstance(parsed, dict):
            for key in ("items", "entries", "data", "结果", "风险条目"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                if all(k in parsed for k in STRUCTURED_FIELDS_CN[:1]):
                    parsed = [parsed]
                else:
                    parsed = []

        if not isinstance(parsed, list):
            return []

        normalized: list[dict[str, Any]] = []
        for row in parsed:
            if not isinstance(row, dict):
                continue
            item = {field: str(row.get(field, "") or "").strip() for field in STRUCTURED_FIELDS_CN}
            level = item.get("风险等级", "中")
            if level not in RISK_LEVELS:
                item["风险等级"] = "中"
            if not item.get("标题") and not item.get("核心摘要"):
                continue
            normalized.append(item)
        return normalized
