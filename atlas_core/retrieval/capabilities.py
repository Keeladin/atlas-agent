from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from atlas_core.capabilities import CapabilityRegistry

from .embedding import EmbeddingProvider
from .fusion import reciprocal_rank_fusion
from .models import RankedCandidate

_WORD = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from", "hi", "how",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "that", "the", "this", "to",
    "was", "what", "when", "where", "which", "who", "with", "you", "your",
}


def _tokens(value: str) -> set[str]:
    return {x.casefold() for x in _WORD.findall(value) if len(x) > 1 and x.casefold() not in _STOPWORDS}


def _search_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(str(k) + " " + _search_text(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_search_text(v) for v in value)
    return str(value) if isinstance(value, (str, int, float)) else ""


def capability_document(registration) -> str:
    """Deterministic semantic representation derived only from live registry truth."""
    definition = registration.definition
    metadata = registration.metadata
    schema = definition.input_schema if isinstance(definition.input_schema, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    parameter_text = []
    for name, spec in sorted(properties.items()):
        if isinstance(spec, dict):
            parameter_text.append(
                " ".join(filter(None, [name, str(spec.get("title") or ""), str(spec.get("description") or "")]))
            )
        else:
            parameter_text.append(name)
    fields = [
        f"Capability: {definition.id}",
        f"Operation: {definition.operation}",
        f"Description: {definition.description}",
        f"Tags: {' '.join(definition.tags)}",
        f"Source: {definition.source}",
        f"Purpose: {metadata.get('purpose') or ''}",
        f"Category: {metadata.get('category') or ''}",
        f"Tool name: {metadata.get('tool_name') or ''}",
        "Parameters: " + " ; ".join(parameter_text),
    ]
    return "\n".join(fields)


def registry_fingerprint(registry: CapabilityRegistry) -> str:
    """Canonical definition/schema identity for the live capability registry."""
    rows = []
    for reg in registry.all():
        d = reg.definition
        rows.append({
            "id": d.id, "description": d.description, "operation": d.operation,
            "tags": list(d.tags), "source": d.source, "schema": d.input_schema,
            "metadata": {k: reg.metadata.get(k) for k in ("purpose", "category", "tool_name")},
        })
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class CapabilityRetriever:
    """Hybrid objective-to-capability retrieval over the live CapabilityRegistry."""

    def __init__(self, embedder: EmbeddingProvider, *, core_signposts: set[str] | None = None) -> None:
        self.embedder = embedder
        self.core_signposts = set(core_signposts or ())
        self._fingerprint = ""
        self._ids: list[str] = []
        self._documents: dict[str, str] = {}
        self._vectors: dict[str, list[float]] = {}

    @staticmethod
    def _registry_fingerprint(registry: CapabilityRegistry) -> str:
        return registry_fingerprint(registry)

    def _ensure_index(self, registry: CapabilityRegistry) -> None:
        fingerprint = self._registry_fingerprint(registry)
        if fingerprint == self._fingerprint:
            return
        regs = list(registry.all())
        documents = {reg.definition.id: capability_document(reg) for reg in regs}
        ids = [reg.definition.id for reg in regs]
        vectors = self.embedder.embed_documents([documents[item_id] for item_id in ids]) if ids else []
        self._ids = ids
        self._documents = documents
        self._vectors = {item_id: vector for item_id, vector in zip(ids, vectors, strict=True)}
        self._fingerprint = fingerprint

    def search_ids(self, registry: CapabilityRegistry, query: str, *, limit: int = 40) -> list[str]:
        self._ensure_index(registry)
        query_tokens = _tokens(query)
        sparse_scored: list[tuple[float, str]] = []
        exact: list[str] = []
        qfold = query.casefold()
        for reg in registry.all():
            d = reg.definition
            id_tokens = _tokens(d.id)
            semantic_tokens = _tokens(d.description + " " + " ".join(d.tags))
            purpose_tokens = _tokens(_search_text({
                "purpose": reg.metadata.get("purpose"), "category": reg.metadata.get("category"),
                "tool_name": reg.metadata.get("tool_name"),
            }))
            schema_tokens = _tokens(_search_text(d.input_schema))
            score = sum(8 for token in query_tokens if token in id_tokens)
            score += sum(5 for token in query_tokens if token in purpose_tokens and token not in id_tokens)
            score += sum(3 for token in query_tokens if token in semantic_tokens and token not in id_tokens)
            score += sum(1 for token in query_tokens if token in schema_tokens and token not in id_tokens | semantic_tokens | purpose_tokens)
            if score > 0:
                sparse_scored.append((float(score), d.id))
            tool_name = str(reg.metadata.get("tool_name") or "").casefold()
            if d.id.casefold() in qfold or (tool_name and tool_name in qfold):
                exact.append(d.id)
        sparse_scored.sort(key=lambda row: (-row[0], row[1]))
        sparse = [
            RankedCandidate(item_id=item_id, rank=i, source="sparse", raw_score=score)
            for i, (score, item_id) in enumerate(sparse_scored, 1)
        ]

        qvec = self.embedder.embed_query(query)
        dense_scored = []
        for item_id in self._ids:
            vec = self._vectors[item_id]
            similarity = sum(a * b for a, b in zip(qvec, vec, strict=True))
            dense_scored.append((similarity, item_id))
        dense_scored.sort(key=lambda row: (-row[0], row[1]))
        dense = [
            RankedCandidate(item_id=item_id, rank=i, source="dense", raw_score=score)
            for i, (score, item_id) in enumerate(dense_scored, 1)
        ]

        fused = reciprocal_rank_fusion([sparse, dense], weights={"sparse": 1.15, "dense": 1.0})
        ordered: list[str] = []
        # When lexical/exact retrieval has no signal, core signposts are safer
        # discovery anchors than an arbitrary nearest dense capability. Dense
        # results still follow, so the model can widen beyond the anchors.
        anchors = sorted(item_id for item_id in self.core_signposts if item_id in self._documents) if not exact and not sparse else []
        for item_id in exact + anchors + [row.item_id for row in fused]:
            if item_id not in ordered:
                ordered.append(item_id)
            if len(ordered) >= limit:
                break
        if not ordered:
            ordered = [item_id for item_id in self._ids if item_id in self.core_signposts][:limit]
        return ordered
