from __future__ import annotations

from dataclasses import asdict

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    CapabilitySpec,
    ExecutionBudget,
    RetryPolicy,
)
from atlas_core.tasks import TaskStore
from atlas_core.verification import VerificationResult, VerifierRegistry

from .store import KnowledgeStore


def _payload(store: TaskStore, request):
    candidate_ids = request.direct_input_artifact_ids or request.input_artifact_ids
    if not candidate_ids:
        raise ValueError("Knowledge capability requires an input artifact.")
    value = store.get_artifact(candidate_ids[-1]).payload
    if not isinstance(value, dict):
        raise ValueError("Knowledge input artifact must be an object.")
    return value


def _search_verifier(spec, output, context):
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        return VerificationResult("fail", "knowledge search output is malformed")
    return VerificationResult("pass", "knowledge search result contract valid")


def register_knowledge_capabilities(
    capabilities: CapabilityRegistry,
    verifiers: VerifierRegistry,
    *,
    task_store: TaskStore,
    knowledge_store: KnowledgeStore,
) -> None:
    verifiers.register("knowledge.search_contract", _search_verifier, replace=True)

    def ingest(request):
        data = _payload(task_store, request)
        result = knowledge_store.ingest_text(
            title=str(data.get("title") or ""),
            text=str(data.get("text") or ""),
            source_uri=(str(data["source_uri"]) if data.get("source_uri") else None),
            metadata=(dict(data["metadata"]) if isinstance(data.get("metadata"), dict) else {}),
            chunk_chars=int(data.get("chunk_chars", 4000)),
            overlap_chars=int(data.get("overlap_chars", 400)),
        )
        output = {
            "document_id": result.document.id,
            "title": result.document.title,
            "source_uri": result.document.source_uri,
            "content_sha256": result.document.content_sha256,
            "chunk_count": result.document.chunk_count,
            "created": result.created,
        }
        return CapabilityOutcome(
            "pass",
            output=output,
            output_kind="knowledge_ingest_result",
            receipt={"ok": True, "document_id": result.document.id},
            claims=(
                {
                    "kind": "executed",
                    "subject": f"knowledge.document.{result.document.id}",
                    "value": output,
                },
            ),
        )

    def search(request):
        data = _payload(task_store, request)
        query = str(data.get("query") or "")
        limit = int(data.get("limit", 8))
        hits = knowledge_store.search(query, limit=limit)
        rows = [
            {
                "chunk_id": hit.chunk.id,
                "document_id": hit.chunk.document_id,
                "title": hit.chunk.title,
                "source_uri": hit.chunk.source_uri,
                "ordinal": hit.chunk.ordinal,
                "text": hit.chunk.text,
                "sha256": hit.chunk.sha256,
                "score": hit.score,
            }
            for hit in hits
        ]
        return CapabilityOutcome(
            "pass",
            output={"query": query, "results": rows},
            output_kind="knowledge_search_results",
            claims=tuple(
                {
                    "kind": "retrieved",
                    "subject": f"knowledge.chunk.{row['chunk_id']}",
                    "value": row,
                }
                for row in rows
            ),
        )

    capabilities.register(
        CapabilitySpec(
            id="knowledge.ingest_text",
            description="Persist and chunk extracted text into Atlas full-text knowledge with provenance.",
            executor_kind="deterministic",
            required_authority="modify_internal",
            input_schema={
                "type": "object",
                "required": ["title", "text"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                    "source_uri": {"type": ["string", "null"]},
                    "metadata": {"type": "object"},
                    "chunk_chars": {"type": "integer", "minimum": 1},
                    "overlap_chars": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["document_id", "title", "content_sha256", "chunk_count", "created"],
                "properties": {
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "source_uri": {"type": ["string", "null"]},
                    "content_sha256": {"type": "string"},
                    "chunk_count": {"type": "integer", "minimum": 1},
                    "created": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            output_kind="knowledge_ingest_result",
            side_effects=("internal_knowledge_store",),
            verifier_id="core.receipt",
            idempotent=True,
            parallel_safe=False,
            privacy="local_only",
            budget=ExecutionBudget(max_attempts=2, max_context_chars=16_000),
        ),
        ingest,
    )
    capabilities.register(
        CapabilitySpec(
            id="knowledge.search",
            description="Retrieve source-grounded chunks from Atlas local full-text knowledge.",
            executor_kind="deterministic",
            required_authority="read",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["query", "results"],
                "properties": {
                    "query": {"type": "string"},
                    "results": {"type": "array"},
                },
                "additionalProperties": False,
            },
            output_kind="knowledge_search_results",
            verifier_id="knowledge.search_contract",
            idempotent=True,
            parallel_safe=True,
            privacy="local_only",
            budget=ExecutionBudget(max_attempts=1, max_context_chars=16_000),
            retry_policy=RetryPolicy(retry_on=(), stop_on=("pass", "fail", "blocked", "abstain", "rework")),
        ),
        search,
    )
