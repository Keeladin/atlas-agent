from __future__ import annotations

import base64
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



def _anthropic_content(request: ModelRequest) -> str | list[dict[str, Any]]:
    if not request.content:
        return request.input
    blocks: list[dict[str, Any]] = []
    if request.input:
        blocks.append({"type": "text", "text": request.input})
    for part in request.content:
        if part.kind == "text":
            if part.text:
                blocks.append({"type": "text", "text": part.text})
            continue
        if part.data is None or not part.media_type:
            raise ProviderHTTPError(f"{part.kind} model content requires bytes and media_type")
        source = {
            "type": "base64",
            "media_type": part.media_type,
            "data": base64.b64encode(part.data).decode("ascii"),
        }
        if part.kind == "image":
            blocks.append({"type": "image", "source": source})
        elif part.kind == "document":
            blocks.append({"type": "document", "source": source})
        else:
            raise ProviderHTTPError(f"unsupported model content kind: {part.kind}")
    if not blocks:
        raise ProviderHTTPError("model request has no usable content")
    return blocks


def _reject_multimodal(request: ModelRequest, provider_name: str) -> None:
    if request.content:
        raise ProviderHTTPError(f"{provider_name} adapter does not yet implement multimodal ModelRequest content")


_ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS = {
    # Anthropic structured outputs accept the structural schema but reject a
    # number of validation-only JSON Schema keywords. Atlas retains and
    # enforces the complete original schema after generation.
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties",
}

def _anthropic_output_schema(value: Any) -> Any:
    """Project Atlas' full JSON Schema onto Anthropic's supported output subset.

    Atlas still validates the returned object against the original schema locally, so
    provider-specific schema limitations never weaken the runtime contract.
    """
    if isinstance(value, list):
        return [_anthropic_output_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _anthropic_output_schema(item)
        for key, item in value.items()
        if key not in _ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS
    }

def _normalize_usage(usage: dict[str, Any], *, family: str) -> dict[str, Any]:
    metrics = dict(usage)
    if family == "openai":
        if "input_tokens" in usage:
            metrics["input_tokens"] = int(usage.get("input_tokens") or 0)
        elif "prompt_tokens" in usage:
            metrics["input_tokens"] = int(usage.get("prompt_tokens") or 0)
        if "output_tokens" in usage:
            metrics["output_tokens"] = int(usage.get("output_tokens") or 0)
        elif "completion_tokens" in usage:
            metrics["output_tokens"] = int(usage.get("completion_tokens") or 0)
    elif family == "anthropic":
        metrics["input_tokens"] = int(usage.get("input_tokens") or 0)
        metrics["output_tokens"] = int(usage.get("output_tokens") or 0)
    elif family == "gemini":
        metrics["input_tokens"] = int(usage.get("promptTokenCount") or 0)
        metrics["output_tokens"] = int(
            usage.get("candidatesTokenCount")
            or usage.get("responseTokenCount")
            or 0
        )
    return metrics


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
    api_key: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com"
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = self.api_key or _require_env(self.api_key_env)
        _reject_multimodal(request, "OpenAI Responses")
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
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=_normalize_usage(dict(usage), family="openai"))


@dataclass
class OpenAICompatibleChatProvider:
    """OpenAI-compatible chat endpoint for LM Studio, xAI-style gateways, etc."""

    spec: ProviderSpec
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = self.api_key or _require_env(self.api_key_env)
        _reject_multimodal(request, "OpenAI-compatible chat")
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.input})
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        payload: dict[str, Any] = {"model": self.spec.model, "messages": messages}
        response_format = request.metadata.get("response_format")
        if isinstance(response_format, dict):
            payload["response_format"] = response_format
        raw = _post_json(
            self.base_url.rstrip("/") + "/v1/chat/completions",
            headers,
            payload,
            self.timeout_seconds,
        )
        choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                text = str(message.get("content") or "").strip()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=_normalize_usage(dict(usage), family="openai"))


@dataclass
class AnthropicMessagesProvider:
    spec: ProviderSpec
    api_key: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"
    max_tokens: int = 4096
    timeout_seconds: float = 120.0
    workspace_id: str | None = None

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = self.api_key or _require_env(self.api_key_env)
        payload: dict[str, Any] = {
            "model": self.spec.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": _anthropic_content(request)}],
        }
        if request.system:
            payload["system"] = request.system
        response_format = request.metadata.get("response_format")
        if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
            wrapper = response_format.get("json_schema")
            schema = wrapper.get("schema") if isinstance(wrapper, dict) else None
            if isinstance(schema, dict):
                # Anthropic structured outputs use output_config.format, while
                # Atlas keeps one provider-neutral response_format contract.
                payload["output_config"] = {"format": {"type": "json_schema", "schema": _anthropic_output_schema(schema)}}
        headers = {"x-api-key": str(key), "anthropic-version": self.api_version}
        workspace_id = (self.workspace_id or str(self.spec.metadata.get("workspace_id") or self.spec.metadata.get("anthropic_workspace_id") or "")).strip()
        if workspace_id:
            headers["anthropic-workspace-id"] = workspace_id
        raw = _post_json(
            self.base_url.rstrip("/") + "/v1/messages",
            headers,
            payload,
            self.timeout_seconds,
        )
        parts = raw.get("content") if isinstance(raw.get("content"), list) else []
        text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text").strip()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return ModelResponse(text=text, provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=_normalize_usage(dict(usage), family="anthropic"))


@dataclass
class GeminiGenerateContentProvider:
    spec: ProviderSpec
    api_key: str | None = None
    api_key_env: str = "GEMINI_API_KEY"
    base_url: str = "https://generativelanguage.googleapis.com"
    timeout_seconds: float = 120.0

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = self.api_key or _require_env(self.api_key_env)
        _reject_multimodal(request, "Gemini")
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
        return ModelResponse(text="\n".join(text_parts).strip(), provider_key=self.spec.key, model=self.spec.model, raw=raw, metrics=_normalize_usage(dict(usage), family="gemini"))
