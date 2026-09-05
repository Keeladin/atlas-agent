from __future__ import annotations

import json
from typing import Any, Iterable

from atlas_core.providers import ModelRequest

from .models import IntakeResult
from .store import ObligationStore


class IntakeExtractionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ObligationIntakeRuntime:
    """Separate semantic pass that enumerates owner commitments before planning."""

    def __init__(self, store: ObligationStore, provider, *, max_attempts: int = 2) -> None:
        self.store = store
        self.provider = provider
        self.max_attempts = max(1, int(max_attempts))

    def capture(
        self, owner_turn: dict[str, Any], *,
        recent_context: Iterable[dict[str, Any]] = (),
    ) -> IntakeResult:
        turn_id = str(owner_turn["turn_id"])
        last_code = "intake_extraction_failed"
        last_provider = last_model = None
        for _ in range(self.max_attempts):
            attempts = self.store.begin_attempt(turn_id)
            try:
                response = self.provider.generate(self._request(owner_turn, recent_context))
                last_provider = str(getattr(response, "provider_key", "") or "") or None
                last_model = str(getattr(response, "model", "") or "") or None
                parsed = self._parse(response.text)
                return self.store.commit_intake(
                    turn_id,
                    parsed["obligations"],
                    attempts=attempts,
                    provider=last_provider,
                    model=last_model,
                    unmapped_spans=parsed["unmapped_spans"],
                )
            except IntakeExtractionError as exc:
                last_code = exc.code
            except (ValueError, json.JSONDecodeError) as exc:
                last_code = "intake_invalid_grounding" if "grounding" in str(exc) else "intake_invalid_response"
            except Exception:
                last_code = "intake_provider_failed"
        return self.store.fail_intake(
            turn_id,
            attempts=attempts,
            provider=last_provider,
            model=last_model,
            error_code=last_code,
        )

    @staticmethod
    def _request(owner_turn: dict[str, Any], recent_context: Iterable[dict[str, Any]]) -> ModelRequest:
        bounded = []
        for row in list(recent_context)[-6:]:
            if str(row.get("turn_id") or "") == str(owner_turn.get("turn_id") or ""):
                continue
            role = str(row.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            bounded.append({"role": role, "content": str(row.get("content") or "")[:1600]})
        schema = {
            "type": "object",
            "required": ["obligations", "unmapped_spans"],
            "properties": {
                "obligations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["grounding_excerpt", "text", "kind"],
                        "properties": {
                            "grounding_excerpt": {"type": "string", "minLength": 1},
                            "text": {"type": "string", "minLength": 1},
                            "kind": {"type": "string", "enum": ["state_change", "communication"]},
                            "temporal_grounding_excerpt": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "unmapped_spans": {
                    "type": "array", "items": {"type": "string", "minLength": 1}
                },
            },
            "additionalProperties": False,
        }
        return ModelRequest(
            capability_id="chat.obligation_intake",
            system=(
                "Enumerate only commitments explicitly grounded in the authenticated owner's current message. "
                "A commitment states what Atlas owes, never how to do it: do not emit capabilities, tools, steps, "
                "dependencies, or execution ordering. Use kind state_change when fulfilment is an externally verifiable "
                "state/effect, and communication when Atlas owes an owner-facing answer/report. "
                "grounding_excerpt must be copied verbatim from owner_message. If the owner states a deadline or temporal bound, "
                "copy that exact source phrase into temporal_grounding_excerpt; do not calculate timestamps or timezones. "
                "Cover every material requested outcome, constraint, condition, prohibition, or requested communication; put any span you cannot safely map in "
                "unmapped_spans. A greeting or non-request may validly return zero obligations and zero unmapped_spans."
            ),
            input=json.dumps(
                {
                    "owner_message": str(owner_turn.get("content") or ""),
                    "recent_conversation": bounded,
                },
                ensure_ascii=False,
            ),
            max_output_chars=5000,
            metadata={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "obligation_intake", "strict": True, "schema": schema},
                }
            },
        )

    @staticmethod
    def _parse(text: str) -> dict[str, list[Any]]:
        try:
            parsed = json.loads(str(text or "").strip())
        except json.JSONDecodeError as exc:
            raise IntakeExtractionError("intake_unparseable_response") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"obligations", "unmapped_spans"}:
            raise IntakeExtractionError("intake_invalid_shape")
        obligations = parsed.get("obligations")
        unmapped = parsed.get("unmapped_spans")
        if not isinstance(obligations, list) or not isinstance(unmapped, list):
            raise IntakeExtractionError("intake_invalid_shape")
        if not all(isinstance(item, str) and item for item in unmapped):
            raise IntakeExtractionError("intake_invalid_shape")
        allowed = {
            "grounding_excerpt", "text", "kind", "temporal_grounding_excerpt",
        }
        cleaned = []
        for item in obligations:
            if not isinstance(item, dict) or not {"grounding_excerpt", "text", "kind"}.issubset(item):
                raise IntakeExtractionError("intake_invalid_shape")
            if set(item) - allowed:
                raise IntakeExtractionError("intake_invalid_shape")
            excerpt = item.get("grounding_excerpt")
            body = item.get("text")
            kind = item.get("kind")
            if not isinstance(excerpt, str) or not excerpt:
                raise IntakeExtractionError("intake_invalid_shape")
            if not isinstance(body, str) or not body.strip():
                raise IntakeExtractionError("intake_invalid_shape")
            if kind not in {"state_change", "communication"}:
                raise IntakeExtractionError("intake_invalid_shape")
            cleaned.append(dict(item))
        return {"obligations": cleaned, "unmapped_spans": list(unmapped)}
