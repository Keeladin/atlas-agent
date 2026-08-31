from __future__ import annotations

import hashlib, json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, CapabilityRegistry, CapabilityRuntime, ScopeResolution
from atlas_core.providers import ModelRequest, ProviderRuntime
from atlas_core.provenance import InvocationProvenance
from atlas_core.schema_validation import SchemaValidationError, validate_json

ARTIFACT_CLASSES = {
    "A": "durable_reference", "B": "operational_input", "C": "evidence",
    "D": "transactional_record", "E": "review_or_uncertain",
}
WORKFLOW_CLASSES = {"A": "knowledge.ingest", "B": "operational.process", "C": "owner.review"}
WORKFLOW_CLASS_BY_INTENT = {intent: code for code, intent in WORKFLOW_CLASSES.items()}
SEMANTIC_REPRESENTATION_NEEDS = ("text", "layout", "tables", "visual")

# A queued intake event that keeps failing is retired after this many sweeps so it
# stops consuming the sweep cap and a provider call each time. Retirement is
# visible and reversible (ArtifactIntakeStore.requeue_event), never a silent drop.
MAX_INTAKE_ATTEMPTS = 3

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "required": ["artifact_class", "purpose", "knowledge_disposition", "relationship", "creates_work", "workflow_class", "workflow_intent", "confidence", "inspection_sufficiency", "unresolved_questions", "reason"],
    "properties": {
        "artifact_class": {"type": "string", "enum": list(ARTIFACT_CLASSES)},
        "purpose": {"type": "string", "minLength": 1},
        "knowledge_disposition": {"type": "string", "enum": ["ingest", "retain", "no", "maybe"]},
        "relationship": {"type": "string", "enum": ["new", "possible_revision", "duplicate", "supplement", "related", "unknown"]},
        "creates_work": {"type": "boolean"},
        "workflow_class": {"type": ["string", "null"]},
        "workflow_intent": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "inspection_sufficiency": {"type": "string", "enum": ["sufficient", "partial", "insufficient"]},
        "unresolved_questions": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
        "representation_needs": {"type": "array", "maxItems": 4, "uniqueItems": True, "items": {"type": "string", "enum": list(SEMANTIC_REPRESENTATION_NEEDS)}},
        "reason": {"type": "string", "minLength": 1},
    }, "additionalProperties": False,
}


