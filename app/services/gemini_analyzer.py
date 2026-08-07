"""Gemini 结构化分析 API 封装（界面二 / 主体评估专用）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.services.api_keys import validate_gemini_key
from app.services.deepseek_analyzer import DeepSeekAnalyzer
from app.services.http_retry import with_retries

logger = logging.getLogger(__name__)


class GeminiAnalyzer(DeepSeekAnalyzer):
    """与 DeepSeekAnalyzer 同接口，仅替换底层 HTTP 调用为 Gemini。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.gemini_api_base_url).rstrip("/")
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model
        self.timeout = timeout or settings.request_timeout_seconds
        self.retry_attempts = int(getattr(settings, "network_retry_attempts", 3) or 3)
        self.retry_backoff = float(getattr(settings, "network_retry_backoff_seconds", 1.5) or 1.5)

    def _request_chat(self, system_prompt: str, user_content: str) -> str:
        validate_gemini_key(self.api_key)
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        params = {"key": self.api_key}

        def _do_chat() -> dict[str, Any]:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, params=params, json=body)
                    resp.raise_for_status()
                    try:
                        return resp.json()
                    except ValueError as exc:
                        snippet = (resp.text or "")[:500]
                        logger.error("Gemini 返回非 JSON: %s", snippet)
                        raise RuntimeError(f"Gemini 响应解析失败: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                body_text = (exc.response.text or "")[:300]
                if exc.response.status_code in (408, 425, 429, 500, 502, 503, 504):
                    raise
                logger.error("Gemini HTTP %s: %s", exc.response.status_code, body_text)
                raise RuntimeError(
                    f"Gemini 分析失败 (HTTP {exc.response.status_code}): {body_text or exc}"
                ) from exc
            except httpx.HTTPError as exc:
                logger.error("Gemini 请求失败: %s", exc)
                raise RuntimeError(f"Gemini 分析失败: {exc}") from exc

        try:
            data = with_retries(
                _do_chat,
                attempts=self.retry_attempts,
                backoff_seconds=self.retry_backoff,
                label="Gemini",
            )
        except httpx.HTTPStatusError as exc:
            body_text = (exc.response.text or "")[:300]
            raise RuntimeError(
                f"Gemini 分析失败 (HTTP {exc.response.status_code}): {body_text or exc}"
            ) from exc

        candidates = data.get("candidates") or []
        if not candidates:
            block_reason = (data.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise RuntimeError(f"Gemini 内容被拦截: {block_reason}")
            raise RuntimeError("Gemini 未返回候选结果")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts = [str(p.get("text") or "") for p in parts if p.get("text")]
        if not text_parts:
            raise RuntimeError("Gemini 返回空内容")
        return "".join(text_parts)
