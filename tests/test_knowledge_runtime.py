from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atlas_core.bootstrap import build_runtime
from atlas_core.knowledge import KnowledgeStore, chunk_text


class KnowledgeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas.db"

    def tearDown(self):
        self.tmp.cleanup()

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


if __name__ == "__main__":
    unittest.main()
