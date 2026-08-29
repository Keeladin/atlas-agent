from __future__ import annotations

import json
import re
from typing import Any

from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeStore
from atlas_core.memory import MemoryStore, memory_content_hash
from atlas_core.providers import ModelRequest
from atlas_core.provenance import InvocationProvenance
from .store import ChatStore

_WORD = re.compile(r"[A-Za-z0-9_.-]+")


class ChatRuntime:
    """One conversational Atlas. The model selects capabilities; runtime policy authorizes them."""

    def __init__(self, store: ChatStore, provider, registry: CapabilityRegistry,
                 capabilities: CapabilityRuntime, knowledge: KnowledgeStore,
                 memory: MemoryStore, identities: IdentityStore) -> None:
        self.store = store
        self.provider = provider
        self.registry = registry
        self.capabilities = capabilities
        self.knowledge = knowledge
        self.memory = memory
        self.identities = identities

    def send(self, conversation_id: str, message: str, *, principal_id: str) -> dict[str, Any]:
        self.store.conversation(conversation_id)
        owner_turn = self.store.append(conversation_id, "user", message)
        relevant = [
            {**item, "context_kind": "memory"}
            for item in self.memory.search(principal_id, message, limit=6)
        ] + [
            {**item, "context_kind": "knowledge"}
            for item in self.knowledge.search(message, limit=6)
        ]
        tool_context: list[dict[str, Any]] = []
        shortlist = self.search_capabilities(message, limit=36)
        memory_handled = False
        for _round in range(6):
            decision = self._decision(conversation_id, message, shortlist, relevant, tool_context, principal_id)
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
                memory_handled = memory_handled or cid.startswith("memory.")
                public = occurrence.public()
                if occurrence.status == "pending_confirmation":
                    text = occurrence.summary or f"Confirmation is required before Atlas can run {cid}."
                    return self._finish_turn(
                        conversation_id, message, principal_id, owner_turn, text,
                        {"action": public, "requires_confirmation": True}, action=public,
                        skip_capture=memory_handled,
                    )
                if occurrence.status in {"blocked", "failed", "expired", "cancelled"}:
                    return self._finish_turn(
                        conversation_id, message, principal_id, owner_turn, self._ground_failure(occurrence),
                        {"action": public}, action=public, skip_capture=memory_handled,
                    )
                if occurrence.status == "uncertain":
                    return self._finish_turn(
                        conversation_id, message, principal_id, owner_turn,
                        "The action was dispatched, but Atlas has not verified the final outcome yet.",
                        {"action": public}, action=public, skip_capture=memory_handled,
                    )
                tool_context.append({"capability_id": cid, "status": occurrence.status, "result": occurrence.result, "receipt": occurrence.receipt})
                continue
            reply = str(decision.get("reply") or "").strip()
            if not reply:
                reply = "I couldn't produce a usable response for that turn."
            return self._finish_turn(
                conversation_id, message, principal_id, owner_turn, reply,
                {"tools_used": [x.get("capability_id") for x in tool_context if x.get("capability_id")]},
                skip_capture=memory_handled,
            )
        return self._finish_turn(
            conversation_id, message, principal_id, owner_turn,
            "I reached the capability-turn limit before a verified answer was ready.",
            {"error": "chat_tool_round_limit"}, skip_capture=memory_handled,
        )

    def _finish_turn(self, conversation_id: str, message: str, principal_id: str,
                     owner_turn: dict[str, Any], text: str, metadata: dict[str, Any], *,
                     action: dict[str, Any] | None = None, skip_capture: bool = False) -> dict[str, Any]:
        turn = self.store.append(conversation_id, "assistant", text, metadata)
        if not skip_capture:
            try:
                self._auto_capture(conversation_id, message, principal_id, owner_turn)
            except Exception:
                # Auto-capture is post-reply bookkeeping. It must never rewrite or fail the owner turn.
                pass
        result: dict[str, Any] = {"turn": turn}
        if action is not None:
            result["action"] = action
        return result

    def _auto_capture(self, conversation_id: str, message: str, principal_id: str,
                      owner_turn: dict[str, Any]) -> None:
        candidates: dict[str, dict[str, Any]] = {}
        for item in (*self.memory.search(principal_id, message, limit=8), *self.memory.recent(principal_id, limit=24)):
            candidates[item["item_id"]] = item
        candidate_rows = [
            {"item_id": item["item_id"], "title": item["title"], "content": item["content"][:1200], "state": item["state"]}
            for item in candidates.values()
        ]
        response = self.provider.generate(ModelRequest(
            capability_id="chat.memory_reconciliation",
            system=(
                "You are proposing persistent-memory reconciliation from one authenticated owner turn. "
                "Do not execute anything. Return JSON only. Auto-capture may propose remember, update, retract, or noop; there is no capture capability. "
                "A remember/update must use grounding_excerpt copied exactly from the owner's message, never a paraphrase or inference. "
                "An update/retract item_id must be one of the supplied candidate ids. Prefer noop for trivia, transient chat, uncertainty, or anything not clearly durable. "
                "Return {\"proposals\":[{\"action\":\"remember|update|retract|noop\",\"item_id\":\"... optional\",\"title\":\"... optional\",\"grounding_excerpt\":\"exact owner substring\"}]} ."
            ),
            input=json.dumps({"owner_message": message, "candidate_memories": candidate_rows}, ensure_ascii=False),
            metadata={"response_format": {"type": "json_object"}},
        ))
        proposal = _json_object(response.text)
        rows = proposal.get("proposals") if isinstance(proposal.get("proposals"), list) else [proposal]
        active_hashes = {
            memory_content_hash(item["content"])
            for item in candidates.values() if item.get("state") == "active"
        }
        source_ref = f"chat:{conversation_id}:{owner_turn['turn_id']}"
        source_meta = {
            "source_conversation_id": conversation_id,
            "source_turn_id": owner_turn["turn_id"],
            "capture": "owner_turn",
        }
        for row in rows[:4]:
            if not isinstance(row, dict):
                continue
            action = str(row.get("action") or "noop")
            if action == "noop":
                continue
            excerpt = str(row.get("grounding_excerpt") or "")
            if not excerpt or excerpt not in message:
                continue
            if action == "remember":
                digest = memory_content_hash(excerpt)
                if digest in active_hashes:
                    continue
                occurrence = self.capabilities.invoke(
                    "memory.remember",
                    {"content": excerpt, "title": str(row.get("title") or "Memory"),
                     "grounding_excerpt": excerpt, "source_ref": source_ref, "metadata": source_meta},
                    provenance=InvocationProvenance(principal_id, "human", "chat"),
                )
                if occurrence.status == "succeeded":
                    active_hashes.add(digest)
                continue
            target = str(row.get("item_id") or "")
            item = candidates.get(target)
            if item is None:
                continue
            if action == "update":
                if memory_content_hash(item["content"]) == memory_content_hash(excerpt):
                    continue
                self.capabilities.invoke(
                    "memory.update",
                    {"item_id": target, "content": excerpt, "title": str(row.get("title") or item["title"]),
                     "grounding_excerpt": excerpt, "source_ref": source_ref, "metadata": source_meta},
                    provenance=InvocationProvenance(principal_id, "human", "chat"),
                )
            elif action == "retract" and item.get("state") == "active":
                self.capabilities.invoke(
                    "memory.retract",
                    {"item_id": target, "grounding_excerpt": excerpt, "source_ref": source_ref},
                    provenance=InvocationProvenance(principal_id, "human", "chat"),
                )

    def search_capabilities(self, query: str, *, limit: int = 40) -> list[dict[str, Any]]:
        tokens = {t.casefold() for t in _WORD.findall(query) if len(t) > 1}
        scored = []
        for reg in self.registry.all():
            definition = reg.definition
            hay = (definition.id + " " + definition.description + " " + " ".join(definition.tags)).casefold()
            score = sum(5 if token in definition.id.casefold() else 1 for token in tokens if token in hay)
            if definition.id in {"knowledge.search", "memory.search", "memory.remember", "memory.update", "memory.retract", "work.create", "cadence.create"}:
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
                  knowledge: list[dict[str, Any]], tool_context: list[dict[str, Any]],
                  principal_id: str) -> dict[str, Any]:
        turns = self.store.turns(cid, limit=16)
        owner = self.identities.principal(principal_id)
        history = [{"role": turn["role"], "content": turn["content"]} for turn in turns[:-1]]
        prompt = {
            "current_user_message": message,
            "owner_identity": {
                "kind": owner.principal_kind,
                "display_name": owner.display_name,
            },
            "recent_conversation": history,
            "relevant_durable_context": [
                {"kind": item.get("context_kind", "knowledge"), "title": item["title"], "content": item["content"][:1800]}
                for item in knowledge
            ],
            "available_capabilities": inventory,
            "tool_results": _bounded(tool_context, 14000),
        }
        system = (
            "You are Atlas, one persistent operational companion. Maintain continuity across turns and speak naturally, directly and proportionately to the user's request. "
            "Do not re-introduce yourself, advertise a menu of capabilities, or use generic onboarding/support language unless the user explicitly asks what Atlas is or can do. "
            "For greetings and casual conversation, respond briefly and naturally rather than describing your role. "
            "The supplied owner_identity is authenticated durable runtime truth about the person Atlas is serving; use it directly when the user asks who they are or when their name is relevant. "
            "Use supplied conversation history and durable context when relevant, but never claim memory, state, evidence or outcomes that are not actually present in the supplied runtime context. "
            "Do not equate the current conversation window with Atlas's total persistent state. Durable Memory and durable Knowledge are separate runtime responsibilities and may both appear in relevant_durable_context. "
            "If the user asks whether Atlas remembers a specific fact and it is not present in owner_identity, recent conversation or relevant durable context, use memory.search when available before saying it is unknown; use knowledge.search for references and notes. "
            "Use the real memory.remember, memory.update and memory.retract capabilities when the user explicitly requests a memory change. Post-reply auto-capture uses those same governed operations and never bypasses runtime policy. "
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
