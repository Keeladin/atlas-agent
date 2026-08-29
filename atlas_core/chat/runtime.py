from __future__ import annotations

import json
import re
from typing import Any

from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.knowledge import KnowledgeStore
from atlas_core.providers import ModelRequest
from atlas_core.provenance import InvocationProvenance
from .store import ChatStore

_WORD = re.compile(r"[A-Za-z0-9_.-]+")


class ChatRuntime:
    """One conversational Atlas. The model selects capabilities; runtime policy authorizes them."""

    def __init__(self, store: ChatStore, provider, registry: CapabilityRegistry,
                 capabilities: CapabilityRuntime, knowledge: KnowledgeStore) -> None:
        self.store = store
        self.provider = provider
        self.registry = registry
        self.capabilities = capabilities
        self.knowledge = knowledge

    def send(self, conversation_id: str, message: str, *, principal_id: str) -> dict[str, Any]:
        self.store.conversation(conversation_id)
        self.store.append(conversation_id, "user", message)
        relevant = list(self.knowledge.search(message, limit=6))
        tool_context: list[dict[str, Any]] = []
        shortlist = self.search_capabilities(message, limit=36)
        for _round in range(6):
            decision = self._decision(conversation_id, message, shortlist, relevant, tool_context)
            kind = str(decision.get("kind") or "reply")
            if kind == "search_capabilities":
                query = str(decision.get("query") or message)
                shortlist = self.search_capabilities(query, limit=60)
                tool_context.append({"capability_search": query, "matches": [item["id"] for item in shortlist]})
                continue
            if kind == "capability":
                cid = str(decision.get("capability_id") or "")
                payload = decision.get("input") if isinstance(decision.get("input"), dict) else {}
                try:
                    occurrence = self.capabilities.invoke(
                        cid, payload,
                        provenance=InvocationProvenance(principal_id, "human", "chat"),
                    )
                except Exception as exc:
                    tool_context.append({"capability_id": cid, "status": "error", "error": str(exc)})
                    continue
                public = occurrence.public()
                if occurrence.status == "pending_confirmation":
                    text = occurrence.summary or f"Confirmation is required before Atlas can run {cid}."
                    turn = self.store.append(conversation_id, "assistant", text, {"action": public, "requires_confirmation": True})
                    return {"turn": turn, "action": public}
                if occurrence.status in {"blocked", "failed", "expired", "cancelled"}:
                    text = self._ground_failure(occurrence)
                    turn = self.store.append(conversation_id, "assistant", text, {"action": public})
                    return {"turn": turn, "action": public}
                if occurrence.status == "uncertain":
                    text = "The action was dispatched, but Atlas has not verified the final outcome yet."
                    turn = self.store.append(conversation_id, "assistant", text, {"action": public})
                    return {"turn": turn, "action": public}
                tool_context.append({"capability_id": cid, "status": occurrence.status, "result": occurrence.result, "receipt": occurrence.receipt})
                continue
            reply = str(decision.get("reply") or "").strip()
            if not reply:
                reply = "I couldn't produce a usable response for that turn."
            turn = self.store.append(conversation_id, "assistant", reply, {
                "tools_used": [x.get("capability_id") for x in tool_context if x.get("capability_id")]
            })
            return {"turn": turn}
        turn = self.store.append(
            conversation_id, "assistant",
            "I reached the capability-turn limit before a verified answer was ready.",
            {"error": "chat_tool_round_limit"},
        )
        return {"turn": turn}

    def search_capabilities(self, query: str, *, limit: int = 40) -> list[dict[str, Any]]:
        tokens = {t.casefold() for t in _WORD.findall(query) if len(t) > 1}
        scored = []
        for reg in self.registry.all():
            definition = reg.definition
            hay = (definition.id + " " + definition.description + " " + " ".join(definition.tags)).casefold()
            score = sum(5 if token in definition.id.casefold() else 1 for token in tokens if token in hay)
            if definition.id in {"knowledge.search", "memory.remember", "work.create", "cadence.create"}:
                score += 1
            scored.append((score, definition.id, definition))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen = [definition for score, _, definition in scored if score > 0][:limit]
        if not chosen:
            chosen = [reg.definition for reg in self.registry.all()[:limit]]
        return [{
            "id": d.id, "description": d.description, "operation": d.operation,
            "effect_class": d.effect_class, "input_schema": d.input_schema, "source": d.source,
        } for d in chosen]

    def _decision(self, cid: str, message: str, inventory: list[dict[str, Any]],
                  knowledge: list[dict[str, Any]], tool_context: list[dict[str, Any]]) -> dict[str, Any]:
        turns = self.store.turns(cid, limit=16)
        history = [{"role": turn["role"], "content": turn["content"]} for turn in turns[:-1]]
        prompt = {
            "current_user_message": message,
            "recent_conversation": history,
            "relevant_durable_context": [
                {"title": item["title"], "content": item["content"][:1800]} for item in knowledge
            ],
            "available_capabilities": inventory,
            "tool_results": _bounded(tool_context, 14000),
        }
        system = (
            "You are Atlas, one persistent operational agent. Answer conversationally when no tool is needed. "
            "When an available capability is needed, select it by semantic meaning and supply only schema-valid input. "
            "Tools, models, MCP servers and providers are capabilities, not separate agents. "
            "Never decide whether an action is allowed and never add a confirmation yourself: Atlas runtime policy applies exact NO/YES/CONFIRM after resource resolution. "
            "If the needed capability is not in the supplied inventory, request a capability search. "
            "After successful tool results, answer the user using those results. Return JSON only in one of these forms: "
            "{\"kind\":\"reply\",\"reply\":\"...\"}, "
            "{\"kind\":\"capability\",\"capability_id\":\"...\",\"input\":{...}}, or "
            "{\"kind\":\"search_capabilities\",\"query\":\"...\"}."
        )
        response = self.provider.generate(ModelRequest(
            capability_id="chat.turn", system=system,
            input=json.dumps(prompt, ensure_ascii=False, default=str),
            metadata={"response_format": {"type": "json_object"}},
        ))
        return _json_object(response.text)

    @staticmethod
    def _ground_failure(occurrence) -> str:
        if occurrence.status == "blocked":
            if occurrence.policy_decision == "NO":
                return f"Atlas did not execute that action because the current runtime policy is NO for {occurrence.operation} on {occurrence.scope}."
            return occurrence.error or "Atlas did not execute that action."
        return occurrence.error or occurrence.error_code or "The action failed."


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"kind": "reply", "reply": text.strip()}


def _bounded(value: Any, max_chars: int) -> Any:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw) <= max_chars:
        return value
    return [{"notice": "tool context truncated", "tail": raw[-max_chars:]}]
