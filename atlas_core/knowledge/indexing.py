from __future__ import annotations

from typing import Any

from atlas_core.actions import ActionResult

from .generations import FTS_MECHANISM, GenerationStore, seed_default_configs
from .passages import PassageStore, SEGMENTER_HEADINGS_V1, content_hash, segment

DERIVED_MARKER = ".atlas-derived/"


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
        return self.generations.building() or self.generations.candidate() or self.generations.active()

    def ensure_generation(self, occurrence_hint: str = "knowledge.index") -> dict[str, Any]:
        existing = self.generations.building()
        if existing is not None:
            return existing
        active = self.generations.active()
        generation_id = self.generations.create(
            extractor_config_id=active["extractor_config_id"] if active else "extractor:text@1",
            segmenter_config_id=active["segmenter_config_id"] if active else SEGMENTER_HEADINGS_V1,
            mechanisms=list(active["mechanisms"]) if active else [FTS_MECHANISM],
            occurrence_id=occurrence_hint, corpus=dict(active.get("corpus") or {}) if active else {},
        )
        if active is not None:
            with self.passages._db() as db:
                db.execute(
                    """INSERT INTO generation_passages(generation_id,passage_id,state)
                       SELECT ?,passage_id,state FROM generation_passages WHERE generation_id=?""",
                    (generation_id, active["generation_id"]),
                )
        return self.generations.get(generation_id)

    def verify(self, generation_id: str, *, required_extraction_artifact_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        """Deterministic verification receipt. A generation that fails is never servable."""
        generation = self.generations.get(generation_id)
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

    def activate(self, generation_id: str) -> dict[str, Any]:
        generation = self.generations.get(generation_id)
        if generation["state"] != "candidate":
            raise ValueError("only a verified candidate generation can be activated")
        previous = self.generations.active()
        if previous is not None:
            self.generations.set_state(previous["generation_id"], "retired")
        return self.generations.set_state(generation_id, "active")

    # ---- indexing

    def index(self, *, source_artifact_id: str, extraction_artifact_id: str,
              generation_id: str | None = None, occurrence_hint: str = "knowledge.index") -> dict[str, Any]:
        artifact = self.artifacts.get(extraction_artifact_id)
        facet = next((row for row in artifact["facets"]
                      if row["kind"] == "local_file" and (row["relative_path"] or "").startswith(DERIVED_MARKER)), None)
        if facet is None:
            raise PermissionError("extraction artifact has no managed derived-area representation")
        generation = self.generations.get(generation_id) if generation_id else self.ensure_generation(occurrence_hint)
        if generation["state"] != "building":
            raise ValueError("only a building generation accepts passages")

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
        out = runtime.index(
            source_artifact_id=payload["source_artifact_id"],
            extraction_artifact_id=payload["extraction_artifact_id"],
            generation_id=payload.get("generation_id"),
        )
    except PermissionError as exc:
        return ActionResult(False, {}, {"ok": False, "operation": "index"},
                            error_code="knowledge_index_source_invalid", error=str(exc))
    except KeyError as exc:
        return ActionResult(False, {}, {"ok": False, "operation": "index"},
                            error_code="artifact_unknown", error=f"unknown artifact: {exc}")
    return ActionResult(True, out, {"ok": True, "operation": "index", **out})
