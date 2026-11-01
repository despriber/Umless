"""LLM 调用：任意 OpenAI 兼容的 /chat/completions 接口。

- Polisher：语音输入的润色（强度 × 场景）
- LLMClient.chat_json：诊断、出题、练习评测等需要结构化输出的任务
"""

import json
import re

import httpx

from .config import LLMConfig
from .i18n import t
from .prompts import build_messages

# 部分模型（Qwen3、DeepSeek-R1 等）即使关闭思考也可能输出 <think> 块
_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, cfg: LLMConfig, model: str | None = None, timeout_s: float | None = None) -> None:
        self._cfg = cfg
        self.model = model or cfg.model
        self._client = httpx.Client(timeout=timeout_s or cfg.timeout_s)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return headers

    def chat(self, messages: list[dict], temperature: float | None = None, **extra) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self._cfg.temperature if temperature is None else temperature,
            "stream": False,
            **self._cfg.extra_body,
            **extra,
        }
        try:
            r = self._client.post(
                self._cfg.base_url.rstrip("/") + "/chat/completions", headers=self._headers(), json=body
            )
        except httpx.TimeoutException as e:
            raise LLMError(t("llm.timeout")) from e
        except httpx.HTTPError as e:
            raise LLMError(t("llm.connect_failed", error=e)) from e
        if r.status_code >= 400:
            raise LLMError(t("llm.http_error", status=r.status_code, body=r.text[:200]))
        try:
            content = r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(t("llm.bad_response", body=r.text[:200])) from e
        return _THINK.sub("", content).strip()

    def chat_json(self, messages: list[dict], temperature: float = 0.3) -> dict:
        """要求返回 JSON 对象。先用 response_format，接口不支持时去掉重试。"""
        try:
            text = self.chat(messages, temperature, response_format={"type": "json_object"})
        except LLMError as e:
            if "400" not in str(e) and "422" not in str(e):
                raise
            text = self.chat(messages, temperature)
        return parse_json(text)

    def list_models(self) -> list[str]:
        try:
            r = self._client.get(self._cfg.base_url.rstrip("/") + "/models", headers=self._headers())
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise LLMError(t("llm.list_failed", error=e)) from e


def parse_json(text: str) -> dict:
    text = _FENCE.sub("", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 模型在 JSON 前后加了说明文字时，截取最外层的大括号
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(t("llm.no_json"))
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(t("llm.bad_json")) from e
    if not isinstance(data, dict):
        raise LLMError(t("llm.not_object"))
    return data


class Polisher:
    def __init__(self, cfg: LLMConfig, glossary: list[str]) -> None:
        self._llm = LLMClient(cfg)
        self._glossary = glossary

    @property
    def model(self) -> str:
        return self._llm.model

    def polish(self, raw_text: str, scene: str = "default", intensity: str = "moderate") -> str:
        return self._llm.chat(build_messages(raw_text, self._glossary, scene, intensity))
