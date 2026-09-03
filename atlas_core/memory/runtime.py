from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from atlas_core.actions import ActionResult, ActionStore
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

from .store import MemoryStore, memory_content_hash

_CONTENT_KEYS = {"content", "title", "grounding_excerpt", "text"}
_RESERVED_METADATA_KEYS = {"content_source", "source_conversation_id", "source_turn_id", "capture"}


class MemoryRuntime:
    def __init__(self, store: MemoryStore, registry: CapabilityRegistry, actions_store: ActionStore,
                 grounding_validator: Callable[[str, str], bool] | None = None) -> None:
        if store.path.resolve() != actions_store.path.resolve():
            raise ValueError("MemoryStore and ActionStore must use the same SQLite database for atomic purge")
        self.store = store
        self.registry = registry
        self.actions_store = actions_store
        self.grounding_validator = grounding_validator
        self._register()

    def _register(self) -> None:
        text = {"type": "string", "minLength": 1}
        item_id = {"type": "string", "minLength": 1}
        search_schema = {"type": "object", "required": ["query"], "properties": {"query": text, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}
        remember_schema = {"type": "object", "required": ["content"], "properties": {"content": text, "title": {"type": "string"}, "grounding_excerpt": {"type": "string"}, "source_ref": {"type": "string"}, "metadata": {"type": "object"}}, "additionalProperties": False}
        update_schema = {"type": "object", "required": ["item_id", "content"], "properties": {"item_id": item_id, "content": text, "title": {"type": "string"}, "grounding_excerpt": {"type": "string"}, "source_ref": {"type": "string"}, "metadata": {"type": "object"}}, "additionalProperties": False}
        exact_schema = {"type": "object", "required": ["item_id"], "properties": {"item_id": item_id}, "additionalProperties": False}
        retract_schema = {"type": "object", "required": ["item_id"], "properties": {"item_id": item_id, "grounding_excerpt": {"type": "string"}, "source_ref": {"type": "string"}}, "additionalProperties": False}

        def reg(cid: str, description: str, operation: str, effect: str, schema: dict[str, Any], resolver, executor) -> None:
            self.registry.register(CapabilityRegistration(
                CapabilityDefinition(cid, description, operation, effect, schema, source="memory", tags=("memory", "durable-context")),
                resolver, executor,
                metadata={"scope_hint": "atlas/memory", "requires_owner_context": True},
            ), replace=True)

        reg("memory.search", "Search durable owner memory.", "search", "none", search_schema,
            lambda p: ScopeResolution("atlas/memory", dict(p), "Search durable owner memory"), self._search)
        reg("memory.remember", "Persist a durable owner memory grounded in owner context.", "remember", "internal", remember_schema,
            lambda p: ScopeResolution("atlas/memory", dict(p), f"Remember a durable owner memory ({len(str(p.get('content') or ''))} chars)"), self._remember)
        reg("memory.update", "Create a superseding version of a durable owner memory.", "update", "internal", update_schema,
            lambda p: ScopeResolution(f"atlas/memory/{p['item_id']}", dict(p), f"Update memory {_short(p['item_id'])} ({len(str(p.get('content') or ''))} chars)"), self._update)
        reg("memory.retract", "Retract an active durable owner memory without deleting its history.", "retract", "reversible", retract_schema,
            lambda p: ScopeResolution(f"atlas/memory/{p['item_id']}", dict(p), f"Retract memory {_short(p['item_id'])}"), self._retract)
        reg("memory.restore", "Restore a retracted durable owner memory.", "restore", "reversible", exact_schema,
            lambda p: ScopeResolution(f"atlas/memory/{p['item_id']}", dict(p), f"Restore memory {_short(p['item_id'])}"), self._restore)
        reg("memory.purge", "Purge a memory supersession chain and redact retained application-level action content atomically.", "purge", "destructive", exact_schema,
            lambda p: ScopeResolution(f"atlas/memory/{p['item_id']}", dict(p), f"Purge memory {_short(p['item_id'])}"), self._purge)

    @staticmethod
    def _context(payload: dict[str, Any]) -> tuple[str, str]:
        owner = str(payload.pop("__owner_principal_id", "") or "")
        surface = str(payload.pop("__invocation_surface", "") or "")
        if not owner:
            raise ValueError("owner principal unavailable")
        return owner, surface

    def _mutation_context_error(self, payload: dict[str, Any], surface: str, operation: str, *,
                                content_must_match_excerpt: bool) -> ActionResult | None:
        if surface == "control":
            return None
        if surface != "chat":
            return ActionResult(
                False, {}, {"ok": False, "operation": operation},
                error_code="memory_surface_invalid",
                error="owner memory mutations require authenticated chat grounding or explicit owner control",
            )
        excerpt = str(payload.get("grounding_excerpt") or "")
        source_ref = str(payload.get("source_ref") or "")
        valid = bool(
            excerpt and source_ref
            and self.grounding_validator is not None
            and self.grounding_validator(source_ref, excerpt)
        )
        if content_must_match_excerpt:
            content = str(payload.get("content") or "")
            valid = valid and bool(content) and memory_content_hash(content) == memory_content_hash(excerpt)
        if valid:
            return None
        return ActionResult(
            False, {}, {"ok": False, "operation": operation},
            error_code="memory_grounding_invalid",
            error="chat memory mutation must be grounded in the authenticated owner turn",
        )

    @staticmethod
    def _memory_metadata(payload: dict[str, Any], surface: str) -> dict[str, Any]:
        incoming = dict(payload.get("metadata") or {})
        metadata = {key: value for key, value in incoming.items() if key not in _RESERVED_METADATA_KEYS}
        if surface == "chat":
            parts = str(payload.get("source_ref") or "").split(":", 2)
            metadata["content_source"] = "owner_turn"
            if len(parts) == 3 and parts[0] == "chat":
                metadata["source_conversation_id"] = parts[1]
                metadata["source_turn_id"] = parts[2]
        else:
            metadata["content_source"] = "control_owner_input"
        return metadata

    def _search(self, payload: dict[str, Any]) -> ActionResult:
        owner, _ = self._context(payload)
        rows = self.store.search(owner, payload["query"], limit=int(payload.get("limit") or 10))
        return ActionResult(True, list(rows), {"ok": True, "operation": "search", "count": len(rows)})

    def _remember(self, payload: dict[str, Any]) -> ActionResult:
        owner, surface = self._context(payload)
        grounding_error = self._mutation_context_error(payload, surface, "remember", content_must_match_excerpt=True)
        if grounding_error is not None:
            return grounding_error
        metadata = self._memory_metadata(payload, surface)
        grounding_excerpt = payload.get("grounding_excerpt") if surface == "chat" else None
        source_ref = payload.get("source_ref") if surface == "chat" else None
        try:
            item = self.store.add(
                principal_id=owner, title=str(payload.get("title") or "Memory"), content=payload["content"],
                grounding_excerpt=grounding_excerpt, source_ref=source_ref, metadata=metadata,
            )
        except sqlite3.IntegrityError as exc:
            if "duplicate active memory" in str(exc):
                return ActionResult(False, {}, {"ok": False, "operation": "remember"},
                                    error_code="memory_duplicate", error="an equivalent active memory already exists")
            raise
        return ActionResult(True, item, {"ok": True, "operation": "remember", "item_id": item["item_id"]})

    def _update(self, payload: dict[str, Any]) -> ActionResult:
        owner, surface = self._context(payload)
        grounding_error = self._mutation_context_error(payload, surface, "update", content_must_match_excerpt=True)
        if grounding_error is not None:
            return grounding_error
        metadata = self._memory_metadata(payload, surface)
        grounding_excerpt = payload.get("grounding_excerpt") if surface == "chat" else None
        source_ref = payload.get("source_ref") if surface == "chat" else None
        try:
            item = self.store.update(
                principal_id=owner, item_id=payload["item_id"], content=payload["content"], title=payload.get("title"),
                grounding_excerpt=grounding_excerpt, source_ref=source_ref, metadata=metadata,
            )
        except sqlite3.IntegrityError as exc:
            if "duplicate active memory" in str(exc):
                return ActionResult(False, {}, {"ok": False, "operation": "update"},
                                    error_code="memory_duplicate", error="an equivalent active memory already exists")
            raise
        return ActionResult(True, item, {"ok": True, "operation": "update", "item_id": item["item_id"], "supersedes": payload["item_id"]})

    def _retract(self, payload: dict[str, Any]) -> ActionResult:
        owner, surface = self._context(payload)
        grounding_error = self._mutation_context_error(payload, surface, "retract", content_must_match_excerpt=False)
        if grounding_error is not None:
            return grounding_error
        item = self.store.retract(owner, payload["item_id"])
        return ActionResult(True, item, {"ok": True, "operation": "retract", "item_id": item["item_id"]})

    def _restore(self, payload: dict[str, Any]) -> ActionResult:
        owner, _ = self._context(payload); item = self.store.restore(owner, payload["item_id"])
        return ActionResult(True, item, {"ok": True, "operation": "restore", "item_id": item["item_id"]})

    def _purge(self, payload: dict[str, Any]) -> ActionResult:
        owner, _ = self._context(payload); item_id = payload["item_id"]
        try:
            with self.store._db() as db:
                chain = self.store.chain(owner, item_id, db=db)
                item_ids = {item["item_id"] for item in chain}
                hashes = {memory_content_hash(value) for item in chain for value in (item.get("content"), item.get("grounding_excerpt")) if isinstance(value, str) and value}
                self.store.purge_chain(owner, item_id, db=db)
                redacted = _redact_occurrences(
                    db, self.actions_store, principal_id=owner, item_ids=item_ids, content_hashes=hashes,
                )
                evidence_redacted = _redact_evidence(db, redacted, item_ids=item_ids, content_hashes=hashes)
                for row in redacted:
                    db.execute(
                        "INSERT INTO evidence(evidence_id,occurrence_id,kind,payload_json) VALUES (?,?,?,?)",
                        (f"evidence_{uuid4().hex}", row["occurrence_id"], "memory_purge_redaction",
                         json.dumps({"occurrence_id": row["occurrence_id"], "matched_by": row["matched_by"], "fields": row["fields"]}, sort_keys=True, separators=(",", ":"))),
                    )
            output = {"item_id": item_id, "purged_items": len(item_ids), "redacted_occurrences": len(redacted), "redacted_evidence": evidence_redacted}
            return ActionResult(True, output, {"ok": True, "operation": "purge", **output})
        except Exception:
            return ActionResult(False, {"item_id": item_id}, {"ok": False, "operation": "purge", "item_id": item_id}, error_code="memory_purge_failed", error="memory purge failed atomically")


def _short(item_id: str) -> str:
    return item_id if len(item_id) <= 18 else item_id[:15] + "…"



def _json_item_matches(value: Any, item_ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_json_item_matches(child, item_ids) for child in value.values())
    if isinstance(value, list):
        return any(_json_item_matches(child, item_ids) for child in value)
    return isinstance(value, str) and any(item_id in value for item_id in item_ids)


def _json_hash_matches(value: Any, content_hashes: set[str], key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(_json_hash_matches(child, content_hashes, str(child_key)) for child_key, child in value.items())
    if isinstance(value, list):
        return any(_json_hash_matches(child, content_hashes, key) for child in value)
    return isinstance(value, str) and key in _CONTENT_KEYS and memory_content_hash(value) in content_hashes


def _redact_occurrences(db: sqlite3.Connection, actions_store: ActionStore, *, principal_id: str,
                        item_ids: set[str], content_hashes: set[str]) -> list[dict[str, Any]]:
    redacted_rows: list[dict[str, Any]] = []
    for row in actions_store.memory_occurrence_rows(db, principal_id=principal_id):
        decoded: dict[str, Any] = {}
        for column in ("payload_json", "result_json", "receipt_json"):
            raw = row[column]
            try:
                decoded[column] = None if raw is None else json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                decoded[column] = raw
        item_match = any(_json_item_matches(value, item_ids) for value in decoded.values())
        hash_match = any(_json_hash_matches(value, content_hashes) for value in decoded.values())
        summary = row.get("summary")
        if isinstance(summary, str):
            item_match = item_match or any(item_id in summary for item_id in item_ids)
            hash_match = hash_match or memory_content_hash(summary) in content_hashes
        if not (item_match or hash_match):
            continue
        if row["status"] == "executing":
            # The current purge occurrence necessarily matches its own item id,
            # but its exact schema cannot retain memory content. Any other
            # matching in-flight action may later become terminal with content
            # that this transaction could not safely redact.
            if row["capability_id"] == "memory.purge":
                continue
            raise RuntimeError("matching memory occurrence is still executing")
        fields: list[str] = []
        encoded: dict[str, str | None] = {}
        for column, value in decoded.items():
            if value is None:
                encoded[column] = None
                continue
            redacted, nested = _redact_json(value, item_ids=item_ids, content_hashes=content_hashes,
                                             path=column, mark_root=(column == "payload_json" and isinstance(value, dict)))
            fields.extend(nested)
            encoded[column] = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fields.append("summary")
        actions_store.redact_memory_occurrence(
            db, occurrence_id=row["occurrence_id"], payload_json=encoded["payload_json"] or "{}",
            result_json=encoded["result_json"], receipt_json=encoded["receipt_json"] or "{}",
        )
        matched_by = []
        if item_match:
            matched_by.append("item_id")
        if hash_match:
            matched_by.append("content_hash")
        redacted_rows.append({"occurrence_id": row["occurrence_id"], "matched_by": matched_by, "fields": sorted(set(fields))})
    return redacted_rows

def _redact_evidence(db: sqlite3.Connection, occurrences: list[dict[str, Any]], *, item_ids: set[str], content_hashes: set[str]) -> int:
    count = 0
    for occurrence in occurrences:
        for row in db.execute("SELECT evidence_id,payload_json FROM evidence WHERE occurrence_id=?", (occurrence["occurrence_id"],)).fetchall():
            try:
                value = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not _json_matches(value, item_ids, content_hashes):
                continue
            redacted, _ = _redact_json(value, item_ids=item_ids, content_hashes=content_hashes, mark_root=isinstance(value, dict))
            db.execute("UPDATE evidence SET payload_json=? WHERE evidence_id=?", (json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=False), row["evidence_id"]))
            count += 1
    return count


def _json_matches(value: Any, item_ids: set[str], content_hashes: set[str], key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(_json_matches(v, item_ids, content_hashes, str(k)) for k, v in value.items())
    if isinstance(value, list):
        return any(_json_matches(v, item_ids, content_hashes, key) for v in value)
    if isinstance(value, str):
        if any(item_id in value for item_id in item_ids):
            return True
        if key in _CONTENT_KEYS and memory_content_hash(value) in content_hashes:
            return True
    return False


def _redact_json(value: Any, *, item_ids: set[str], content_hashes: set[str], path: str = "", mark_root: bool = False) -> tuple[Any, list[str]]:
    fields: list[str] = []
    if isinstance(value, dict):
        target_dict = any(isinstance(child, str) and any(item_id in child for item_id in item_ids) for child in value.values())
        out: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) in _CONTENT_KEYS and isinstance(child, str) and (target_dict or memory_content_hash(child) in content_hashes):
                out[key] = "[purged]"
                fields.append(child_path)
            else:
                out[key], nested = _redact_json(child, item_ids=item_ids, content_hashes=content_hashes, path=child_path)
                fields.extend(nested)
        if fields or mark_root:
            out["__redacted"] = True
        return out, fields
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            redacted, nested = _redact_json(item, item_ids=item_ids, content_hashes=content_hashes, path=f"{path}[{index}]")
            rows.append(redacted)
            fields.extend(nested)
        return rows, fields
    return value, fields
