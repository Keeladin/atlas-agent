from __future__ import annotations

import re

from atlas_core.capabilities import (
    CapabilityExecutionProfile,
    CapabilityOutcome,
    ExecutionBudget,
    RetryPolicy,
    require,
)
from atlas_core.evidence import qualifies_as_source_evidence
from atlas_core.verification import VerificationResult, VerifierRegistry
from atlas_core.work.inventory import DeploymentInventory

from .store import (
    MAX_SEARCH_RESULT_CHARS,
    KnowledgeStore,
    content_tokens,
    hit_is_relevant,
    token_overlap,
)

_SEARCH_PREFIXES = (
    "Search Atlas knowledge for:",
    "Search local knowledge for:",
    "Search knowledge for:",
    "Search for:",
    "Search:",
)


def parse_search_objective(objective: str) -> str | None:
    """Return a search query when the objective is a retrieval request."""
    text = (objective or "").strip()
    if not text:
        return None
    lowered = text.casefold()
    for prefix in _SEARCH_PREFIXES:
        if lowered.startswith(prefix.casefold()):
            query = text[len(prefix) :].strip()
            return query or None
    return None


_QUESTION_STARTERS = {
    "what", "why", "how", "when", "where", "who", "which",
    "does", "do", "is", "are", "can", "should", "explain",
}


def is_knowledge_question(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if "?" in value:
        return True
    first = value.split(None, 1)[0].casefold().strip("\"'")
    return first in _QUESTION_STARTERS


def search_query_from_task(*, objective: str, description: str | None = None) -> str:
    """Deterministic query for a search step that has no request artifact."""
    parsed = parse_search_objective(objective)
    if parsed:
        return parsed
    for candidate in (description, objective):
        text = (candidate or "").strip()
        if text:
            return text
    raise ValueError("knowledge.search requires a non-empty query.")


_INGEST_PREFIXES = (
    "Index local knowledge source ",
    "Index local knowledge source:",
    "Index knowledge source ",
    "Ingest local knowledge source ",
    "Ingest local knowledge source:",
)
_INGEST_SUFFIXES = {".md", ".txt", ".rst", ".json"}


def parse_ingest_objective(objective: str) -> str | None:
    """Return a source name or path when the objective is an ingest request."""
    text = (objective or "").strip().strip('"').strip("'")
    if not text:
        return None
    lowered = text.casefold()
    for prefix in _INGEST_PREFIXES:
        if lowered.startswith(prefix.casefold()):
            return text[len(prefix) :].strip() or None
    if lowered.startswith("index "):
        rest = text[6:].strip()
        if any(rest.casefold().endswith(suffix) for suffix in _INGEST_SUFFIXES):
            return rest
    return None


def resolve_knowledge_source(
    label: str,
    *,
    provider_namespace: str | None = None,
    root_id: str | None = None,
    configuration_revision: str | None = None,
) -> dict | None:
    """Parse controlled source intent without touching the filesystem."""
    text = (label or "").strip().strip('"').strip("'")
    if not text or not provider_namespace or not root_id or not configuration_revision:
        return None
    try:
        from atlas_core.sources.local import validate_relative_path

        relative_path = validate_relative_path(text)
    except Exception:
        return None
    return {
        "provider_namespace": provider_namespace,
        "root_id": root_id,
        "configuration_revision": configuration_revision,
        "relative_path": relative_path,
    }


def ingest_request_from_task(
    *,
    objective: str,
    description: str | None = None,
    provider_namespace: str,
    root_id: str,
    configuration_revision: str,
) -> dict:
    """Build controlled source intent from durable task text without acquisition."""
    label = parse_ingest_objective(objective)
    if not label:
        label = parse_ingest_objective(description or "") or (description or "").strip() or objective.strip()
    source = resolve_knowledge_source(
        label,
        provider_namespace=provider_namespace,
        root_id=root_id,
        configuration_revision=configuration_revision,
    )
    if source is None:
        raise ValueError(
            "knowledge ingestion requires a valid configured root-relative source; "
            f"could not parse {label!r}."
        )
    title = source["relative_path"].rsplit("/", 1)[-1]
    return {
        "files.read": source,
        "knowledge.ingest_text": {
            "title": title,
            "chunk_chars": 4000,
            "overlap_chars": 400,
        },
    }


def _content_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w-]+", (text or "").casefold(), flags=re.UNICODE)
        if len(token) > 2 and token not in {
            "a", "an", "and", "are", "for", "from", "how", "the", "this", "that",
            "what", "when", "where", "which", "who", "why", "with",
        }
    }


