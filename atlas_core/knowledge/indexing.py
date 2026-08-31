from __future__ import annotations

import sqlite3
from typing import Any

from atlas_core.actions import ActionResult

from .generations import FTS_MECHANISM, GenerationStore, seed_default_configs
from .passages import PassageStore, SEGMENTER_HEADINGS_V1, content_hash, segment

DERIVED_MARKER = ".atlas-derived/"


class KnowledgeGenerationBusy(RuntimeError):
    """Another artifact intake owns the only mutable knowledge generation."""


class IndexingRuntime:
    """Deterministic segmentation and persistence of the derived knowledge tier.

    Index reads ride the extraction grant: this runtime only ever opens an
    extraction that already lives in a root's managed derived area, so source
    authority is exercised once, at the files gate, by files.extract_text.
    """

    def __init__(self, passages: PassageStore, generations: GenerationStore, artifacts, sources) -> None:
        if passages.path.resolve() != generations.path.resolve():
            raise ValueError("PassageStore and GenerationStore must use the same SQLite database")
        self.passages = passages
        self.generations = generations
        self.artifacts = artifacts
        self.sources = sources
        seed_default_configs(self.generations)

    # ---- generation helpers

    def current_generation(self) -> dict[str, Any] | None:
        return self.generations.pending() or self.generations.active()

    def _busy(self, pending: dict[str, Any], work_id: str | None = None) -> KnowledgeGenerationBusy:
        owner = pending.get("build_owner_work_id")
        detail = f" ({owner})" if owner else ""
        return KnowledgeGenerationBusy(
            f"another knowledge ingest owns the open generation{detail}; finish, cancel, or resume it before continuing"
        )

    def _contains_source(self, generation_id: str, source_artifact_id: str) -> bool:
        with self.passages._db() as db:
            row = db.execute(
                """SELECT 1 FROM generation_passages g
                   JOIN passages p ON p.passage_id=g.passage_id
                   WHERE g.generation_id=? AND p.source_artifact_id=? LIMIT 1""",
                (generation_id, source_artifact_id),
            ).fetchone()
        return row is not None

    def ensure_generation(self, source_artifact_id: str, occurrence_hint: str = "knowledge.index",
                          owner_work_id: str | None = None) -> dict[str, Any]:
        pending = self.generations.pending()
        if pending is not None:
            if pending["state"] == "building":
                owner = pending.get("build_owner_work_id")
                if owner_work_id is None and owner is None:
                    # Direct/internal indexing keeps the historical shared-build behaviour.
                    return pending
                if owner_work_id is not None:
                    if owner is None:
                        pending = self.generations.claim_build_owner(pending["generation_id"], owner_work_id)
                        owner = owner_work_id
                    if owner == owner_work_id:
                        return pending
            raise self._busy(pending, owner_work_id)
        active = self.generations.active()
        try:
            generation_id = self.generations.create(
                extractor_config_id=active["extractor_config_id"] if active else "extractor:text@1",
                segmenter_config_id=active["segmenter_config_id"] if active else SEGMENTER_HEADINGS_V1,
                mechanisms=list(active["mechanisms"]) if active else [FTS_MECHANISM],
                occurrence_id=occurrence_hint, corpus=dict(active.get("corpus") or {}) if active else {},
                build_owner_work_id=owner_work_id,
            )
        except sqlite3.IntegrityError:
            pending = self.generations.pending()
            if pending is not None and pending["state"] == "building":
                owner = pending.get("build_owner_work_id")
                if owner_work_id is None and owner is None:
                    return pending
                if owner_work_id is not None and owner == owner_work_id:
                    return pending
            if pending is not None:
                raise self._busy(pending, owner_work_id)
            raise
        if active is not None:
            with self.passages._db() as db:
                db.execute(
                    """INSERT INTO generation_passages(generation_id,passage_id,state)
                       SELECT ?,passage_id,state FROM generation_passages WHERE generation_id=?""",
                    (generation_id, active["generation_id"]),
                )
        return self.generations.get(generation_id)

    def abandon_for_work(self, work_id: str) -> dict[str, Any] | None:
        pending = self.generations.pending()
        if pending is None or pending.get("build_owner_work_id") != work_id:
            return None
        return self.generations.set_state(
            pending["generation_id"], "failed",
            verification={"ok": False, "abandoned": True, "work_id": work_id},
        )

    def verify(self, generation_id: str, *, required_extraction_artifact_ids: list[str] | tuple[str, ...] | None = None,
               owner_work_id: str | None = None) -> dict[str, Any]:
        """Deterministic verification receipt. A generation that fails is never servable."""
        generation = self.generations.get(generation_id)
        owner = generation.get("build_owner_work_id")
        if owner is not None and owner != owner_work_id:
            raise self._busy(generation, owner_work_id)
        if generation["state"] not in {"building", "verifying", "candidate"}:
            raise ValueError("only a building or verifying generation can be verified")
        self.generations.set_state(generation_id, "verifying")
        with self.passages._db() as db:
            passage_count = int(db.execute(
                "SELECT COUNT(*) FROM generation_passages WHERE generation_id=? AND state='current'",
                (generation_id,),
            ).fetchone()[0])
            source_count = int(db.execute(
                """SELECT COUNT(DISTINCT p.source_artifact_id) FROM generation_passages g
                   JOIN passages p ON p.passage_id=g.passage_id WHERE g.generation_id=?""",
                (generation_id,),
            ).fetchone()[0])
            orphans = int(db.execute(
                """SELECT COUNT(*) FROM generation_passages g
                   LEFT JOIN passages p ON p.passage_id=g.passage_id
                   WHERE g.generation_id=? AND p.passage_id IS NULL""",
                (generation_id,),
            ).fetchone()[0])
        required = tuple(dict.fromkeys(str(item) for item in (required_extraction_artifact_ids or ()) if str(item)))
        missing_required: list[str] = []
        if required:
            with self.passages._db() as db:
                for extraction_artifact_id in required:
                    count = int(db.execute(
                        """SELECT COUNT(*) FROM generation_passages g
                           JOIN passages p ON p.passage_id=g.passage_id
                           WHERE g.generation_id=? AND g.state='current' AND p.extraction_artifact_id=?""",
                        (generation_id, extraction_artifact_id),
                    ).fetchone()[0])
                    if count == 0:
                        missing_required.append(extraction_artifact_id)
        receipt = {
            "passages": passage_count, "sources": source_count, "orphan_memberships": orphans,
            "required_extractions": {"checked": len(required), "missing": missing_required},
            "canaries": {"checked": 0, "passed": 0},
        }
        ok = passage_count > 0 and orphans == 0 and not missing_required
        receipt["ok"] = ok
        self.generations.set_state(generation_id, "candidate" if ok else "failed", verification=receipt)
        return receipt

    def activate(self, generation_id: str, *, owner_work_id: str | None = None) -> dict[str, Any]:
        generation = self.generations.get(generation_id)
        owner = generation.get("build_owner_work_id")
        if owner is not None and owner != owner_work_id:
            raise self._busy(generation, owner_work_id)
        if generation["state"] != "candidate":
            raise ValueError("only a verified candidate generation can be activated")
        previous = self.generations.active()
        if previous is not None:
            self.generations.set_state(previous["generation_id"], "retired")
        return self.generations.set_state(generation_id, "active")

    # ---- indexing

    def index(self, *, source_artifact_id: str, extraction_artifact_id: str,
              generation_id: str | None = None, occurrence_hint: str = "knowledge.index",
              owner_work_id: str | None = None) -> dict[str, Any]:
        artifact = self.artifacts.get(extraction_artifact_id)
        facet = next((row for row in artifact["facets"]
                      if row["kind"] == "local_file" and (row["relative_path"] or "").startswith(DERIVED_MARKER)), None)
        if facet is None:
            raise PermissionError("extraction artifact has no managed derived-area representation")
        rebased_from: str | None = None
        if generation_id:
            generation = self.generations.get(generation_id)
            if generation["state"] != "building":
                # Durable resume compatibility: an older workflow may point at a generation
                # that another intake activated while this work was paused. Rebase only if
                # this source was already part of that generation; arbitrary writes to a
                # closed generation remain forbidden.
                if generation["state"] in {"active", "retired", "failed"} and self._contains_source(generation_id, source_artifact_id):
                    rebased_from = generation_id
                    generation = self.ensure_generation(source_artifact_id, occurrence_hint, owner_work_id)
                else:
                    raise ValueError("only a building generation accepts passages")
            else:
                owner = generation.get("build_owner_work_id")
                if owner_work_id is None:
                    if owner is not None:
                        raise self._busy(generation)
                else:
                    if owner is None:
                        generation = self.generations.claim_build_owner(generation["generation_id"], owner_work_id)
                        owner = owner_work_id
                    if owner != owner_work_id:
                        raise self._busy(generation, owner_work_id)
        else:
            generation = self.ensure_generation(source_artifact_id, occurrence_hint, owner_work_id)

        root = self.sources.store.get(facet["root_id"])
        read = self.sources.kernel.read(root.provider_namespace, root.root_id, facet["relative_path"])
        rows = segment(read.text, self.generations.config(generation["segmenter_config_id"])["spec"])

        created = shared = 0
        passage_ids: list[str] = []
        with self.passages._db() as db:
            for row in rows:
                digest, is_new = self.passages.upsert_content(row["content"], db=db)
                if is_new:
                    created += 1
                else:
                    shared += 1
                passage_id, _added = self.passages.add_passage(
                    source_artifact_id=source_artifact_id, extraction_artifact_id=extraction_artifact_id,
                    segmenter_config_id=generation["segmenter_config_id"], locator=row["locator"],
                    content_hash_value=digest, occurrence_id=occurrence_hint, db=db,
                )
                self.passages.link(generation["generation_id"], passage_id, db=db)
                passage_ids.append(passage_id)
        return {
            "generation_id": generation["generation_id"],
            **({"rebased_from_generation_id": rebased_from} if rebased_from else {}),
            "source_artifact_id": source_artifact_id,
            "extraction_artifact_id": extraction_artifact_id,
            "passages": len(passage_ids), "new_contents": created, "shared_contents": shared,
            "coverage_chars": sum(len(row["content"]) for row in rows),
        }

    # ---- retrieval (derived tier)

    def retrieve(self, need: str, *, limit: int, state: str = "current",
                 artifact_id: str | None = None, generation: str | None = None) -> list[dict[str, Any]]:
        if generation in {None, "active"}:
            target = self.generations.active()
        elif generation == "candidate":
            target = self.generations.candidate()
        else:
            try:
                target = self.generations.get(generation)
            except KeyError:
                return []
        if target is None:
            return []
        rows = self.passages.search(target["generation_id"], need, limit=limit, state=state, artifact_id=artifact_id)
        return [{
            "content": row["content"],
            "score": row["score"],
            "mechanism": "fts.bm25@passages",
            "grounding": {
                "tier": "derived", "passage_id": row["passage_id"],
                "generation_id": target["generation_id"],
                "artifact_id": row["source_artifact_id"],
                "extraction_artifact_id": row["extraction_artifact_id"],
                "locator": row["locator"], "content_hash": row["content_hash"],
                "revision_state": state, "derivation_occurrence_id": row["occurrence_id"],
            },
        } for row in rows]


def index_result(payload: dict[str, Any], runtime: IndexingRuntime) -> ActionResult:
    try:
        owner_work_id = payload.pop("__work_id", None)
        payload.pop("__step_id", None)
        out = runtime.index(
            source_artifact_id=payload["source_artifact_id"],
            extraction_artifact_id=payload["extraction_artifact_id"],
            generation_id=payload.get("generation_id"),
            owner_work_id=owner_work_id,
        )
    except KnowledgeGenerationBusy as exc:
        return ActionResult(False, {}, {"ok": False, "operation": "index", "retryable": True},
                            error_code="knowledge_generation_busy", error=str(exc))
    except PermissionError as exc:
        return ActionResult(False, {}, {"ok": False, "operation": "index"},
                            error_code="knowledge_index_source_invalid", error=str(exc))
    except KeyError as exc:
        return ActionResult(False, {}, {"ok": False, "operation": "index"},
                            error_code="artifact_unknown", error=f"unknown artifact: {exc}")
    return ActionResult(True, out, {"ok": True, "operation": "index", **out})
