from __future__ import annotations

from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, ScopeResolution

from .indexing import IndexingRuntime, KnowledgeGenerationBusy, index_result
from .store import KnowledgeStore

CURATED_MECHANISM = "fts.bm25@curated"


def _short(item_id: str) -> str:
    return item_id if len(item_id) <= 18 else item_id[:15] + "…"


class KnowledgeRuntime:
    """Curated durable references and notes, plus the generic retrieval contract.

    Retrieval is mechanism-blind by design: callers receive content, a raw
    mechanism score and a grounding record. Which mechanism answered is a label,
    never a handle into a physical index.
    """

    def __init__(self, store: KnowledgeStore, registry: CapabilityRegistry,
                 indexing: IndexingRuntime | None = None) -> None:
        self.store = store
        self.registry = registry
        self.indexing = indexing
        self._register()
        if indexing is not None:
            self._register_indexing()

    # ---- shared runtime code path (capability executor and Chat both call this)

    def retrieve(self, need: str, *, limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        selected = filters or {}
        tiers = selected.get("tiers") or ["curated", "derived"]
        ranked: list[list[dict[str, Any]]] = []
        if "curated" in tiers:
            ranked.append(self._curated(need, limit))
        if "derived" in tiers and self.indexing is not None:
            ranked.append(self.indexing.retrieve(
                need, limit=limit, state=str(selected.get("state") or "current"),
                artifact_id=selected.get("artifact_id"), generation=selected.get("generation"),
            ))
        return _rank_merge(ranked, limit)

    def _curated(self, need: str, limit: int) -> list[dict[str, Any]]:
        return [{
            "content": item["content"],
            "score": item["score"],
            "mechanism": CURATED_MECHANISM,
            "grounding": {"tier": "curated", "item_id": item["item_id"], "title": item.get("title"), "source_ref": item.get("source_ref")},
        } for item in self.store.search(need, limit=limit)]

    def promote(self, *, content: str, title: str, source_ref: str, kind: str = "reference",
                metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        reference = str(source_ref or "").strip()
        if not reference:
            raise ValueError("curated knowledge requires a source_ref")
        return self.store.add(kind=kind, title=title, content=content, source_ref=reference, metadata=metadata)

    # ---- capability registrations

    def _register(self) -> None:
        text = {"type": "string", "minLength": 1}
        search_schema = {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}
        retrieve_schema = {"type": "object", "required": ["need"], "properties": {
            "need": text,
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "filters": {"type": "object", "properties": {
                "tiers": {"type": "array", "items": {"type": "string", "enum": ["curated", "derived"]}},
                "artifact_id": {"type": "string"},
                "state": {"type": "string", "enum": ["current", "all"]},
                "generation": {"type": "string"},
            }, "additionalProperties": False},
        }, "additionalProperties": False}
        promote_schema = {"type": "object", "required": ["content", "title", "source_ref"], "properties": {
            "content": text, "title": text, "source_ref": text,
            "kind": {"type": "string", "enum": ["reference", "note"]},
            "metadata": {"type": "object"},
        }, "additionalProperties": False}
        delete_schema = {"type": "object", "required": ["item_id"], "properties": {"item_id": text}, "additionalProperties": False}

        def reg(cid: str, description: str, operation: str, effect: str, schema: dict[str, Any], resolver, executor) -> None:
            self.registry.register(CapabilityRegistration(
                CapabilityDefinition(cid, description, operation, effect, schema, source="knowledge", tags=("knowledge", "durable-context")),
                resolver, executor, metadata={"scope_hint": "atlas/knowledge"},
            ), replace=True)

        reg("knowledge.retrieve", "Retrieve grounded durable knowledge relevant to a semantic need.", "retrieve", "none", retrieve_schema,
            lambda p: ScopeResolution("atlas/knowledge", dict(p), "Retrieve grounded durable knowledge"), self._retrieve_execute)
        reg("knowledge.search", "Search durable Atlas references and notes.", "search", "none", search_schema,
            lambda p: ScopeResolution("atlas/knowledge", dict(p), "Search durable knowledge"), self._search_execute)
        reg("knowledge.promote", "Promote grounded content into durable curated knowledge.", "promote", "internal", promote_schema,
            lambda p: ScopeResolution("atlas/knowledge", dict(p), f"Promote durable knowledge ({len(str(p.get('content') or ''))} chars)"), self._promote_execute)
        reg("knowledge.delete", "Delete one curated durable knowledge item.", "delete", "reversible", delete_schema,
            lambda p: ScopeResolution(f"atlas/knowledge/{p['item_id']}", dict(p), f"Delete knowledge item {_short(p['item_id'])}"), self._delete_execute)

    def _register_indexing(self) -> None:
        index_schema = {"type": "object", "required": ["source_artifact_id", "extraction_artifact_id"], "properties": {
            "source_artifact_id": {"type": "string", "minLength": 1},
            "extraction_artifact_id": {"type": "string", "minLength": 1},
            "generation_id": {"type": "string", "minLength": 1},
        }, "additionalProperties": False}
        activate_schema = {"type": "object", "required": ["generation_id"], "properties": {
            "generation_id": {"type": "string", "minLength": 1},
        }, "additionalProperties": False}
        verify_schema = {"type": "object", "required": ["generation_id"], "properties": {
            "generation_id": {"type": "string", "minLength": 1},
            "required_extraction_artifact_ids": {"type": "array", "uniqueItems": True,
                                                 "items": {"type": "string", "minLength": 1}},
        }, "additionalProperties": False}

        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("knowledge.index", "Segment an extracted artifact into the derived knowledge tier.", "index", "internal", index_schema, source="knowledge", tags=("knowledge", "index")),
            lambda p: ScopeResolution("atlas/knowledge/index", dict(p), f"Index artifact {_short(p['source_artifact_id'])}"),
            lambda p: index_result(p, self.indexing),
            metadata={"scope_hint": "atlas/knowledge/index", "requires_invocation_context": True},
        ), replace=True)
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("knowledge.verify_generation", "Deterministically verify a built knowledge generation before activation.", "verify", "internal", verify_schema, source="knowledge", tags=("knowledge", "index", "verification")),
            lambda p: ScopeResolution("atlas/knowledge/index", dict(p), f"Verify knowledge generation {_short(p['generation_id'])}"),
            self._verify_execute, metadata={"scope_hint": "atlas/knowledge/index", "requires_invocation_context": True},
        ), replace=True)
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("knowledge.activate_generation", "Activate a verified knowledge generation as the default retrieval corpus.", "activate", "internal", activate_schema, source="knowledge", tags=("knowledge", "index")),
            lambda p: ScopeResolution("atlas/knowledge/index", dict(p), f"Activate knowledge generation {_short(p['generation_id'])}"),
            self._activate_execute,
            metadata={"scope_hint": "atlas/knowledge/index", "requires_invocation_context": True},
        ), replace=True)

    def _verify_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner_work_id = payload.pop("__work_id", None)
        payload.pop("__step_id", None)
        try:
            receipt = self.indexing.verify(
                payload["generation_id"],
                required_extraction_artifact_ids=payload.get("required_extraction_artifact_ids"),
                owner_work_id=owner_work_id,
            )
        except KnowledgeGenerationBusy as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "verify", "retryable": True},
                                error_code="knowledge_generation_busy", error=str(exc))
        except (KeyError, ValueError) as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "verify"}, error_code="generation_not_verifiable", error=str(exc))
        return ActionResult(True, {"generation_id": payload["generation_id"], "verification": receipt},
                            {"ok": bool(receipt.get("ok")), "operation": "verify", "generation_id": payload["generation_id"]})

    def _activate_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner_work_id = payload.pop("__work_id", None)
        payload.pop("__step_id", None)
        try:
            generation = self.indexing.activate(payload["generation_id"], owner_work_id=owner_work_id)
        except KnowledgeGenerationBusy as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "activate", "retryable": True},
                                error_code="knowledge_generation_busy", error=str(exc))
        except KeyError:
            return ActionResult(False, {}, {"ok": False, "operation": "activate"},
                                error_code="generation_unknown", error="unknown generation")
        except ValueError as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "activate"},
                                error_code="generation_not_verified", error=str(exc))
        return ActionResult(True, generation, {"ok": True, "operation": "activate", "generation_id": generation["generation_id"]})

    def _retrieve_execute(self, payload: dict[str, Any]) -> ActionResult:
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        rows = self.retrieve(payload["need"], limit=int(payload.get("limit") or 10), filters=filters)
        return ActionResult(True, rows, {"ok": True, "operation": "retrieve", "count": len(rows)})

    def _search_execute(self, payload: dict[str, Any]) -> ActionResult:
        rows = list(self.store.search(payload["query"], limit=int(payload.get("limit") or 10)))
        return ActionResult(True, rows, {"ok": True, "operation": "search", "count": len(rows)})

    def _promote_execute(self, payload: dict[str, Any]) -> ActionResult:
        try:
            item = self.promote(
                content=payload["content"], title=payload["title"], source_ref=payload["source_ref"],
                kind=str(payload.get("kind") or "reference"), metadata=payload.get("metadata"),
            )
        except ValueError as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "promote"},
                                error_code="knowledge_source_ref_required", error=str(exc))
        return ActionResult(True, item, {"ok": True, "operation": "promote", "item_id": item["item_id"]})

    def _delete_execute(self, payload: dict[str, Any]) -> ActionResult:
        item_id = payload["item_id"]
        try:
            self.store.get(item_id)
        except KeyError:
            return ActionResult(False, {}, {"ok": False, "operation": "delete"},
                                error_code="knowledge_item_unknown", error="unknown knowledge item")
        self.store.delete(item_id)
        return ActionResult(True, {"item_id": item_id, "deleted": True},
                            {"ok": True, "operation": "delete", "item_id": item_id})


def _rank_merge(ranked: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """Interleave per-mechanism rankings.

    Scores from different mechanisms are not comparable, so results are merged by
    rank and never by numeric fusion.
    """
    merged: list[dict[str, Any]] = []
    lists = [rows for rows in ranked if rows]
    for position in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if position < len(rows):
                merged.append(rows[position])
                if len(merged) >= limit:
                    return merged
    return merged
