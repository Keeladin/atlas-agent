from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import atlas_core.__main__ as cli
from atlas_companion.server import CompanionService
from atlas_core.advanced.brief import TaskBrief
from atlas_core.knowledge import KnowledgeStore, register_knowledge_capabilities
from atlas_core.knowledge import capabilities as knowledge_capabilities
from atlas_core.sources import LocalRootConfig, LocalRootRegistry
from atlas_core.verification import VerifierRegistry
from atlas_core.work import DeploymentInventory, build_work_runtime


class SourceConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.db = self.base / "atlas.db"
        self.registry = LocalRootRegistry()
        self.registry.register(LocalRootConfig(
            root_id="docs", provider_namespace="local", host_path=str(self.root),
            display_name="Documents", configuration_revision="rev-1",
        ))
        inventory = DeploymentInventory()
        verifiers = VerifierRegistry()
        self.runtime = build_work_runtime(
            db_path=self.db, profiles=inventory, verifiers=verifiers,
            local_source_registry=self.registry,
        )
        self.knowledge = KnowledgeStore(self.db)
        self.knowledge.initialize()
        register_knowledge_capabilities(
            inventory, verifiers, store=self.runtime.store,
            knowledge_store=self.knowledge,
        )

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def ingest(self, relative_path: str):
        work_id = self.runtime.accept(
            TaskBrief(
                f"Index local knowledge source {relative_path}",
                ("files.read", "knowledge.ingest_text"),
                "modify_internal",
                "Persist normalized Knowledge with exact source provenance.",
            ),
            "modify_internal",
            inputs={
                "files.read": {
                    "provider_namespace": "local", "root_id": "docs",
                    "configuration_revision": "rev-1",
                    "relative_path": relative_path,
                },
                "knowledge.ingest_text": {
                    "title": relative_path,
                    "chunk_chars": 512, "overlap_chars": 32,
                },
            },
        )
        result = self.runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        output = next(
            item for item in self.runtime.store.list_artifacts(work_id)
            if item.kind == "knowledge_ingest_result"
        )
        return work_id, output

    def test_controlled_content_and_exact_provenance_are_retained(self):
        raw = b"alpha\r\nbeta\r\n"
        (self.root / "note.txt").write_bytes(raw)
        work_id, output = self.ingest("note.txt")
        self.assertEqual(output.payload["source_byte_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(output.payload["source_byte_sha256"], output.payload["normalized_text_sha256"])
        sources = self.knowledge.list_document_sources(output.payload["document_id"])
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.acquired_content_artifact_id, output.payload["acquired_content_artifact_id"])
        artifacts = {item.id: item for item in self.runtime.store.list_artifacts(work_id)}
        observation = artifacts[source.observation_artifact_id].payload["observation"]
        self.assertEqual(observation["source_ref"]["relative_path"], "note.txt")
        self.assertEqual(observation["observation_id"], output.payload["source_observation_id"])
        self.assertEqual(artifacts[source.acquired_content_artifact_id].payload["text"], raw.decode("utf-8"))
        self.assertNotIn(str(self.root), json.dumps([item.payload for item in artifacts.values()]))

    def test_identical_normalized_content_keeps_distinct_source_observations(self):
        (self.root / "one.txt").write_text("same normalized text\n", encoding="utf-8")
        (self.root / "two.txt").write_text("same normalized text\n", encoding="utf-8")
        _, first = self.ingest("one.txt")
        _, second = self.ingest("two.txt")
        self.assertEqual(first.payload["document_id"], second.payload["document_id"])
        sources = self.knowledge.list_document_sources(first.payload["document_id"])
        self.assertEqual(len(sources), 2)
        paths = {
            self.runtime.store.get_artifact(item.observation_artifact_id)
            .payload["observation"]["source_ref"]["relative_path"]
            for item in sources
        }
        self.assertEqual(paths, {"one.txt", "two.txt"})
        self.assertEqual(len({item.observation_artifact_id for item in sources}), 2)

    def test_search_derivative_is_not_source_evidence(self):
        (self.root / "proof.txt").write_text("controlled-source-probe evidence\n", encoding="utf-8")
        ingest_work_id, _ = self.ingest("proof.txt")
        work_id = self.runtime.accept(
            TaskBrief(
                "Search Atlas knowledge for: controlled-source-probe",
                ("knowledge.search",), "read", "Return matching source excerpts.",
            ),
            "read",
            inputs={"knowledge.search": {"query": "controlled-source-probe", "limit": 3}},
        )
        self.assertEqual(self.runtime.run(work_id).status, "completed")
        artifacts = self.runtime.store.list_artifacts(work_id)
        derivative = next(item for item in artifacts if item.kind == "knowledge_search_results")
        self.assertEqual(derivative.provenance_category, "generated_deliverable")
        claims = self.runtime.store.list_claims(work_id)
        self.assertTrue(claims)
        replicas = {
            item.id: item for item in artifacts
            if item.metadata.get("purpose") == "knowledge_source_evidence"
        }
        self.assertTrue(replicas)
        for claim in claims:
            self.assertNotIn(derivative.id, claim.evidence_artifact_ids)
            self.assertTrue(claim.evidence_artifact_ids)
            self.assertTrue(all(
                self.runtime.store.get_artifact(item).provenance_category
                in {"acquired_observation", "acquired_content"}
                for item in claim.evidence_artifact_ids
            ))
            for artifact_id in claim.evidence_artifact_ids:
                replica = replicas[artifact_id]
                origin = self.runtime.store.get_artifact(
                    replica.metadata["origin_artifact_id"]
                )
                self.assertEqual(origin.work_id, ingest_work_id)
                self.assertEqual(origin.sha256, replica.sha256)
                self.assertEqual(origin.sha256, replica.metadata["origin_artifact_sha256"])

    def test_ingest_profile_accepts_only_controlled_acquired_content(self):
        schema = self.runtime._profiles.get("knowledge.ingest_text").input_schema
        self.assertNotIn("text", schema["properties"])
        self.assertNotIn("source_uri", schema["properties"])
        self.assertNotIn("content_sha256", schema["properties"])
        self.assertEqual(
            self.runtime._profiles.get("knowledge.ingest_text").requires_artifact_kinds,
            ("files_acquired_content",),
        )

    def test_callers_have_no_direct_local_acquisition(self):
        knowledge_source = inspect.getsource(knowledge_capabilities)
        self.assertNotIn("read_text(", knowledge_source)
        self.assertNotIn(".resolve(", knowledge_source)
        cli_index = inspect.getsource(cli.main).split('if args.command == "search":', 1)[0]
        for forbidden in ("read_text(", ".stat(", ".resolve(", "normalized_text_sha256"):
            self.assertNotIn(forbidden, cli_index)
        companion_source = inspect.getsource(CompanionService.stat_source)
        for forbidden in ("Path(", "read_text(", ".stat(", ".resolve("):
            self.assertNotIn(forbidden, companion_source)

    def test_companion_rejects_arbitrary_path_shape_and_uses_files_stat(self):
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        service = CompanionService(db_path=self.db, work_runtime=self.runtime)
        with self.assertRaises(TypeError):
            service.stat_source("/etc/passwd")
        result = service.stat_source(
            provider_namespace="local", root_id="docs", relative_path="note.txt",
            configuration_revision="rev-1",
        )
        self.assertEqual(result["observation"]["source_ref"]["relative_path"], "note.txt")
        execution = self.runtime.store.list_executions(result["work_id"])[0]
        self.assertEqual(execution.capability, "files.stat")


if __name__ == "__main__":
    unittest.main()
