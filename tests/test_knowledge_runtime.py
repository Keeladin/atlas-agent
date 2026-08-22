from __future__ import annotations
from tests.capability_fixtures import make_registration

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from atlas_core.__main__ import _work_runtime
from atlas_core.advanced.brief import TaskBrief
from atlas_core.context import ContextBuilder
from atlas_core.knowledge import (
    MAX_SEARCH_RESULT_CHARS,
    KnowledgeStore,
    chunk_text,
    grounded_answer_from_hits,
    is_knowledge_question,
    normalized_text_sha256,
)
from atlas_core.knowledge.capabilities import _search_verifier
from atlas_core.presentation import WorkPresenter
from atlas_core.sources import LocalRootConfig, LocalRootRegistry


REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"


class KnowledgeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"
        self.registry = LocalRootRegistry()
        self.registry.register(LocalRootConfig(
            root_id="repo", provider_namespace="local", host_path=str(REPO_ROOT),
            display_name="Repository", configuration_revision="repo-1",
        ))
        self.registry.register(LocalRootConfig(
            root_id="scratch", provider_namespace="local", host_path=self.tmp.name,
            display_name="Scratch", configuration_revision="scratch-1",
        ))

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def _runtime(self):
        return _work_runtime(
            self.db, morning=False, knowledge=True,
            local_source_registry=self.registry,
        )

    @staticmethod
    def _store_ingest(store, *, title: str, text: str, suffix: str = "one"):
        return store.ingest_text(
            title=title,
            text=text,
            observation_artifact_id=f"artifact_observation_{suffix}",
            acquired_content_artifact_id=f"artifact_content_{suffix}",
        )

    def _ingest_via_path(
        self,
        runtime,
        source: Path,
        *,
        title: str | None = None,
        relative_path_override: str | None = None,
    ):
        if relative_path_override is not None:
            relative_path = relative_path_override
            root_id, revision = "scratch", "scratch-1"
        elif source.is_relative_to(REPO_ROOT):
            relative_path = source.relative_to(REPO_ROOT).as_posix()
            root_id, revision = "repo", "repo-1"
        else:
            relative_path = source.relative_to(Path(self.tmp.name)).as_posix()
            root_id, revision = "scratch", "scratch-1"
        payload = {
            "title": title or source.name,
            "chunk_chars": 4000,
            "overlap_chars": 400,
        }
        work_id = runtime.accept(
            TaskBrief(
                objective=f"Index local knowledge source {payload['title']}",
                capabilities=("files.read", "knowledge.ingest_text"),
                required_authority="modify_internal",
                expected_effect="The source is durably indexed with chunk provenance.",
            ),
            "modify_internal",
            inputs={
                "files.read": {
                    "provider_namespace": "local", "root_id": root_id,
                    "configuration_revision": revision,
                    "relative_path": relative_path,
                },
                "knowledge.ingest_text": payload,
            },
        )
        result = runtime.run(work_id)
        request = next(
            artifact
            for artifact in runtime.store.list_artifacts(work_id)
            if artifact.kind == "knowledge_ingest_text_request"
        )
        return runtime.store.get_work(work_id), request, result

    def _search(self, runtime, query: str, *, limit: int = 5):
        work_id = runtime.accept(
            TaskBrief(
                objective=f"Search Atlas knowledge for: {query}",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="A source-grounded local knowledge search result is produced.",
            ),
            "read",
            inputs={"knowledge.search": {"query": query, "limit": limit}},
        )
        result = runtime.run(work_id)
        return runtime.store.get_work(work_id), result

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
            if artifact.kind != "task_brief" and artifact.kind.endswith("_request")
        )
        self.assertNotIn("text", request.payload)
        self.assertNotIn("source_path", request.payload)

    def test_chunking_is_deterministic_and_overlapping(self):
        text = "\n\n".join(f"section {i} " + ("pump failure " * 50) for i in range(20))
        first = chunk_text(text, chunk_chars=800, overlap_chars=100)
        second = chunk_text(text, chunk_chars=800, overlap_chars=100)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 2)
        self.assertTrue(all(chunk.strip() for chunk in first))

    def test_chunk_schema_has_no_unused_metadata_column(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        with sqlite3.connect(self.db) as db:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(knowledge_chunks)")
            }
        self.assertNotIn("metadata_json", columns)

    def test_knowledge_ingest_is_content_idempotent_and_searchable(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        text = "RLH5 hydraulic pump failure. Replace the suction hose and inspect cavitation.\n\nBrake test procedure is separate."
        first = self._store_ingest(store, title="RLH5 manual", text=text)
        second = self._store_ingest(
            store, title="duplicate title", text=text, suffix="two"
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.document.id, second.document.id)
        self.assertNotIn(first.document.normalized_text_sha256[:24], first.document.id)
        self.assertEqual(
            [source.title for source in store.list_document_sources(first.document.id)],
            ["RLH5 manual", "duplicate title"],
        )
        hits = store.search("hydraulic cavitation", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.document_id, first.document.id)
        self.assertIn("cavitation", hits[0].chunk.text)
        listed = store.list_documents()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].id, first.document.id)

    def test_ingest_and_retrieve_execute_through_work_runtime(self):
        runtime = self._runtime()
        source = Path(self.tmp.name) / "pump.txt"
        source.write_text("A cavitating hydraulic pump may show noise and unstable pressure. Inspect suction restrictions.", encoding="utf-8")
        self.assertEqual(self._ingest_via_path(runtime, source)[2].status, "completed")
        search_task, result = self._search(runtime, "cavitating hydraulic pump")
        self.assertEqual(result.status, "completed")
        outputs = [
            a
            for a in runtime.store.list_artifacts(search_task.id)
            if a.kind == "knowledge_search_results"
        ]
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs[0].payload["results"])
        claims = runtime.store.list_claims(search_task.id)
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
            outputs[0].payload["normalized_text_sha256"],
            normalized_text_sha256(README_PATH.read_text(encoding="utf-8")),
        )
        search_task, search_result = self._search(runtime, "ContextBuilder")
        self.assertEqual(search_result.status, "completed")
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(search_task.id)
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits[0].payload["results"])
        self.assertIn("ContextBuilder", hits[0].payload["results"][0]["text"])
        self.assertNotIn("source_uri", request.payload)
        presentation = WorkPresenter(runtime.store).build(search_task.id)
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
        answer_id = runtime.accept(
            TaskBrief(
                objective="Search Atlas knowledge for: What is ContextBuilder?",
                capabilities=("knowledge.search", "knowledge.answer"),
                required_authority="read",
                expected_effect="An evidence-grounded answer cites retrieved sources.",
            ),
            "read",
            inputs={"knowledge.search": {"query": "What is ContextBuilder?", "limit": 5}},
        )
        self.assertEqual(runtime.run(answer_id).status, "completed")
        answers = [
            artifact
            for artifact in runtime.store.list_artifacts(answer_id)
            if artifact.kind == "grounded_answer"
        ]
        self.assertTrue(answers)
        self.assertIn("From retrieved sources", answers[0].payload)
        self.assertFalse(any(
            claim.subject == "knowledge.grounded_answer"
            for claim in runtime.store.list_claims(answer_id)
        ))
        answer_md = WorkPresenter(runtime.store).build(answer_id).render_markdown()
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
        search_task, search_result = self._search(runtime, planted)
        self.assertEqual(search_result.status, "completed")
        hits = [
            artifact
            for artifact in runtime.store.list_artifacts(search_task.id)
            if artifact.kind == "knowledge_search_results"
        ]
        self.assertTrue(hits[0].payload["results"])
        self.assertIn(planted, hits[0].payload["results"][0]["text"])

    def test_path_ingest_missing_file_fails_closed(self):
        missing = Path(self.tmp.name) / "does-not-exist.txt"
        runtime = self._runtime()
        task, _, result = self._ingest_via_path(runtime, missing)
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
        self.assertEqual(first.payload["status"], "created")
        self.assertEqual(second.payload["status"], "deduplicated")
        self.assertEqual(first.payload["document_id"], second.payload["document_id"])
        self.assertEqual(first.payload["normalized_text_sha256"], second.payload["normalized_text_sha256"])

    def test_extra_invocation_fields_do_not_fail_search_schema(self):
        runtime = self._runtime()
        self._ingest_via_path(runtime, README_PATH)
        work_id = runtime.accept(
            TaskBrief(
                objective="Search Atlas knowledge for: ContextBuilder",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="A source-grounded local knowledge search result is produced.",
            ),
            "read",
            inputs={
                "knowledge.search": {
                    "query": "ContextBuilder",
                    "limit": 5,
                    "authority_scope": "read",
                }
            },
        )
        self.assertEqual(runtime.run(work_id).status, "completed")

    def test_search_objective_parser_extracts_query(self):
        from atlas_core.knowledge import parse_search_objective

        self.assertEqual(
            parse_search_objective("Search Atlas knowledge for: ContextBuilder"),
            "ContextBuilder",
        )

    def test_off_topic_riddle_is_an_honest_search_miss(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        constitution = (REPO_ROOT / "Atlas Constitution.md").read_text(encoding="utf-8")
        self._store_ingest(store, title="Atlas Constitution.md", text=constitution)
        self._store_ingest(
            store,
            title="README.md",
            text=(REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            suffix="two",
        )
        riddle = (
            "fictional character breaks fourth wall selfless ascetics humor "
            "TV show 1960s-1980s fewer than 50 episodes"
        )
        hits = store.search(riddle, limit=8)
        self.assertEqual(hits, ())

        runtime = self._runtime()
        task, result = self._search(runtime, riddle, limit=8)
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
        self._store_ingest(
            store,
            title="Atlas Constitution.md",
            text=(REPO_ROOT / "Atlas Constitution.md").read_text(encoding="utf-8"),
        )
        hits = store.search("verification precedes completion", limit=8)
        self.assertTrue(hits)
        self.assertIn("verification", hits[0].chunk.text.casefold())
        self.assertGreaterEqual(hits[0].score, 0.25)

        runtime = self._runtime()
        task, result = self._search(runtime, "verification precedes completion", limit=8)
        self.assertEqual(result.status, "completed")
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
        spec = make_registration(
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

    def test_store_rejects_orphan_ingestion_but_helpers_remain_pure(self):
        store = KnowledgeStore(self.db)
        store.initialize()
        with self.assertRaises(TypeError):
            store.ingest_text(title="orphan", text="orphan source text")
        with self.assertRaisesRegex(ValueError, "artifact references"):
            store.ingest_text(
                title="orphan", text="orphan source text",
                observation_artifact_id="", acquired_content_artifact_id="content",
            )
        self.assertEqual(normalized_text_sha256("a\r\nb"), normalized_text_sha256("a\nb"))
        self.assertEqual(chunk_text("independent helper text"), ("independent helper text",))


if __name__ == "__main__":
    unittest.main()
