from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atlas_core.bootstrap import build_runtime
from atlas_core.capabilities import CapabilitySpec
from atlas_core.context import ContextBuilder
from atlas_core.knowledge import (
    MAX_SEARCH_RESULT_CHARS,
    KnowledgeStore,
    chunk_text,
    grounded_answer_from_hits,
    is_knowledge_question,
    source_content_sha256,
)
from atlas_core.knowledge.capabilities import _search_verifier
from atlas_core.presentation import TaskPresenter


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
        listed = store.list_documents()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].id, first.document.id)

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
        presentation = TaskPresenter(runtime.store).build(search_task.id)
        markdown = presentation.render_markdown()
        self.assertIn("## Retrieved sources", markdown)
        self.assertNotIn('"results":', markdown)
        self.assertTrue(presentation.outputs[0]["hits"])
        self.assertTrue(is_knowledge_question("What is the purpose of Atlas's durable task runtime?"))
        self.assertFalse(is_knowledge_question("ContextBuilder"))
        answer = grounded_answer_from_hits(
            "What is the purpose of Atlas's durable task runtime?",
            [
                {"title": "README.md", "text": "├── tests/ runtime + behavioural regressions", "sha256": "aa"},
                {
                    "title": "Atlas Constitution.md",
                    "text": "Atlas is a local-first persistent operational agent whose purpose is to remove recurring friction by owning useful work over time.",
                    "sha256": "bb" * 32,
                },
            ],
        )
        self.assertIn("purpose is to remove recurring friction", answer)
        self.assertLess(answer.index("purpose is to remove recurring friction"), answer.find("behavioural regressions") % 10**9)
        answer_task = runtime.store.create_task(
            objective="Search Atlas knowledge for: What is ContextBuilder?",
            success_criteria=(
                "A source-grounded local knowledge search result is produced.",
                "An evidence-grounded answer cites retrieved sources.",
            ),
            authority_scope="read",
        )
        search_request = runtime.store.put_artifact(
            answer_task.id,
            kind="knowledge_search_request",
            payload={"query": "What is ContextBuilder?", "limit": 5},
        )
        search_step = runtime.store.add_step(
            answer_task.id,
            description="Retrieve matching knowledge chunks.",
            capability="knowledge.search",
            input_artifact_ids=(search_request.id,),
            metadata={"satisfies_criteria": [1]},
        )
        runtime.store.add_step(
            answer_task.id,
            description="Compose a source-grounded answer from retrieved chunks.",
            capability="knowledge.answer",
            dependencies=(search_step.id,),
            metadata={"satisfies_criteria": [2]},
        )
        self.assertEqual(runtime.run_until_blocked(answer_task.id).status, "completed")
        answer_md = TaskPresenter(runtime.store).build(answer_task.id).render_markdown()
        self.assertIn("## Grounded answer", answer_md)
        self.assertIn("From retrieved sources", answer_md)
        self.assertNotIn('"results":', answer_md)

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

    def test_extra_invocation_fields_do_not_fail_search_schema(self):
        runtime = self._runtime()
        self._ingest_via_path(runtime, README_PATH)
        task = runtime.store.create_task(
            objective="Search Atlas knowledge for: ContextBuilder",
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        request = runtime.store.put_artifact(
            task.id,
            kind="knowledge_search_request",
            payload={"query": "ContextBuilder", "limit": 5, "authority_scope": "read"},
        )
        runtime.store.add_step(
            task.id,
            description="Retrieve matching knowledge chunks.",
            capability="knowledge.search",
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        self.assertEqual(runtime.run_until_blocked(task.id).status, "completed")

    def test_planned_search_step_synthesizes_query_instead_of_failing_schema(self):
        from atlas_core.knowledge import parse_search_objective

        self.assertEqual(
            parse_search_objective("Search Atlas knowledge for: ContextBuilder"),
            "ContextBuilder",
        )
        runtime = self._runtime()
        self._ingest_via_path(runtime, README_PATH)
        task = runtime.store.create_task(
            objective="Search Atlas knowledge for: ContextBuilder",
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        runtime.store.add_step(
            task.id,
            description="Retrieve matching knowledge chunks.",
            capability="knowledge.search",
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        request = next(
            artifact
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_search_request"
        )
        self.assertEqual(request.payload["query"], "ContextBuilder")
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits[0].payload["results"])

    def test_planned_ingest_step_synthesizes_title_and_path(self):
        from atlas_core.knowledge import parse_ingest_objective, resolve_knowledge_source

        self.assertEqual(
            parse_ingest_objective("Index local knowledge source README.md"),
            "README.md",
        )
        self.assertEqual(resolve_knowledge_source("README.md", roots=(REPO_ROOT,)), README_PATH.resolve())
        runtime = self._runtime()
        task = runtime.store.create_task(
            objective="Index local knowledge source README.md",
            success_criteria=("The source is durably indexed with chunk provenance.",),
            authority_scope="modify_internal",
        )
        runtime.store.add_step(
            task.id,
            description="Chunk and index extracted text.",
            capability="knowledge.ingest_text",
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        request = next(
            artifact
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_ingest_request"
        )
        self.assertEqual(request.payload["title"], "README.md")
        self.assertEqual(request.payload["source_path"], str(README_PATH.resolve()))
        self.assertNotIn("text", request.payload)

    def test_off_topic_riddle_is_an_honest_search_miss(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        constitution = (REPO_ROOT / "Atlas Constitution.md").read_text(encoding="utf-8")
        store.ingest_text(title="Atlas Constitution.md", text=constitution, source_uri="constitution")
        store.ingest_text(
            title="README.md",
            text=(REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            source_uri="readme",
        )
        riddle = (
            "fictional character breaks fourth wall selfless ascetics humor "
            "TV show 1960s-1980s fewer than 50 episodes"
        )
        hits = store.search(riddle, limit=8)
        self.assertEqual(hits, ())

        runtime = self._runtime()
        store_rt = KnowledgeStore(self.db)
        store_rt.initialize()
        task = runtime.store.create_task(
            objective="Search Atlas knowledge for: " + riddle,
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        request = runtime.store.put_artifact(
            task.id,
            kind="knowledge_search_request",
            payload={"query": riddle, "limit": 8},
        )
        runtime.store.add_step(
            task.id,
            description="Retrieve evidence",
            capability="knowledge.search",
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        payload = next(
            artifact.payload
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_search_results"
        )
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["status"], "no_relevant_results")
        self.assertFalse(
            any("text" in (claim.value or {}) for claim in runtime.store.list_claims(task.id) if claim.kind == "retrieved")
        )

    def test_on_topic_search_is_bounded_and_claims_omit_chunk_text(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        store.ingest_text(
            title="Atlas Constitution.md",
            text=(REPO_ROOT / "Atlas Constitution.md").read_text(encoding="utf-8"),
            source_uri="constitution",
        )
        hits = store.search("verification precedes completion", limit=8)
        self.assertTrue(hits)
        self.assertIn("verification", hits[0].chunk.text.casefold())
        self.assertGreaterEqual(hits[0].score, 0.25)

        runtime = self._runtime()
        task = runtime.store.create_task(
            objective="Search Atlas knowledge for: verification precedes completion",
            success_criteria=("A source-grounded local knowledge search result is produced.",),
            authority_scope="read",
        )
        request = runtime.store.put_artifact(
            task.id,
            kind="knowledge_search_request",
            payload={"query": "verification precedes completion", "limit": 8},
        )
        runtime.store.add_step(
            task.id,
            description="Retrieve evidence",
            capability="knowledge.search",
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        self.assertEqual(runtime.run_until_blocked(task.id).status, "completed")
        payload = next(
            artifact.payload
            for artifact in runtime.store.list_artifacts(task.id)
            if artifact.kind == "knowledge_search_results"
        )
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["results"])
        total_chars = sum(len(hit["text"]) for hit in payload["results"])
        self.assertLessEqual(total_chars, MAX_SEARCH_RESULT_CHARS)
        for claim in runtime.store.list_claims(task.id):
            if claim.kind != "retrieved":
                continue
            self.assertNotIn("text", claim.value)
            self.assertIn("chunk_id", claim.value)
        pack = ContextBuilder(runtime.store).build(
            task.id,
            runtime.store.list_steps(task.id)[-1].id,
            artifact_ids=tuple(
                artifact.id
                for artifact in runtime.store.list_artifacts(task.id)
                if artifact.kind == "knowledge_search_results"
            ),
        )
        claim_blob = json.dumps(pack.payload.get("claims") or [])
        self.assertNotIn(hits[0].chunk.text[:80], claim_blob)

    def test_search_verifier_rejects_off_topic_result_lists(self):
        spec = CapabilitySpec(
            id="knowledge.search",
            description="search",
            executor_kind="deterministic",
            verifier_id="knowledge.search_contract",
        )
        off_topic = {
            "query": "fictional character fourth wall ascetics humor 1960s television",
            "status": "ok",
            "results": [
                {
                    "chunk_id": "chunk_x",
                    "text": "Verification precedes completion. Atlas owns durable task state.",
                }
            ],
        }
        rejected = _search_verifier(spec, off_topic, {})
        self.assertEqual(rejected.status, "fail")
        self.assertIn("not relevant", rejected.summary)
        miss = _search_verifier(
            spec,
            {"query": "fictional character", "status": "no_relevant_results", "results": []},
            {},
        )
        self.assertEqual(miss.status, "pass")
        self.assertIn("no relevant", miss.summary)


if __name__ == "__main__":
    unittest.main()
