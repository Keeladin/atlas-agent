from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_core.bootstrap import build_runtime
from atlas_core.knowledge import KnowledgeStore, chunk_text, source_content_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"


class KnowledgeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _runtime(self):
        return build_runtime(db_path=self.db, include_morning=False)

    def _ingest_via_path(
        self,
        runtime,
        source: Path,
        *,
        title: str | None = None,
        content_sha256: str | None = None,
        source_path: str | None = None,
        include_hash: bool = True,
    ):
        if source_path is None:
            source_path = str(source.resolve()) if source.exists() else str(source)
        payload = {
            "title": title or source.name,
            "source_path": source_path,
            "source_uri": source_path,
            "chunk_chars": 4000,
            "overlap_chars": 400,
        }
        if include_hash:
            if content_sha256 is not None:
                payload["content_sha256"] = content_sha256
            elif source.is_file():
                payload["content_sha256"] = source_content_sha256(
                    source.read_text(encoding="utf-8")
                )
                payload["byte_size"] = source.stat().st_size
        task = runtime.store.create_task(
            objective=f"Index local knowledge source {payload['title']}",
            success_criteria=("The source is durably indexed with chunk provenance.",),
            authority_scope="modify_internal",
        )
        request = runtime.store.put_artifact(
            task.id,
            kind="knowledge_ingest_request",
            payload=payload,
        )
        runtime.store.add_step(
            task.id,
            description="Chunk and index extracted text.",
            capability="knowledge.ingest_text",
            capability_version=runtime.capabilities.get("knowledge.ingest_text").spec.version,
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        return task, request, result

    def _assert_control_context(self, runtime, task_id: str, *, max_source_tokens: int) -> None:
        manifests = runtime.store.list_context_manifests(task_id)
        self.assertTrue(manifests)
        ingest_manifests = [
            item for item in manifests if item.capability == "knowledge.ingest_text"
        ]
        self.assertTrue(ingest_manifests)
        manifest = ingest_manifests[0]
        self.assertLessEqual(manifest.total_tokens, manifest.budget_tokens)
        self.assertEqual(manifest.budget_tokens, 4000)
        self.assertLess(
            manifest.total_tokens,
            max_source_tokens,
            "ingest context pack should not carry the source document",
        )
        request = next(
            artifact
            for artifact in runtime.store.list_artifacts(task_id)
            if artifact.kind == "knowledge_ingest_request"
        )
        self.assertNotIn("text", request.payload)
        self.assertIn("source_path", request.payload)

    def test_chunking_is_deterministic_and_overlapping(self):
        text = "\n\n".join(f"section {i} " + ("pump failure " * 50) for i in range(20))
        first = chunk_text(text, chunk_chars=800, overlap_chars=100)
        second = chunk_text(text, chunk_chars=800, overlap_chars=100)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 2)
        self.assertTrue(all(chunk.strip() for chunk in first))

    def test_knowledge_ingest_is_content_idempotent_and_searchable(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        text = "RLH5 hydraulic pump failure. Replace the suction hose and inspect cavitation.\n\nBrake test procedure is separate."
        first = store.ingest_text(title="RLH5 manual", text=text, source_uri="manual://rlh5")
        second = store.ingest_text(title="duplicate title", text=text, source_uri="other://copy")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.document.id, second.document.id)
        hits = store.search("hydraulic cavitation", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.document_id, first.document.id)
        self.assertIn("cavitation", hits[0].chunk.text)

    def test_ingest_and_retrieve_execute_through_task_runtime(self):
        runtime = build_runtime(db_path=self.db, include_morning=False)
        task = runtime.store.create_task(
            objective="Index and retrieve a technical note",
            success_criteria=("Relevant technical evidence is retrievable",),
            authority_scope="modify_internal",
        )
        ingest_input = runtime.store.put_artifact(
            task.id,
            kind="knowledge_ingest_request",
            payload={
                "title": "Pump note",
                "source_uri": "note://pump",
                "text": "A cavitating hydraulic pump may show noise and unstable pressure. Inspect suction restrictions.",
                "chunk_chars": 512,
                "overlap_chars": 64,
            },
        )
        ingest = runtime.store.add_step(
            task.id,
            description="Index note",
            capability="knowledge.ingest_text",
            input_artifact_ids=(ingest_input.id,),
        )
        search_input = runtime.store.put_artifact(
            task.id,
            kind="knowledge_search_request",
            payload={"query": "cavitating hydraulic pump", "limit": 5},
        )
        runtime.store.add_step(
            task.id,
            description="Retrieve evidence",
            capability="knowledge.search",
            dependencies=(ingest.id,),
            input_artifact_ids=(search_input.id,),
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        outputs = [a for a in runtime.store.list_artifacts(task.id) if a.kind == "knowledge_search_results"]
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs[0].payload["results"])
        claims = runtime.store.list_claims(task.id)
        self.assertTrue(any(c.kind == "retrieved" for c in claims))

    def test_path_ingest_indexes_readme_without_exceeding_context_budget(self):
        runtime = self._runtime()
        source_tokens = max(1, (len(README_PATH.read_text(encoding="utf-8")) + 3) // 4)
        task, request, result = self._ingest_via_path(runtime, README_PATH)
        self.assertEqual(result.status, "completed")
        self._assert_control_context(runtime, task.id, max_source_tokens=source_tokens)
        outputs = [
            artifact
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_ingest_result"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertGreaterEqual(outputs[0].payload["chunk_count"], 1)
        self.assertEqual(
            outputs[0].payload["content_sha256"],
            source_content_sha256(README_PATH.read_text(encoding="utf-8")),
        )
        search_task = runtime.store.create_task(
            objective="Search README evidence",
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        search_input = runtime.store.put_artifact(
            search_task.id,
            kind="knowledge_search_request",
            payload={"query": "ContextBuilder", "limit": 5},
        )
        runtime.store.add_step(
            search_task.id,
            description="Retrieve evidence",
            capability="knowledge.search",
            input_artifact_ids=(search_input.id,),
            metadata={"accept_all_criteria": True},
        )
        search_result = runtime.run_until_blocked(search_task.id)
        self.assertEqual(search_result.status, "completed")
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(search_task.id)
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits[0].payload["results"])
        self.assertIn("ContextBuilder", hits[0].payload["results"][0]["text"])
        self.assertEqual(request.payload["source_path"], str(README_PATH.resolve()))

    def test_path_ingest_indexes_large_synthetic_file_within_context_budget(self):
        planted = "hydraulic-cavitation-probe-unique"
        line = f"atlas knowledge ingest regression line with planted phrase {planted}\n"
        source = Path(self.tmp.name) / "large-source.txt"
        source.write_text(line * 8000, encoding="utf-8")
        self.assertGreater(source.stat().st_size, 400_000)
        runtime = self._runtime()
        source_tokens = max(1, (source.stat().st_size + 3) // 4)
        task, _, result = self._ingest_via_path(runtime, source)
        self.assertEqual(result.status, "completed")
        self._assert_control_context(runtime, task.id, max_source_tokens=source_tokens)
        outputs = [
            artifact
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_ingest_result"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertGreater(outputs[0].payload["chunk_count"], 10)
        search_task = runtime.store.create_task(
            objective="Search large source",
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        search_input = runtime.store.put_artifact(
            search_task.id,
            kind="knowledge_search_request",
            payload={"query": planted, "limit": 5},
        )
        runtime.store.add_step(
            search_task.id,
            description="Retrieve evidence",
            capability="knowledge.search",
            input_artifact_ids=(search_input.id,),
            metadata={"accept_all_criteria": True},
        )
        self.assertEqual(runtime.run_until_blocked(search_task.id).status, "completed")
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(search_task.id)
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits[0].payload["results"])
        self.assertIn(planted, hits[0].payload["results"][0]["text"])

    def test_path_ingest_hash_mismatch_fails_closed(self):
        source = Path(self.tmp.name) / "hashed.txt"
        source.write_text("original source bytes for hash check\n", encoding="utf-8")
        runtime = self._runtime()
        task, _, result = self._ingest_via_path(
            runtime,
            source,
            content_sha256="0" * 64,
        )
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertIn("hash mismatch", execution.error or "")
        self.assertFalse(
            any(
                artifact.kind == "knowledge_ingest_result"
                for artifact in runtime.store.list_artifacts(task.id)
            )
        )

    def test_path_ingest_missing_file_fails_closed(self):
        missing = Path(self.tmp.name) / "does-not-exist.txt"
        runtime = self._runtime()
        task, _, result = self._ingest_via_path(
            runtime,
            missing,
            include_hash=False,
        )
        self.assertEqual(result.status, "failed")
        execution = runtime.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "fail")
        self.assertIn("missing", execution.error or "")
        self.assertFalse(
            any(
                artifact.kind == "knowledge_ingest_result"
                for artifact in runtime.store.list_artifacts(task.id)
            )
        )

    def test_path_ingest_is_idempotent_for_the_same_source_bytes(self):
        source = Path(self.tmp.name) / "repeat.txt"
        source.write_text("RLH5 suction hose inspection. Repeatable ingest.\n", encoding="utf-8")
        runtime = self._runtime()
        first_task, _, first_result = self._ingest_via_path(runtime, source)
        second_task, _, second_result = self._ingest_via_path(runtime, source)
        self.assertEqual(first_result.status, "completed")
        self.assertEqual(second_result.status, "completed")
        first = next(
            artifact
            for artifact in runtime.store.list_artifacts(first_task.id)
            if artifact.kind == "knowledge_ingest_result"
        )
        second = next(
            artifact
            for artifact in runtime.store.list_artifacts(second_task.id)
            if artifact.kind == "knowledge_ingest_result"
        )
        self.assertTrue(first.payload["created"])
        self.assertFalse(second.payload["created"])
        self.assertEqual(first.payload["document_id"], second.payload["document_id"])
        self.assertEqual(first.payload["content_sha256"], second.payload["content_sha256"])


if __name__ == "__main__":
    unittest.main()
