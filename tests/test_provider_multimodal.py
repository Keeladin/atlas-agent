from __future__ import annotations

import base64

import pytest

from atlas_core.providers import ModelContentPart, ModelRequest, ProviderSpec
from atlas_core.providers.http import AnthropicMessagesProvider, OpenAICompatibleChatProvider, ProviderHTTPError


def test_anthropic_serializes_provider_neutral_image_and_document(monkeypatch):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update(payload)
        return {"content": [{"type": "text", "text": "seen"}], "usage": {}}

    monkeypatch.setattr("atlas_core.providers.http._post_json", fake_post)
    provider = AnthropicMessagesProvider(ProviderSpec("anthropic:test", "claude-sonnet-5", "anthropic", {}), api_key="secret")
    request = ModelRequest(
        capability_id="test.multimodal", system="Inspect the supplied evidence.", input="Describe it.",
        content=(
            ModelContentPart.binary("image", b"png-bytes", "image/png", source_ref="artifact:a#page=2"),
            ModelContentPart.binary("document", b"%PDF-test", "application/pdf", source_ref="artifact:b"),
        ),
    )
    response = provider.generate(request)
    assert response.text == "seen"
    blocks = captured["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "Describe it."}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert base64.b64decode(blocks[1]["source"]["data"]) == b"png-bytes"
    assert blocks[2]["type"] == "document"
    assert blocks[2]["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(blocks[2]["source"]["data"]) == b"%PDF-test"


def test_text_only_adapter_fails_closed_on_multimodal_request():
    provider = OpenAICompatibleChatProvider(
        ProviderSpec("local:test", "atlas", "openai_compatible", {}),
        base_url="http://127.0.0.1:1234",
    )
    request = ModelRequest(
        capability_id="test.multimodal", system="", input="inspect",
        content=(ModelContentPart.binary("image", b"pixels", "image/png"),),
    )
    with pytest.raises(ProviderHTTPError, match="does not yet implement multimodal"):
        provider.generate(request)


def test_anthropic_sends_configured_workspace_header(monkeypatch):
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["headers"] = dict(headers)
        return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

    monkeypatch.setattr("atlas_core.providers.http._post_json", fake_post)
    spec = ProviderSpec(
        "anthropic:test", "claude-sonnet-5", "anthropic", {},
        metadata={"workspace_id": "wrk_test123"},
    )
    provider = AnthropicMessagesProvider(spec, api_key="secret")
    provider.generate(ModelRequest(capability_id="test", system="", input="hello"))
    assert captured["headers"]["anthropic-workspace-id"] == "wrk_test123"


def test_anthropic_maps_provider_neutral_json_schema_to_output_config(monkeypatch):
    captured = {}
    def fake_post(url, headers, payload, timeout):
        captured.update(payload)
        return {"content": [{"type": "text", "text": '{"ok":true}'}], "usage": {}}
    monkeypatch.setattr("atlas_core.providers.http._post_json", fake_post)
    provider = AnthropicMessagesProvider(ProviderSpec("anthropic:test", "claude-sonnet-5", "anthropic", {}), api_key="secret")
    schema = {
        "type": "object", "required": ["ok", "confidence", "route", "questions"],
        "properties": {
            "ok": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "route": {"type": ["string", "null"], "minLength": 1},
            "questions": {"type": "array", "maxItems": 12, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        },
        "additionalProperties": False,
    }
    request = ModelRequest(capability_id="test.structured", system="", input="Return JSON.", metadata={"response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}})
    response = provider.generate(request)
    assert response.text == '{"ok":true}'
    sent_schema = captured["output_config"]["format"]["schema"]
    assert sent_schema["properties"]["ok"] == {"type": "boolean"}
    assert sent_schema["properties"]["confidence"] == {"type": "number"}
    assert sent_schema["properties"]["route"] == {"type": ["string", "null"]}
    assert sent_schema["properties"]["questions"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["confidence"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert schema["properties"]["questions"]["maxItems"] == 12
