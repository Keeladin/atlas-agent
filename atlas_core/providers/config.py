from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ProviderSpec
from .http import AnthropicMessagesProvider, GeminiGenerateContentProvider, OpenAICompatibleChatProvider, OpenAIResponsesProvider
from .registry import ProviderRegistry


def _spec(key: str, item: dict[str, Any]) -> ProviderSpec:
    return ProviderSpec(
        key=key,
        model=str(item["model"]),
        provider_kind=str(item["kind"]),
        capabilities={str(k): float(v) for k, v in dict(item.get("capabilities", {})).items()},
        local=bool(item.get("local", False)),
        enabled=bool(item.get("enabled", True)),
        max_context_chars=int(item.get("max_context_chars", 128000)),
        input_cost_per_million=(float(item["input_cost_per_million"]) if item.get("input_cost_per_million") is not None else None),
        output_cost_per_million=(float(item["output_cost_per_million"]) if item.get("output_cost_per_million") is not None else None),
        latency_rank=int(item.get("latency_rank", 50)),
        priority=int(item.get("priority", 50)),
        metadata=dict(item.get("metadata", {})),
    )


def load_provider_registry(path: str | Path) -> ProviderRegistry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    registry = ProviderRegistry()
    for key, item in dict(data.get("providers", {})).items():
        spec = _spec(key, item)
        kind = spec.provider_kind
        if kind == "openai_responses":
            provider = OpenAIResponsesProvider(spec, api_key_env=str(item.get("api_key_env", "OPENAI_API_KEY")), base_url=str(item.get("base_url", "https://api.openai.com")))
        elif kind == "openai_compatible_chat":
            provider = OpenAICompatibleChatProvider(spec, base_url=str(item["base_url"]), api_key_env=(str(item["api_key_env"]) if item.get("api_key_env") else None))
        elif kind == "anthropic_messages":
            provider = AnthropicMessagesProvider(spec, api_key_env=str(item.get("api_key_env", "ANTHROPIC_API_KEY")), base_url=str(item.get("base_url", "https://api.anthropic.com")), max_tokens=int(item.get("max_tokens", 4096)))
        elif kind == "gemini_generate_content":
            provider = GeminiGenerateContentProvider(spec, api_key_env=str(item.get("api_key_env", "GEMINI_API_KEY")), base_url=str(item.get("base_url", "https://generativelanguage.googleapis.com")))
        else:
            raise ValueError(f"Unsupported provider kind: {kind}")
        registry.register(provider)
    return registry
