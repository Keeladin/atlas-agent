from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .contracts import ModelRequest, ModelResponse, ProviderSpec


class ProviderHTTPError(RuntimeError):
    pass


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ProviderHTTPError(f"HTTP {exc.code} from provider: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderHTTPError(f"Provider connection failed: {exc}") from exc
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderHTTPError("Provider returned non-JSON response.") from exc
    if not isinstance(value, dict):
        raise ProviderHTTPError("Provider returned a non-object JSON response.")
    return value


def _require_env(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if not value:
        raise ProviderHTTPError(f"Required provider credential is not configured: {name}")
    return value


@dataclass
class OpenAIResponsesProvider:
    spec: ProviderSpec
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com"
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = _require_env(self.api_key_env)
        payload: dict[str, Any] = {"model": self.spec.model, "input": request.input}
        if request.system:
            payload["instructions"] = request.system
        raw = _post_json(
            self.base_url.rstrip("/") + "/v1/responses",
            {"Authorization": f"Bearer {key}"},
            payload,
            self.timeout_seconds,
        )
        text = str(raw.get("output_text") or "").strip()
        if not text:
            pieces: list[str] = []
            for item in raw.get("output", []) if isinstance(raw.get("output"), list) else []:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                    if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                        pieces.append(str(content.get("text") or ""))
            text = "\n".join(piece for piece in pieces if piece).strip()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=dict(usage))


@dataclass
class OpenAICompatibleChatProvider:
    """OpenAI-compatible chat endpoint for LM Studio, xAI-style gateways, etc."""

    spec: ProviderSpec
    base_url: str
    api_key_env: str | None = None
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = _require_env(self.api_key_env)
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.input})
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        raw = _post_json(
            self.base_url.rstrip("/") + "/v1/chat/completions",
            headers,
            {"model": self.spec.model, "messages": messages},
            self.timeout_seconds,
        )
        choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                text = str(message.get("content") or "").strip()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=dict(usage))


@dataclass
class AnthropicMessagesProvider:
    spec: ProviderSpec
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"
    max_tokens: int = 4096
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = _require_env(self.api_key_env)
        payload: dict[str, Any] = {
            "model": self.spec.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": request.input}],
        }
        if request.system:
            payload["system"] = request.system
        raw = _post_json(
            self.base_url.rstrip("/") + "/v1/messages",
            {"x-api-key": str(key), "anthropic-version": self.api_version},
            payload,
            self.timeout_seconds,
        )
        parts = raw.get("content") if isinstance(raw.get("content"), list) else []
        text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text").strip()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=dict(usage))


@dataclass
class GeminiGenerateContentProvider:
    spec: ProviderSpec
    api_key_env: str = "GEMINI_API_KEY"
    base_url: str = "https://generativelanguage.googleapis.com"
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = _require_env(self.api_key_env)
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": request.input}]}]}
        if request.system:
            payload["system_instruction"] = {"parts": [{"text": request.system}]}
        raw = _post_json(
            self.base_url.rstrip("/") + f"/v1beta/models/{self.spec.model}:generateContent",
            {"x-goog-api-key": str(key)},
            payload,
            self.timeout_seconds,
        )
        candidates = raw.get("candidates") if isinstance(raw.get("candidates"), list) else []
        text_parts: list[str] = []
        if candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict):
                for part in content.get("parts", []) if isinstance(content.get("parts"), list) else []:
                    if isinstance(part, dict) and "text" in part:
                        text_parts.append(str(part.get("text") or ""))
        usage = raw.get("usageMetadata") if isinstance(raw.get("usageMetadata"), dict) else {}
        return ModelResponse(text="\n".join(text_parts).strip(), provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=dict(usage))