class ArtifactIntakeStore:
    def __init__(self, path: str | Path) -> None: self.path = Path(path)
    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.execute("PRAGMA busy_timeout=5000")
        try:
            with db: yield db
        finally: db.close()
    def initialize(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS artifact_intakes(
                intake_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, source_artifact_id TEXT, principal_id TEXT NOT NULL,
                source_event_kind TEXT NOT NULL, artifact_class TEXT NOT NULL, purpose TEXT NOT NULL,
                knowledge_disposition TEXT NOT NULL, relationship TEXT NOT NULL, workflow_class TEXT,
                workflow_intent TEXT, confidence REAL NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
                work_id TEXT, provider TEXT, model TEXT, inspection_occurrence_id TEXT, inspection_json TEXT, representation_needs_json TEXT NOT NULL DEFAULT '[]', event_fingerprint TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(artifact_intakes)")}
            if "source_artifact_id" not in columns:
                db.execute("ALTER TABLE artifact_intakes ADD COLUMN source_artifact_id TEXT")
                db.execute("UPDATE artifact_intakes SET source_artifact_id=artifact_id WHERE source_artifact_id IS NULL")
            if "inspection_occurrence_id" not in columns: db.execute("ALTER TABLE artifact_intakes ADD COLUMN inspection_occurrence_id TEXT")
            if "inspection_json" not in columns: db.execute("ALTER TABLE artifact_intakes ADD COLUMN inspection_json TEXT")
            if "event_fingerprint" not in columns: db.execute("ALTER TABLE artifact_intakes ADD COLUMN event_fingerprint TEXT")
            if "representation_needs_json" not in columns: db.execute("ALTER TABLE artifact_intakes ADD COLUMN representation_needs_json TEXT NOT NULL DEFAULT '[]'")
            db.execute("CREATE INDEX IF NOT EXISTS artifact_intake_event ON artifact_intakes(principal_id,artifact_id,source_event_kind,event_fingerprint)")
            db.execute("""CREATE TABLE IF NOT EXISTS artifact_intake_pending(
                event_fingerprint TEXT PRIMARY KEY, principal_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
                source_event_kind TEXT NOT NULL, candidate_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT, last_attempt_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            pending_columns = {row[1] for row in db.execute("PRAGMA table_info(artifact_intake_pending)")}
            if "attempts" not in pending_columns: db.execute("ALTER TABLE artifact_intake_pending ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "state" not in pending_columns: db.execute("ALTER TABLE artifact_intake_pending ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'")
            if "last_error" not in pending_columns: db.execute("ALTER TABLE artifact_intake_pending ADD COLUMN last_error TEXT")
            if "last_attempt_at" not in pending_columns: db.execute("ALTER TABLE artifact_intake_pending ADD COLUMN last_attempt_at TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS artifact_intake_pending_state ON artifact_intake_pending(principal_id,state,created_at)")
    def record(self, *, artifact_id: str, source_artifact_id: str | None, principal_id: str, source_event_kind: str,
               decision: dict[str, Any], status: str, work_id: str | None, provider: str, model: str,
               inspection_occurrence_id: str | None = None, inspection: dict[str, Any] | None = None) -> dict[str, Any]:
        iid = f"intake_{uuid4().hex}"
        with self._db() as db:
            db.execute("""INSERT INTO artifact_intakes(intake_id,artifact_id,source_artifact_id,principal_id,source_event_kind,
                artifact_class,purpose,knowledge_disposition,relationship,workflow_class,workflow_intent,
                confidence,reason,status,work_id,provider,model,inspection_occurrence_id,inspection_json,representation_needs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, artifact_id, source_artifact_id or artifact_id, principal_id, source_event_kind, decision["artifact_class"], decision["purpose"],
                 decision["knowledge_disposition"], decision["relationship"], decision.get("workflow_class"),
                 decision.get("workflow_intent"), decision["confidence"], decision["reason"], status, work_id, provider, model,
                 inspection_occurrence_id, json.dumps(inspection or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                 json.dumps(decision.get("representation_needs") or [], sort_keys=True, separators=(",", ":"))))
        return self.get(iid)
    def get(self, intake_id: str) -> dict[str, Any]:
        with self._db() as db: row = db.execute("SELECT * FROM artifact_intakes WHERE intake_id=?", (intake_id,)).fetchone()
        if row is None: raise KeyError(intake_id)
        return dict(row)
    def latest_reusable(self, *, principal_id: str, artifact_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                """SELECT * FROM artifact_intakes WHERE principal_id=? AND artifact_id=?
                   AND status IN ('routed','no_work','reused_existing_content')
                   ORDER BY created_at DESC,intake_id DESC LIMIT 1""",
                (principal_id, artifact_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def has_event(self, *, principal_id: str, artifact_id: str, source_event_kind: str, event_fingerprint: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT 1 FROM artifact_intakes WHERE principal_id=? AND artifact_id=? AND source_event_kind=? AND event_fingerprint=? LIMIT 1",
                             (principal_id, artifact_id, source_event_kind, event_fingerprint)).fetchone()
        return row is not None
    def set_event_fingerprint(self, intake_id: str, event_fingerprint: str) -> None:
        with self._db() as db:
            db.execute("UPDATE artifact_intakes SET event_fingerprint=? WHERE intake_id=?", (event_fingerprint, intake_id))
    def enqueue_event(self, *, principal_id: str, artifact_id: str, source_event_kind: str, event_fingerprint: str, candidate: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("INSERT INTO artifact_intake_pending(event_fingerprint,principal_id,artifact_id,source_event_kind,candidate_json) VALUES (?,?,?,?,?) ON CONFLICT(event_fingerprint) DO NOTHING",
                       (event_fingerprint, principal_id, artifact_id, source_event_kind, json.dumps(candidate, sort_keys=True, separators=(",", ":"), default=str)))
    def pending_events(self, principal_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows=db.execute("SELECT * FROM artifact_intake_pending WHERE principal_id=? AND state='pending' ORDER BY created_at,event_fingerprint", (principal_id,)).fetchall()
        return tuple({**dict(row), "candidate": json.loads(row["candidate_json"])} for row in rows)
    def dead_letter_events(self, principal_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows=db.execute("SELECT * FROM artifact_intake_pending WHERE principal_id=? AND state='dead_letter' ORDER BY last_attempt_at,event_fingerprint", (principal_id,)).fetchall()
        return tuple({**dict(row), "candidate": json.loads(row["candidate_json"])} for row in rows)
    def record_attempt(self, event_fingerprint: str, *, error: str | None, max_attempts: int) -> dict[str, Any]:
        """Count one failed classification and retire the event once it stops being worth retrying.

        The row is never deleted: a dead-lettered event keeps blocking re-enqueue
        through the fingerprint primary key, stays visible, and can be requeued.
        """
        with self._db() as db:
            row = db.execute("SELECT * FROM artifact_intake_pending WHERE event_fingerprint=?", (event_fingerprint,)).fetchone()
            if row is None: raise KeyError(event_fingerprint)
            attempts = int(row["attempts"] or 0) + 1
            state = "dead_letter" if attempts >= max_attempts else "pending"
            db.execute("UPDATE artifact_intake_pending SET attempts=?,state=?,last_error=?,last_attempt_at=CURRENT_TIMESTAMP WHERE event_fingerprint=?",
                       (attempts, state, error, event_fingerprint))
            updated = db.execute("SELECT * FROM artifact_intake_pending WHERE event_fingerprint=?", (event_fingerprint,)).fetchone()
        return {**dict(updated), "candidate": json.loads(updated["candidate_json"])}
    def requeue_event(self, event_fingerprint: str) -> dict[str, Any]:
        with self._db() as db:
            changed = db.execute("UPDATE artifact_intake_pending SET state='pending',attempts=0,last_error=NULL WHERE event_fingerprint=?", (event_fingerprint,)).rowcount
            if changed != 1: raise KeyError(event_fingerprint)
            row = db.execute("SELECT * FROM artifact_intake_pending WHERE event_fingerprint=?", (event_fingerprint,)).fetchone()
        return {**dict(row), "candidate": json.loads(row["candidate_json"])}
    def delete_pending(self, event_fingerprint: str) -> None:
        with self._db() as db: db.execute("DELETE FROM artifact_intake_pending WHERE event_fingerprint=?", (event_fingerprint,))


class WorkflowUnsupported(RuntimeError):
    pass


class WorkflowCatalog:
    """Runtime-owned workflow templates. The model selects intent; it never emits steps."""
    def __init__(self, representations=None) -> None:
        self.representations = representations
        self._builders = {"knowledge.ingest": self._knowledge_ingest}
    def available(self) -> tuple[str, ...]: return tuple(sorted(self._builders))
    def preflight(self, intent: str, artifact: dict[str, Any], inspection: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        if intent not in self._builders: raise KeyError(intent)
        if intent != "knowledge.ingest": return {"ok": True, "workflow_intent": intent}
        needs = list(dict.fromkeys(decision.get("representation_needs") or ["text"]))
        semantic_needs = [need for need in ("layout", "tables", "visual") if need in needs]
        result: dict[str, Any] = {"ok": True, "workflow_intent": intent, "representation_needs": needs}
        if semantic_needs:
            if self.representations is None:
                raise WorkflowUnsupported("knowledge.ingest requires semantic document interpretation but no representation runtime is configured")
            try:
                result["semantic_interpretation"] = self.representations.preflight_interpretation(artifact, inspection, semantic_needs)
            except Exception as exc:
                raise WorkflowUnsupported(f"knowledge.ingest semantic preflight failed: {exc}") from exc
        return result
    def build(self, intent: str, artifact: dict[str, Any], inspection: dict[str, Any], decision: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        if intent not in self._builders: raise KeyError(intent)
        return self._builders[intent](artifact, inspection, decision)
    def _knowledge_ingest(self, artifact: dict[str, Any], inspection: dict[str, Any], decision: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        fmt = str(inspection.get("format") or "")
        supported = {"text", "markdown", "xml", "json", "csv", "html", "pdf"}
        extra = [row for row in inspection.get("representations", []) if row.get("kind") not in {"text", "table_like_text", "image_reference"}]
        needs = list(dict.fromkeys(decision.get("representation_needs") or ["text"]))
        if fmt not in supported or (fmt == "html" and extra):
            raise WorkflowUnsupported(f"knowledge.ingest has no governed representation pipeline for {fmt or 'unknown'} artifacts")
        facet = next((x for x in artifact["facets"] if x["kind"] == "local_file"), None)
        if facet is None or not facet.get("root_id") or not facet.get("relative_path"):
            raise ValueError("knowledge.ingest requires a governed local representation")
        root_id, relative = facet["root_id"], facet["relative_path"]
        allowed = {"text", "layout", "tables", "visual"}
        unsupported_needs = [need for need in needs if need not in allowed]
        if unsupported_needs:
            raise WorkflowUnsupported(f"knowledge.ingest representation needs are not executable: {', '.join(unsupported_needs)}")

        representation_steps: list[tuple[dict[str, Any], str]] = []
        if "text" in needs:
            representation_steps.append(({
                "capability_id": "files.extract_text",
                "description": "Derive governed searchable text representation",
                "input": {"root_id": root_id, "relative_path": relative},
            }, "/extraction_artifact_id"))

        semantic_needs = [need for need in ("layout", "tables", "visual") if need in needs]
        if semantic_needs:
            if self.representations is None:
                raise WorkflowUnsupported("knowledge.ingest requires semantic document interpretation but no representation runtime is configured")
            ok, reason = self.representations.interpretation_available()
            if not ok:
                raise WorkflowUnsupported(f"knowledge.ingest requires semantic document interpretation but model boundary is unavailable: {reason}")
            representation_steps.append(({
                "capability_id": "representations.interpret",
                "description": "Derive governed semantic document representation through the configured AI model",
                "input": {"artifact_id": artifact["artifact_id"], "needs": semantic_needs},
            }, "/representation_artifact_id"))

        if not representation_steps:
            raise WorkflowUnsupported("knowledge.ingest has no representation work to execute")

        steps: list[dict[str, Any]] = []
        index_ordinals: list[int] = []
        extraction_refs: list[dict[str, Any]] = []
        first_index_ordinal: int | None = None
        for rep_step, output_pointer in representation_steps:
            steps.append(rep_step)
            rep_ordinal = len(steps)
            extraction_ref = {"$ref": {"step": rep_ordinal, "output": output_pointer}}
            extraction_refs.append(extraction_ref)
            index_input: dict[str, Any] = {
                "source_artifact_id": artifact["artifact_id"],
                "extraction_artifact_id": extraction_ref,
            }
            if first_index_ordinal is not None:
                index_input["generation_id"] = {"$ref": {"step": first_index_ordinal, "output": "/generation_id"}}
            steps.append({
                "capability_id": "knowledge.index",
                "description": "Index derived representation into the shared building generation",
                "input": index_input,
            })
            index_ordinal = len(steps)
            index_ordinals.append(index_ordinal)
            if first_index_ordinal is None:
                first_index_ordinal = index_ordinal

        assert first_index_ordinal is not None
        # Verify and activate the generation returned by the LAST index step.
        # Normally all representations share one building generation; on durable
        # resume an older closed generation may be safely rebased to a new build.
        generation_ref = {"$ref": {"step": index_ordinals[-1], "output": "/generation_id"}}
        steps.extend([
            {"capability_id": "knowledge.verify_generation", "description": "Verify complete multi-representation knowledge generation",
             "input": {"generation_id": generation_ref, "required_extraction_artifact_ids": extraction_refs}},
            {"capability_id": "knowledge.activate_generation", "description": "Activate verified knowledge generation",
             "input": {"generation_id": generation_ref}},
        ])
        return f"Ingest {artifact['display_name']} into Knowledge", steps



class DeterministicIntakeWorkflow:
    """Runtime-owned custody chain that must complete before semantic routing.

    These stages are capabilities so they remain visible, governed and testable,
    but the model does not choose whether to run them.
    """

    STAGES = ("artifacts.verify_format", "artifacts.acquire_managed", "artifacts.inspect")

    def __init__(self, capabilities: CapabilityRuntime, artifacts) -> None:
        self.capabilities = capabilities
        self.artifacts = artifacts

    def available(self) -> tuple[str, ...]:
        return self.STAGES

    def run(self, source_artifact: dict[str, Any], *, owner: str) -> dict[str, Any]:
        provenance = InvocationProvenance(owner, "human", "system")
        verified = self.capabilities.invoke(
            "artifacts.verify_format", {"artifact_id": source_artifact["artifact_id"]}, provenance=provenance,
        )
        if verified.status != "succeeded":
            raise RuntimeError(f"format verification {verified.status}: {verified.error or verified.error_code or verified.policy_decision}")

        acquired = self.capabilities.invoke(
            "artifacts.acquire_managed", {"artifact_id": source_artifact["artifact_id"]}, provenance=provenance,
        )
        if acquired.status != "succeeded":
            raise RuntimeError(f"managed intake {acquired.status}: {acquired.error or acquired.error_code or acquired.policy_decision}")

        managed = self.artifacts.get(acquired.result["managed_artifact_id"])
        inspected = self.capabilities.invoke(
            "artifacts.inspect", {"artifact_id": managed["artifact_id"]}, provenance=provenance,
        )
        if inspected.status != "succeeded":
            raise RuntimeError(f"artifact inspection {inspected.status}: {inspected.error or inspected.error_code or inspected.policy_decision}")

        return {
            "artifact": managed,
            "format_verification": verified.result,
            "acquisition": {**acquired.result, "format_verification": verified.result},
            "inspection": inspected.result["inspection"],
            "inspection_occurrence_id": inspected.occurrence_id,
            "stage_occurrence_ids": {
                "artifacts.verify_format": verified.occurrence_id,
                "artifacts.acquire_managed": acquired.occurrence_id,
                "artifacts.inspect": inspected.occurrence_id,
            },
        }


class ArtifactIntakeRuntime:
    """Semantic intake before Work exists. Work is created only for a routed responsibility."""
    def __init__(self, store: ArtifactIntakeStore, artifacts, providers: ProviderRuntime,
                 work, registry: CapabilityRegistry, capabilities: CapabilityRuntime, workflows: WorkflowCatalog | None = None, representations=None, managed_intake=None, intake_workflow: DeterministicIntakeWorkflow | None = None) -> None:
        self.store, self.artifacts, self.providers, self.work, self.registry, self.capabilities = store, artifacts, providers, work, registry, capabilities
        self.managed_intake = managed_intake
        self.intake_workflow = intake_workflow or (DeterministicIntakeWorkflow(capabilities, artifacts) if managed_intake is not None else None)
        self.workflows = workflows or WorkflowCatalog(representations); self._register()
    def _register(self) -> None:
        schema = {"type": "object", "required": ["artifact_id", "source_event_kind"], "properties": {
            "artifact_id": {"type": "string", "minLength": 1},
            "source_event_kind": {"type": "string", "enum": ["new", "changed", "missing", "manual"]},
        }, "additionalProperties": False}
        self.registry.register(CapabilityRegistration(
            CapabilityDefinition("artifacts.classify_intake", "Classify an established artifact and route any resulting responsibility to a runtime-owned workflow.", "classify", "internal", schema, source="artifacts", tags=("artifacts", "intake", "model", "work")),
            lambda p: ScopeResolution("atlas/artifacts/intake", dict(p), f"Classify artifact {p['artifact_id']}"),
            self._execute, metadata={"scope_hint": "atlas/artifacts/intake", "requires_owner_context": True},
        ), replace=True)
        if self.managed_intake is not None:
            file_schema = {"type": "object", "required": ["root_id", "relative_path"], "properties": {
                "root_id": {"type": "string", "minLength": 1},
                "relative_path": {"type": "string", "minLength": 1},
            }, "additionalProperties": False}
            self.registry.register(CapabilityRegistration(
                CapabilityDefinition("artifacts.intake_file", "Establish one governed source file, run deterministic managed intake, inspect it, and route any resulting responsibility.", "intake", "internal", file_schema, source="artifacts", tags=("artifacts", "intake", "sources", "work")),
                self._file_scope, self._file_execute,
                metadata={"scope_hint": "files", "requires_owner_context": True},
            ), replace=True)
    def _file_scope(self, payload: dict[str, Any]) -> ScopeResolution:
        root_id = str(payload.get("root_id") or "").strip()
        relative_path = str(payload.get("relative_path") or "").strip()
        row = self.managed_intake.sources.store.get(root_id)
        from atlas_core.sources import validate_relative_path
        relative_path = validate_relative_path(relative_path)
        clean = {"root_id": row.root_id, "relative_path": relative_path}
        return ScopeResolution(
            f"files/{row.provider_namespace}/{row.root_id}/{relative_path}", clean,
            f"Intake {relative_path}",
        )

    def _file_execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or "")
        payload.pop("__invocation_surface", None)
        try:
            if not owner:
                raise ValueError("owner principal unavailable")
            established = self.managed_intake.sources.establish_file(
                payload["root_id"], payload["relative_path"], principal_id=owner,
            )
            routed = self.capabilities.invoke(
                "artifacts.classify_intake",
                {"artifact_id": established["artifact_id"], "source_event_kind": "manual"},
                provenance=InvocationProvenance(owner, "human", "system"),
            )
            if routed.status != "succeeded":
                raise RuntimeError(f"intake routing {routed.status}: {routed.error or routed.error_code or routed.policy_decision}")
            return ActionResult(True, {"established": established, **routed.result}, {
                "ok": True, "operation": "intake", "artifact_id": established["artifact_id"],
                "work_id": routed.result.get("intake", {}).get("work_id"),
            })
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "intake"},
                                error_code="artifact_file_intake_failed", error=str(exc))

    def _execute(self, payload: dict[str, Any]) -> ActionResult:
        owner = str(payload.pop("__owner_principal_id", "") or ""); payload.pop("__invocation_surface", None)
        try:
            source_artifact = self.artifacts.get(payload["artifact_id"])
            if not owner or source_artifact["principal_id"] != owner: raise KeyError(payload["artifact_id"])
            artifact = source_artifact
            acquisition = None
            intake_stage = None
            if self.intake_workflow is not None and payload["source_event_kind"] != "missing":
                intake_stage = self.intake_workflow.run(source_artifact, owner=owner)
                acquisition = intake_stage["acquisition"]
                artifact = intake_stage["artifact"]
                prior = self.store.latest_reusable(principal_id=owner, artifact_id=artifact["artifact_id"])
                if prior is not None:
                    decision = _decision_from_intake(prior)
                    inspection = json.loads(prior.get("inspection_json") or "{}")
                    intake = self.store.record(
                        artifact_id=artifact["artifact_id"], source_artifact_id=source_artifact["artifact_id"],
                        principal_id=owner, source_event_kind=payload["source_event_kind"], decision=decision,
                        status="reused_existing_content", work_id=prior.get("work_id"),
                        provider=str(prior.get("provider") or "content-reuse"), model=str(prior.get("model") or "none"),
                        inspection_occurrence_id=prior.get("inspection_occurrence_id"), inspection=inspection,
                    )
                    result = {
                        "intake": intake, "inspection": inspection, "classification": decision, "work": None,
                        "source_artifact_id": source_artifact["artifact_id"],
                        "managed_artifact_id": artifact["artifact_id"], "acquisition": acquisition,
                        "intake_pipeline": {"stages": list(self.intake_workflow.available()), "occurrence_ids": intake_stage["stage_occurrence_ids"]},
                        "reused_intake_id": prior["intake_id"], "reused_work_id": prior.get("work_id"),
                    }
                    return ActionResult(True, result, {"ok": True, "operation": "classify",
                        "intake_id": intake["intake_id"], "work_id": prior.get("work_id"), "content_reused": True})
            elif self.managed_intake is not None and payload["source_event_kind"] == "missing":
                links = self.artifacts.managed_for_source(source_artifact["artifact_id"])
                if links:
                    artifact = self.artifacts.get(links[0]["managed_artifact_id"])

            if intake_stage is not None:
                inspection = intake_stage["inspection"]
                inspection_occurrence_id = intake_stage["inspection_occurrence_id"]
            else:
                inspected = self.capabilities.invoke("artifacts.inspect", {"artifact_id": artifact["artifact_id"]},
                    provenance=InvocationProvenance(owner, "human", "system"))
                if inspected.status != "succeeded":
                    raise RuntimeError(f"artifact inspection {inspected.status}: {inspected.error or inspected.error_code or inspected.policy_decision}")
                inspection = inspected.result["inspection"]
                inspection_occurrence_id = inspected.occurrence_id
            decision, provider, model = self._classify(artifact, payload["source_event_kind"], inspection)
            decision = _derive_workflow_class(decision)
            decision, provider, model = self._repair_route_if_needed(
                decision, artifact=artifact, event_kind=payload["source_event_kind"], inspection=inspection,
                provider=provider, model=model,
            )
            work_item = None; status = "no_work"; workflow_preflight = None
            if decision["creates_work"]:
                intent = decision["workflow_intent"]
                if intent in self.workflows.available():
                    try:
                        workflow_preflight = self.workflows.preflight(intent, artifact, inspection, decision)
                        objective, steps = self.workflows.build(intent, artifact, inspection, decision)
                    except WorkflowUnsupported as exc:
                        workflow_preflight = {"ok": False, "workflow_intent": intent, "reason": str(exc)}
                        status = "workflow_unavailable_for_artifact"
                    else:
                        work_item = self.work.create(objective, steps, owner_principal_id=owner,
                            metadata={"artifact_id": artifact["artifact_id"], "source_artifact_id": source_artifact["artifact_id"],
                                      "workflow_intent": intent, "source_event_kind": payload["source_event_kind"],
                                      "inspection_occurrence_id": inspection_occurrence_id, "workflow_preflight": workflow_preflight},
                            artifact_class=decision["artifact_class"], workflow_class=decision["workflow_class"])
                        status = "routed"
                else:
                    workflow_preflight = {"ok": False, "workflow_intent": intent, "reason": "workflow intent is not available"}
                    status = "workflow_unavailable"
            intake = self.store.record(artifact_id=artifact["artifact_id"], source_artifact_id=source_artifact["artifact_id"], principal_id=owner,
                source_event_kind=payload["source_event_kind"], decision=decision, status=status,
                work_id=work_item.work_id if work_item else None, provider=provider, model=model,
                inspection_occurrence_id=inspection_occurrence_id, inspection=inspection)
            result = {"intake": intake, "inspection": inspection, "classification": decision, "work": work_item.as_dict() if work_item else None,
                      "source_artifact_id": source_artifact["artifact_id"], "managed_artifact_id": artifact["artifact_id"], "acquisition": acquisition,
                      "workflow_preflight": workflow_preflight,
                      "intake_pipeline": ({"stages": list(self.intake_workflow.available()), "occurrence_ids": intake_stage["stage_occurrence_ids"]} if intake_stage is not None else None)}
            return ActionResult(True, result, {"ok": True, "operation": "classify", "intake_id": intake["intake_id"], "work_id": intake.get("work_id")})
        except Exception as exc:
            return ActionResult(False, {}, {"ok": False, "operation": "classify"}, error_code="artifact_intake_failed", error=str(exc))

    def sweep(self, root_id: str, principal_id: str, *, max_candidates: int = 25) -> dict[str, Any]:
        """Fan a monitored source diff into independent semantic intakes outside Work.

        Diff may establish more Artifacts than this sweep is allowed to classify.
        Every detected event is therefore durably queued before the cap is applied.
        """
        if max_candidates < 1 or max_candidates > 250:
            raise ValueError("max_candidates must be between 1 and 250")
        provenance = InvocationProvenance(principal_id, "human", "system")
        diff = self.capabilities.invoke("artifacts.diff_source", {"root_id": root_id}, provenance=provenance)
        if diff.status != "succeeded":
            return {"root_id": root_id, "status": "diff_failed", "diff_occurrence_id": diff.occurrence_id,
                    "error": diff.error or diff.error_code or diff.policy_decision, "candidates": 0, "processed": 0,
                    "skipped_idempotent": 0, "skipped_cap": 0, "work_created": 0, "failed": 0,
                    "dead_lettered": 0, "dead_letter_events": []}
        for kind in ("new", "changed", "missing"):
            for candidate in (diff.result.get(kind) or []):
                artifact_id = str(candidate.get("artifact_id") or "")
                if not artifact_id: continue
                fingerprint = _event_fingerprint(kind, candidate)
                if not self.store.has_event(principal_id=principal_id, artifact_id=artifact_id, source_event_kind=kind, event_fingerprint=fingerprint):
                    self.store.enqueue_event(principal_id=principal_id, artifact_id=artifact_id, source_event_kind=kind, event_fingerprint=fingerprint, candidate=candidate)
        pending = list(self.store.pending_events(principal_id))
        selected = pending[:max_candidates]
        summary = {"root_id": root_id, "status": "completed", "diff_occurrence_id": diff.occurrence_id,
                   "candidates": len(pending), "detected_this_sweep": sum(len(diff.result.get(k) or []) for k in ("new","changed","missing")),
                   "processed": 0, "skipped_idempotent": 0, "skipped_cap": max(0, len(pending) - len(selected)),
                   "work_created": 0, "failed": 0, "dead_lettered": 0, "dead_letter_events": [], "results": []}
        for queued in selected:
            kind=queued["source_event_kind"]; artifact_id=queued["artifact_id"]; fingerprint=queued["event_fingerprint"]
            if self.store.has_event(principal_id=principal_id, artifact_id=artifact_id, source_event_kind=kind, event_fingerprint=fingerprint):
                self.store.delete_pending(fingerprint); summary["skipped_idempotent"] += 1
                summary["results"].append({"artifact_id": artifact_id, "kind": kind, "status": "already_classified"}); continue
            occurrence = self.capabilities.invoke("artifacts.classify_intake", {"artifact_id": artifact_id, "source_event_kind": kind}, provenance=provenance)
            if occurrence.status != "succeeded":
                error = occurrence.error or occurrence.error_code or occurrence.policy_decision
                attempted = self.store.record_attempt(fingerprint, error=error, max_attempts=MAX_INTAKE_ATTEMPTS)
                summary["failed"] += 1
                row = {"artifact_id": artifact_id, "kind": kind, "status": occurrence.status,
                       "occurrence_id": occurrence.occurrence_id, "error": error,
                       "attempts": attempted["attempts"], "queue_state": attempted["state"]}
                if attempted["state"] == "dead_letter":
                    summary["dead_lettered"] += 1
                    summary["dead_letter_events"].append({"artifact_id": artifact_id, "kind": kind,
                                                          "event_fingerprint": fingerprint,
                                                          "attempts": attempted["attempts"], "last_error": error})
                summary["results"].append(row)
                continue
            intake = occurrence.result["intake"]
            self.store.set_event_fingerprint(intake["intake_id"], fingerprint); self.store.delete_pending(fingerprint)
            summary["processed"] += 1
            if occurrence.result.get("work") is not None: summary["work_created"] += 1
            summary["results"].append({"artifact_id": artifact_id, "kind": kind, "status": intake["status"],
                                       "intake_id": intake["intake_id"], "work_id": intake.get("work_id"),
                                       "occurrence_id": occurrence.occurrence_id})
        return summary

    def _repair_route_if_needed(self, decision: dict[str, Any], *, artifact: dict[str, Any], event_kind: str,
                                inspection: dict[str, Any], provider: str, model: str) -> tuple[dict[str, Any], str, str]:
        try:
            _validate_route(decision)
            return decision, provider, model
        except ValueError as route_error:
            context = {
                "artifact": artifact, "inspection": inspection, "source_event_kind": event_kind,
                "artifact_classes": ARTIFACT_CLASSES, "workflow_classes": WORKFLOW_CLASSES,
                "available_workflows": list(self.workflows.available()),
                "representation_needs": list(SEMANTIC_REPRESENTATION_NEEDS),
                "previous_decision": decision, "route_validation_error": str(route_error),
            }
            system = (
                "You are Atlas repairing an internally inconsistent artifact-routing decision. Preserve the semantic facts and "
                "representation needs from the previous decision. Repair only creates_work and workflow_intent. workflow_class is "
                "runtime-owned bookkeeping: return it as null and Atlas will derive it deterministically from workflow_intent. "
                "If creates_work is true, workflow_intent must be one of the supplied workflow intents. If creates_work is false, "
                "workflow_intent must be null. Return exactly one JSON object satisfying the supplied classification schema; do not invent evidence."
            )
            response_format = {"type": "json_schema", "json_schema": {"name": "artifact_intake_route_repair", "strict": True, "schema": CLASSIFICATION_SCHEMA}}
            repaired = self.providers.generate(ModelRequest(
                capability_id="artifact.intake.route_repair", system=system,
                input=json.dumps(context, ensure_ascii=False, default=str),
                metadata={"response_format": response_format}, max_output_chars=6000,
            ))
            value = _classification_object(repaired.text)
            validate_json(value, CLASSIFICATION_SCHEMA, path="$.classification")
            value = _derive_workflow_class(value)
            _validate_route(value)
            return value, repaired.provider_key, repaired.model

    def _classify(self, artifact: dict[str, Any], event_kind: str, inspection: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        context = {"artifact": artifact, "inspection": inspection, "source_event_kind": event_kind,
                   "artifact_classes": ARTIFACT_CLASSES, "workflow_classes": WORKFLOW_CLASSES,
                   "available_workflows": list(self.workflows.available()), "representation_needs": list(SEMANTIC_REPRESENTATION_NEEDS)}
        required_keys = list(CLASSIFICATION_SCHEMA["required"]) + ["representation_needs"]
        system = ("You are Atlas classifying an already-established artifact before Work exists. Decide what responsibility, if any, the artifact creates. "
                  "Classify semantic purpose, not file modality. The inspection may describe multiple observed representations and unresolved modalities; do not collapse a compound artifact into one modality or invent content that inspection did not establish. "
                  "State whether inspection is sufficient and list unresolved semantic questions. Select semantic representation_needs only when required (text, layout, tables, visual); do not name providers or processing implementations. "
                  "Use text when a durable searchable textual representation is needed. Never request OCR: native extraction versus OCR is a runtime implementation decision. "
                  "If Work is needed, select a workflow intent from the supplied workflow classes; runtime owns the workflow mechanics. "
                  "workflow_class is runtime-owned bookkeeping: always return workflow_class as null; Atlas derives the class deterministically from workflow_intent. "
                  "Use only the supplied enum values exactly as written. In particular knowledge_disposition is one of ingest, retain, no, maybe; "
                  "relationship is one of new, possible_revision, duplicate, supplement, related, unknown; inspection_sufficiency is one of sufficient, partial, insufficient. "
                  "Return exactly one JSON object with these top-level keys and no wrapper object: " + ", ".join(required_keys) + ".")
        request_input = json.dumps(context, ensure_ascii=False, default=str)
        response_format = {"type": "json_schema", "json_schema": {"name": "artifact_intake_classification", "strict": True, "schema": CLASSIFICATION_SCHEMA}}
        response = self.providers.generate(ModelRequest(capability_id="artifact.intake", system=system,
            input=request_input, metadata={"response_format": response_format}, max_output_chars=6000))
        try:
            decision = _classification_object(response.text)
            validate_json(decision, CLASSIFICATION_SCHEMA, path="$.classification")
            return _derive_workflow_class(decision), response.provider_key, response.model
        except (SchemaValidationError, ValueError, json.JSONDecodeError) as first_error:
            repair_system = (system + " Your previous response did not satisfy the runtime contract. "
                             "Repair only the JSON structure/fields; do not change facts or invent evidence. "
                             f"Validation error: {first_error}. Previous response: {response.text[:2000]}")
            repaired = self.providers.generate(ModelRequest(capability_id="artifact.intake", system=repair_system,
                input=request_input, metadata={"response_format": response_format}, max_output_chars=6000))
            decision = _classification_object(repaired.text)
            validate_json(decision, CLASSIFICATION_SCHEMA, path="$.classification")
            return _derive_workflow_class(decision), repaired.provider_key, repaired.model


def _classification_object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("classification output must be a JSON object")
    # Some JSON-mode models add a harmless structural wrapper despite being told
    # to return the classification object directly. Unwrap only that one shape;
    # never infer or default semantic fields.
    if set(value) == {"classification"} and isinstance(value["classification"], dict):
        value = value["classification"]
    value.setdefault("representation_needs", [])
    return value


def _decision_from_intake(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_class": row["artifact_class"], "purpose": row["purpose"],
        "knowledge_disposition": row["knowledge_disposition"], "relationship": row["relationship"],
        "creates_work": bool(row.get("workflow_intent")), "workflow_class": row.get("workflow_class"),
        "workflow_intent": row.get("workflow_intent"), "confidence": float(row["confidence"]),
        "inspection_sufficiency": "sufficient", "unresolved_questions": [],
        "representation_needs": ["text" if need == "ocr" else need for need in json.loads(row.get("representation_needs_json") or "[]")],
        "reason": row["reason"],
    }


def _derive_workflow_class(decision: dict[str, Any]) -> dict[str, Any]:
    """Derive the internal route code from the model-selected semantic workflow intent."""
    value = dict(decision)
    if not bool(value.get("creates_work")):
        value["workflow_class"] = None
        return value
    expected = WORKFLOW_CLASS_BY_INTENT.get(value.get("workflow_intent"))
    if expected is not None:
        value["workflow_class"] = expected
    return value


def _validate_route(decision: dict[str, Any]) -> None:
    creates = bool(decision["creates_work"]); code = decision.get("workflow_class"); intent = decision.get("workflow_intent")
    if not creates:
        if code is not None or intent is not None: raise ValueError("no-work classification must not select a workflow")
        return
    expected = WORKFLOW_CLASS_BY_INTENT.get(intent)
    if expected is None: raise ValueError("work classification requires a known workflow intent")
    if code != expected: raise ValueError("workflow class must be derived from workflow intent")


def _event_fingerprint(kind: str, candidate: dict[str, Any]) -> str:
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    stable = {"kind": kind, "artifact_id": candidate.get("artifact_id"), "root_id": candidate.get("root_id"),
              "relative_path": candidate.get("relative_path"), "byte_size": candidate.get("byte_size"),
              "previous_state": candidate.get("previous_state"),
              "metadata": {key: metadata.get(key) for key in ("size", "mtime_ns", "ctime_ns", "inode")}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