def _hit_relevance(query_tokens: set[str], text: str) -> int | None:
    tokens = _content_tokens(text)
    overlap = query_tokens & tokens
    if not overlap:
        return None
    score = len(overlap)
    for term in ("purpose", "durable", "friction", "owns", "runtime", "task"):
        if term in query_tokens and term in tokens:
            score += 2
    if "```" in text or "flowchart" in text or "├──" in text:
        score -= 4
    if text and not (text[0].isupper() or text[0] in "#>"):
        score -= 1
    return score


def _best_excerpt(text: str, query_tokens: set[str], limit: int = 420) -> str:
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\n+", text or "") if piece.strip()]
    if not pieces:
        return ""
    best = max(
        pieces,
        key=lambda piece: _hit_relevance(query_tokens, piece) if _hit_relevance(query_tokens, piece) is not None else -999,
    )
    compact = " ".join(best.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def grounded_answer_from_hits(query: str, results: list[dict]) -> str:
    """Build a source-grounded answer from retrieved chunks. No new claims."""
    query_tokens = _content_tokens(query)
    ranked: list[tuple[int, dict]] = []
    for hit in results:
        if not isinstance(hit, dict):
            continue
        score = _hit_relevance(query_tokens, str(hit.get("text") or ""))
        if score is not None:
            ranked.append((score, hit))
    ranked.sort(key=lambda item: item[0], reverse=True)
    chosen = [hit for _, hit in ranked[:3]]
    if not chosen:
        return (
            "Retrieved sources do not contain enough overlapping evidence to answer "
            f"{query!r}. Open the source hits and refine the query."
        )
    lines = [
        f"From retrieved sources, answering: {query}",
        "",
    ]
    for hit in chosen:
        title = hit.get("title") or "Untitled"
        digest = (hit.get("sha256") or "")[:12]
        excerpt = _best_excerpt(str(hit.get("text") or ""), query_tokens)
        if not excerpt:
            continue
        lines.append(excerpt)
        citation = title
        if digest:
            citation += f" · hash {digest}…"
        lines.append(f"— {citation}")
        lines.append("")
    lines.append("Atlas did not add claims beyond these excerpts.")
    return "\n".join(lines).rstrip()


def _payload(store, request):
    candidate_ids = request.direct_input_artifact_ids or request.input_artifact_ids
    if not candidate_ids:
        raise ValueError("Knowledge capability requires an input artifact.")
    value = store.get_artifact(candidate_ids[-1]).payload
    if not isinstance(value, dict):
        raise ValueError("Knowledge input artifact must be an object.")
    return value


def _acquired_source(store, request):
    observation_artifact = None
    content_artifact = None
    for artifact_id in request.dependency_artifact_ids:
        artifact = store.get_artifact(artifact_id)
        if not qualifies_as_source_evidence(artifact):
            continue
        if artifact.provenance_category == "acquired_observation" and artifact.kind == "files_read_observation":
            observation_artifact = artifact
        elif artifact.provenance_category == "acquired_content" and artifact.kind == "files_acquired_content":
            content_artifact = artifact
    if observation_artifact is None and content_artifact is None:
        return None
    if observation_artifact is None or content_artifact is None:
        raise ValueError("Knowledge ingest requires both acquired observation and content artifacts.")
    observation = observation_artifact.payload.get("observation")
    content = content_artifact.payload
    if not isinstance(observation, dict) or not isinstance(content, dict):
        raise ValueError("Knowledge source artifacts are malformed.")
    if observation.get("consistency") != "stable" or observation.get("completeness") != "complete":
        raise ValueError("Knowledge ingest requires a complete stable source observation.")
    if content.get("source_observation_id") != observation.get("observation_id"):
        raise ValueError("Knowledge source observation/content identity mismatch.")
    if content.get("source_observation_payload_sha256") != observation.get("observation_payload_sha256"):
        raise ValueError("Knowledge source observation payload mismatch.")
    if content.get("source_ref") != observation.get("source_ref"):
        raise ValueError("Knowledge source reference mismatch.")
    if not isinstance(content.get("text"), str):
        raise ValueError("Knowledge acquired content has no UTF-8 text.")
    return {
        "text": content["text"],
        "observation_artifact_id": observation_artifact.id,
        "acquired_content_artifact_id": content_artifact.id,
        "evidence_artifact_ids": (observation_artifact.id, content_artifact.id),
    }


def _search_verifier(spec, output, context):
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        return VerificationResult("fail", "knowledge search output is malformed")
    results = output.get("results") or []
    query = str(output.get("query") or "")
    status = str(output.get("status") or "")
    if results and status == "no_relevant_results":
        return VerificationResult("fail", "search marked no_relevant_results but returned chunks")
    if not results:
        return VerificationResult(
            "pass",
            "no relevant local knowledge",
            {"status": status or "no_relevant_results", "result_count": 0},
        )
    query_tokens = content_tokens(query)
    for index, hit in enumerate(results):
        if not isinstance(hit, dict) or not hit.get("chunk_id"):
            return VerificationResult("fail", f"search hit {index} is missing provenance")
        if "overlap_count" in hit or "overlap_ratio" in hit:
            count = int(hit.get("overlap_count") or 0)
            ratio = float(hit.get("overlap_ratio") or 0.0)
            if len(query_tokens) <= 2:
                relevant = count >= 1
            else:
                relevant = count >= 2 and ratio >= 0.25
        else:
            relevant = hit_is_relevant(query_tokens, str(hit.get("text") or ""))
        if not relevant:
            return VerificationResult(
                "fail",
                "search results are not relevant to the query",
                {"status": "irrelevant", "result_count": len(results)},
            )
    return VerificationResult(
        "pass",
        "knowledge search result contract valid",
        {"status": status or "ok", "result_count": len(results)},
    )


def _query_excerpt(text: str, query_tokens: tuple[str, ...], limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    lowered = text.casefold()
    pos = -1
    for token in query_tokens:
        pos = lowered.find(token)
        if pos >= 0:
            break
    if pos < 0:
        return text[:limit]
    start = max(0, pos - max(0, limit // 4))
    return text[start : start + limit]


def _bounded_search_rows(query: str, hits) -> tuple[list[dict], bool]:
    query_tokens = content_tokens(query)
    rows: list[dict] = []
    used = 0
    truncated = False
    for hit in hits:
        remaining = MAX_SEARCH_RESULT_CHARS - used
        if remaining <= 0:
            truncated = True
            break
        text = _query_excerpt(hit.chunk.text, query_tokens, remaining)
        if len(text) < len(hit.chunk.text):
            truncated = True
        count, ratio = token_overlap(query_tokens, hit.chunk.text)
        rows.append(
            {
                "chunk_id": hit.chunk.id,
                "document_id": hit.chunk.document_id,
                "title": hit.chunk.title,
                "ordinal": hit.chunk.ordinal,
                "text": text,
                "sha256": hit.chunk.sha256,
                "score": hit.score,
                "overlap_count": count,
                "overlap_ratio": round(ratio, 4),
                "source_provenance": [
                    {
                        "observation_artifact_id": item.observation_artifact_id,
                        "acquired_content_artifact_id": item.acquired_content_artifact_id,
                    }
                    for item in hit.chunk.source_provenance
                ],
            }
        )
        used += len(text)
        if truncated:
            break
    return rows, truncated


def register_knowledge_capabilities(
    inventory: DeploymentInventory,
    verifiers: VerifierRegistry,
    *,
    store,
    knowledge_store: KnowledgeStore,
) -> None:
    verifiers.register("knowledge.search_contract", _search_verifier, replace=True)

    def ingest(request):
        data = _payload(store, request)
        acquired = _acquired_source(store, request)
        if acquired is None:
            raise ValueError("Knowledge ingest requires controlled files.read acquisition.")
        result = knowledge_store.ingest_text(
            title=str(data.get("title") or ""),
            text=acquired["text"],
            metadata=(dict(data["metadata"]) if isinstance(data.get("metadata"), dict) else {}),
            chunk_chars=int(data.get("chunk_chars", 4000)),
            overlap_chars=int(data.get("overlap_chars", 400)),
            observation_artifact_id=acquired["observation_artifact_id"],
            acquired_content_artifact_id=acquired["acquired_content_artifact_id"],
        )
        output = {
            "document_id": result.document.id,
            "normalized_text_sha256": result.document.normalized_text_sha256,
            "observation_artifact_id": acquired["observation_artifact_id"],
            "acquired_content_artifact_id": acquired["acquired_content_artifact_id"],
            "chunk_count": result.document.chunk_count,
            "status": "created" if result.created else "deduplicated",
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
                    **(
                        {"evidence_artifact_ids": acquired["evidence_artifact_ids"]}
                        if acquired is not None
                        else {}
                    ),
                },
            ),
        )

    def search(request):
        data = _payload(store, request)
        query = str(data.get("query") or "")
        limit = int(data.get("limit", 8))
        hits = knowledge_store.search(query, limit=limit)
        rows, truncated = _bounded_search_rows(query, hits)
        copied: dict[str, str] = {}

        def materialize(origin_id: str) -> str | None:
            if origin_id in copied:
                return copied[origin_id]
            try:
                origin = store.get_artifact(origin_id)
            except Exception:
                return None
            if origin.work_id == request.work_id:
                copied[origin_id] = origin.id
                return origin.id
            try:
                replica = store.replicate_source_artifact(
                    origin.id,
                    work_id=request.work_id,
                    step_id=request.step_id,
                    metadata={
                        "execution_id": request.execution_id,
                        "purpose": "knowledge_source_evidence",
                    },
                )
            except ValueError:
                return None
            if replica.sha256 != origin.sha256:
                raise ValueError("Knowledge source evidence replica hash mismatch.")
            copied[origin_id] = replica.id
            return replica.id

        claim_rows: list[tuple[dict, tuple[str, ...]]] = []
        for row in rows:
            evidence: list[str] = []
            for provenance in row.get("source_provenance", []):
                for key in ("observation_artifact_id", "acquired_content_artifact_id"):
                    artifact_id = materialize(str(provenance.get(key) or ""))
                    if artifact_id:
                        evidence.append(artifact_id)
            claim_rows.append((row, tuple(dict.fromkeys(evidence))))
        status = "ok" if rows else "no_relevant_results"
        return CapabilityOutcome(
            "pass",
            output={
                "query": query,
                "results": rows,
                "status": status,
                "truncated": truncated,
            },
            output_kind="knowledge_search_results",
            claims=tuple(
                {
                    "kind": "retrieved",
                    "subject": f"knowledge.chunk.{row['chunk_id']}",
                    "value": {
                        "chunk_id": row["chunk_id"],
                        "document_id": row["document_id"],
                        "title": row["title"],
                        "ordinal": row["ordinal"],
                        "sha256": row["sha256"],
                        "score": row["score"],
                        "overlap_count": row["overlap_count"],
                        "overlap_ratio": row["overlap_ratio"],
                        "source_provenance": row["source_provenance"],
                    },
                    "evidence_artifact_ids": evidence_ids,
                }
                for row, evidence_ids in claim_rows
                if evidence_ids
            ),
        )

    def answer(request):
        payload = None
        for artifact_id in reversed(request.input_artifact_ids):
            artifact = store.get_artifact(artifact_id)
            if artifact.kind == "knowledge_search_results" and isinstance(artifact.payload, dict):
                payload = artifact.payload
                break
        if payload is None:
            raise ValueError("knowledge.answer requires a knowledge_search_results artifact.")
        query = str(payload.get("query") or "")
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        text = grounded_answer_from_hits(query, results)
        return CapabilityOutcome(
            "pass",
            output=text,
            output_kind="grounded_answer",
        )

    require("knowledge.ingest_text")
    inventory.register(
        CapabilityExecutionProfile(
            capability_id="knowledge.ingest_text",
            version="2.0.0",
            executor_kind="deterministic",
            input_schema={
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "metadata": {"type": "object"},
                    "chunk_chars": {"type": "integer", "minimum": 1},
                    "overlap_chars": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": [
                    "document_id", "normalized_text_sha256", "observation_artifact_id",
                    "acquired_content_artifact_id", "chunk_count", "status",
                ],
                "properties": {
                    "document_id": {"type": "string"},
                    "normalized_text_sha256": {"type": "string"},
                    "observation_artifact_id": {"type": "string"},
                    "acquired_content_artifact_id": {"type": "string"},
                    "chunk_count": {"type": "integer", "minimum": 1},
                    "status": {"type": "string", "enum": ["created", "deduplicated"]},
                },
                "additionalProperties": False,
            },
            output_kind="knowledge_ingest_result",
            requires_artifact_kinds=("files_acquired_content",),
            side_effects=("internal_knowledge_store",),
            verifier_id="core.receipt",
            idempotent=True,
            parallel_safe=False,
            privacy="local_only",
            budget=ExecutionBudget(max_attempts=2, max_context_chars=16_000),
        ),
        ingest,
    )
    require("knowledge.search")
    inventory.register(
        CapabilityExecutionProfile(
            capability_id="knowledge.search",
            executor_kind="deterministic",
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
                    "status": {"type": "string"},
                    "truncated": {"type": "boolean"},
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
    require("knowledge.answer")
    inventory.register(
        CapabilityExecutionProfile(
            capability_id="knowledge.answer",
            executor_kind="deterministic",
            input_schema={
                "type": "object",
                "description": "Invoked with dependency artifacts; a knowledge_search_results artifact is required.",
            },
            requires_artifact_kinds=("knowledge_search_results",),
            output_schema={"type": "string", "minLength": 1},
            output_kind="grounded_answer",
            verifier_id="core.nonempty",
            idempotent=True,
            parallel_safe=True,
            privacy="local_only",
            budget=ExecutionBudget(max_attempts=1, max_context_chars=96_000),
        ),
        answer,
    )
