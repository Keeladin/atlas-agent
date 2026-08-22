from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from atlas_core.advanced import TaskBrief, TaskCriterion, TaskCriterionBinding
from atlas_core.capabilities import CapabilityBinding, CapabilityExecutionProfile, CapabilityOutcome
from atlas_core.evidence import qualifies_as_source_evidence
from atlas_core.presentation import WorkPresenter
from atlas_core.sources import LocalRootConfig, LocalRootRegistry
from atlas_core.sources.capabilities import _ERROR_STATUS, _error_outcome
from atlas_core.sources.errors import LocalSourceError
from atlas_core.verification import VerificationResult
from atlas_core.work import DeploymentInventory, UnavailableWork, build_work_runtime


class _PassingGroundedVerifier:
    def verify(self, _profile, _document):
        return VerificationResult("pass", "acquired source covers the criterion")


class FilesWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root_path = self.base / "files"
        self.root_path.mkdir()
        self.registry = LocalRootRegistry()
        self.registry.register(LocalRootConfig(
            root_id="documents",
            provider_namespace="local",
            host_path=str(self.root_path),
            display_name="Documents",
            configuration_revision="rev-1",
        ))
        self.db = self.base / "work.db"

    def tearDown(self) -> None:
        self.registry.close()
        self.tmp.cleanup()

    def runtime(self, **kwargs):
        return build_work_runtime(
            db_path=self.db,
            local_source_registry=self.registry,
            **kwargs,
        )

    def request(self, path: str = ".", **extra):
        return {
            "provider_namespace": "local",
            "root_id": "documents",
            "configuration_revision": "rev-1",
            "relative_path": path,
            **extra,
        }

    def brief(self, capability: str, *, grounded: bool = False) -> TaskBrief:
        criteria = ()
        bindings = ()
        if grounded:
            criteria = (TaskCriterion(
                f"The {capability} observation satisfies the requested source fact.",
                satisfaction_policy="evidence_grounded",
                semantic_verification="required",
            ),)
            bindings = (TaskCriterionBinding(1, 1),)
        return TaskBrief(
            objective=f"Run {capability} on the configured local source",
            capabilities=(capability,),
            required_authority="read",
            expected_effect=f"Return the {capability} result",
            criteria=criteria,
            criterion_bindings=bindings,
        )

    def execute(self, capability: str, request: dict, *, grounded: bool = False):
        runtime = self.runtime()
        if grounded:
            runtime._engine.grounded_criterion_verifier = _PassingGroundedVerifier()
        work_id = runtime.accept(
            self.brief(capability, grounded=grounded),
            "read",
            inputs={capability: request},
        )
        result = runtime.run(work_id)
        return runtime, work_id, result

    def test_capability_registration_and_no_root_unavailability(self) -> None:
        runtime = self.runtime()
        for capability in ("files.list", "files.stat", "files.hash", "files.read"):
            profile = runtime._profiles.get(capability)
            self.assertIsNotNone(profile)
            self.assertEqual(profile.executor_kind, "tool")
            self.assertEqual(profile.implementation.provider, "local_sources")
            self.assertEqual(
                profile.tools,
                (f"native.local_sources.{capability.rsplit('.', 1)[1]}@1.0.0",),
            )
            self.assertEqual(profile.input_schema["x-atlas-root-bindings"], [{
                "provider_namespace": "local",
                "root_id": "documents",
                "configuration_revision": "rev-1",
            }])
        unavailable = build_work_runtime(db_path=self.base / "empty.db")
        with self.assertRaises(UnavailableWork):
            unavailable.accept(self.brief("files.stat"), "read", inputs={"files.stat": self.request("x")})

    def test_root_read_policy_is_enforced_by_kernel(self) -> None:
        registry = LocalRootRegistry()
        registry.register(LocalRootConfig(
            root_id="closed", provider_namespace="local", host_path=str(self.root_path),
            read_allowed=False, configuration_revision="closed-1",
        ))
        try:
            runtime = build_work_runtime(db_path=self.base / "closed.db", local_source_registry=registry)
            brief = TaskBrief("Stat closed source", ("files.stat",), "read", "Observe metadata")
            work_id = runtime.accept(brief, "read", inputs={"files.stat": {
                "provider_namespace": "local", "root_id": "closed",
                "configuration_revision": "closed-1", "relative_path": ".",
            }})
            result = runtime.run(work_id)
            execution = runtime.store.list_executions(work_id)[0]
            self.assertEqual(result.status, "waiting")
            self.assertEqual(execution.status, "blocked")
            self.assertEqual(execution.receipt["error_code"], "operation_not_allowed")
        finally:
            registry.close()

    def test_files_list_work_persists_observations_receipt_and_backend(self) -> None:
        for name in ("b.txt", ".hidden", "a.txt"):
            (self.root_path / name).write_text(name, encoding="utf-8")
        runtime, work_id, result = self.execute("files.list", self.request(".", page_size=2))
        self.assertEqual(result.status, "completed")
        execution = runtime.store.list_executions(work_id)[0]
        output = runtime.store.get_artifact(execution.output_artifact_ids[0])
        self.assertEqual(output.kind, "files_list_observation")
        self.assertEqual(output.provenance_category, "acquired_observation")
        self.assertEqual(
            [item["source_ref"]["relative_path"] for item in output.payload["entries"]],
            [".hidden", "a.txt"],
        )
        self.assertTrue(output.payload["next_cursor"])
        self.assertEqual(execution.receipt["entries_processed"], 2)
        self.assertIn(execution.receipt["backend"], {"linux_openat2", "linux_openat_fallback"})
        self.assertEqual(execution.receipt["artifact_ids"], list(execution.output_artifact_ids))

    def test_files_stat_work(self) -> None:
        (self.root_path / "item.txt").write_text("hello", encoding="utf-8")
        runtime, work_id, result = self.execute("files.stat", self.request("item.txt"))
        self.assertEqual(result.status, "completed")
        artifact = runtime.store.get_artifact(runtime.store.list_executions(work_id)[0].output_artifact_ids[0])
        self.assertEqual(artifact.payload["observation"]["object_type"], "regular_file")
        self.assertEqual(artifact.payload["observation"]["consistency"], "metadata_only")

    def test_files_hash_work_uses_exact_source_byte_hash(self) -> None:
        raw = b"exact\x00bytes\n"
        (self.root_path / "item.dat").write_bytes(raw)
        runtime, work_id, result = self.execute("files.hash", self.request("item.dat"))
        self.assertEqual(result.status, "completed")
        execution = runtime.store.list_executions(work_id)[0]
        artifact = runtime.store.get_artifact(execution.output_artifact_ids[0])
        self.assertEqual(artifact.payload["observation"]["byte_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(artifact.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(execution.receipt["bytes_processed"], len(raw))

    def test_files_read_persists_separate_observation_and_content(self) -> None:
        raw = "hello λ".encode("utf-8")
        (self.root_path / "item.txt").write_bytes(raw)
        runtime, work_id, result = self.execute("files.read", self.request("item.txt"))
        self.assertEqual(result.status, "completed")
        execution = runtime.store.list_executions(work_id)[0]
        artifacts = [runtime.store.get_artifact(item) for item in execution.output_artifact_ids]
        self.assertEqual(
            [item.provenance_category for item in artifacts],
            ["acquired_observation", "acquired_content"],
        )
        observation, content = artifacts
        self.assertEqual(observation.kind, "files_read_observation")
        self.assertEqual(content.kind, "files_acquired_content")
        self.assertEqual(content.payload["text"], "hello λ")
        self.assertEqual(content.payload["source_observation_id"], observation.payload["observation"]["observation_id"])
        receipts = [item for item in runtime.store.list_artifacts(work_id) if item.kind == "execution_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].provenance_category, "execution_receipt")
        self.assertFalse(qualifies_as_source_evidence(receipts[0]))
        self.assertNotIn(
            str(self.root_path),
            str([item.payload for item in runtime.store.list_artifacts(work_id)]),
        )

    def test_invocation_input_is_context_visible_but_never_source_evidence(self) -> None:
        (self.root_path / "item.txt").write_text("hello", encoding="utf-8")
        runtime, work_id, result = self.execute("files.stat", self.request("item.txt"))
        self.assertEqual(result.status, "completed")
        request = next(
            item for item in runtime.store.list_artifacts(work_id)
            if item.kind == "files_stat_request"
        )
        self.assertEqual(request.provenance_category, "invocation_input")
        self.assertFalse(qualifies_as_source_evidence(request))
        manifest = runtime.store.list_context_manifests(work_id)[0]
        included = {item["id"]: item for item in manifest.manifest["included"]}
        self.assertIn(request.id, included)
        self.assertEqual(included[request.id]["representation"], "full")
        self.assertNotIn(
            request.id,
            {item["id"] for item in WorkPresenter(runtime.store).build(work_id).outputs},
        )

    def test_evidence_eligibility_requires_structured_controlled_payloads(self) -> None:
        malformed_observation = SimpleNamespace(
            provenance_category="acquired_observation",
            metadata={},
            payload={"query": "ordinary request data"},
        )
        malformed_content = SimpleNamespace(
            provenance_category="acquired_content",
            metadata={"source_consistency": "stable"},
            payload={"text": "unattributed"},
        )
        generated = SimpleNamespace(
            provenance_category="generated_deliverable", metadata={}, payload={"claim": True}
        )
        receipt = SimpleNamespace(
            provenance_category="execution_receipt", metadata={}, payload={"ok": True}
        )
        verifier = SimpleNamespace(
            provenance_category="verifier_result", metadata={}, payload={"status": "pass"}
        )
        for artifact in (
            malformed_observation, malformed_content, generated, receipt, verifier
        ):
            self.assertFalse(qualifies_as_source_evidence(artifact))

        (self.root_path / "evidence.txt").write_text("evidence", encoding="utf-8")
        runtime, work_id, result = self.execute("files.read", self.request("evidence.txt"))
        self.assertEqual(result.status, "completed")
        evidence = [
            item for item in runtime.store.list_artifacts(work_id)
            if item.provenance_category in {"acquired_observation", "acquired_content"}
        ]
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(qualifies_as_source_evidence(item) for item in evidence))

    def test_generic_capability_cannot_declare_acquired_evidence(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.search",
                executor_kind="deterministic",
                output_kind="knowledge_search_results",
                verifier_id="core.nonempty",
            ),
            lambda _request: CapabilityOutcome(
                "pass",
                output={"observation": {}},
                output_kind="forged_source",
                output_provenance_category="acquired_observation",
            ),
        )
        runtime = build_work_runtime(db_path=self.base / "forged.db", profiles=inventory)
        work_id = runtime.accept(
            TaskBrief("Search", ("knowledge.search",), "read", "Return results"),
            "read",
            inputs={"knowledge.search": {"query": "x"}},
        )
        result = runtime.run(work_id)
        self.assertEqual(result.status, "failed")
        self.assertFalse(any(
            item.provenance_category in {"acquired_observation", "acquired_content"}
            for item in runtime.store.list_artifacts(work_id)
        ))

    def test_acquired_observation_and_content_can_satisfy_grounded_criteria(self) -> None:
        (self.root_path / "item.txt").write_text("grounded", encoding="utf-8")
        for capability in ("files.stat", "files.read"):
            with self.subTest(capability=capability):
                if self.db.exists():
                    self.db.unlink()
                runtime, work_id, result = self.execute(capability, self.request("item.txt"), grounded=True)
                self.assertEqual(result.status, "completed")
                criterion = runtime.store.list_criteria(work_id)[0]
                evidence = [runtime.store.get_artifact(item) for item in criterion.evidence_artifact_ids]
                self.assertTrue(evidence)
                self.assertTrue(all(qualifies_as_source_evidence(item) for item in evidence))
                if capability == "files.read":
                    self.assertIn("acquired_content", {item.provenance_category for item in evidence})

    def test_real_source_evidence_preserves_append_only_verification_history(self) -> None:
        class ReworkThenPass:
            def __init__(inner):
                inner.calls = 0

            def verify(inner, _profile, _document):
                inner.calls += 1
                return VerificationResult(
                    "rework" if inner.calls == 1 else "pass",
                    "retry coverage" if inner.calls == 1 else "source covers criterion",
                )

        (self.root_path / "history.txt").write_text("grounded history", encoding="utf-8")
        runtime = self.runtime()
        verifier = ReworkThenPass()
        runtime._engine.grounded_criterion_verifier = verifier
        work_id = runtime.accept(
            self.brief("files.read", grounded=True),
            "read",
            inputs={"files.read": self.request("history.txt")},
        )
        result = runtime.run(work_id)
        criterion = runtime.store.list_criteria(work_id)[0]
        history = runtime.store.list_criterion_verifications(criterion.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(tuple(item.status for item in history), ("rework", "pass"))
        self.assertEqual(criterion.verification_artifact_id, history[-1].artifact_id)
        self.assertEqual(criterion.status, "accepted")

    def test_generated_output_cannot_self_support_grounded_completion(self) -> None:
        inventory = DeploymentInventory()
        inventory.register(CapabilityExecutionProfile(
            capability_id="files.stat",
            implementation=CapabilityBinding("files.stat", "test", "generated", "1"),
            verifier_id="core.nonempty",
            executor_kind="deterministic",
            input_schema={"type": "object"},
        ), lambda request: CapabilityOutcome(
            "pass", output={"claim": "generated"}, claims=({
                "kind": "observed", "subject": "generated.self", "value": True,
                "criterion_ordinals": request.criterion_ordinals,
            },),
        ))
        runtime = build_work_runtime(db_path=self.base / "generated.db", profiles=inventory)
        runtime._engine.grounded_criterion_verifier = _PassingGroundedVerifier()
        work_id = runtime.accept(self.brief("files.stat", grounded=True), "read", inputs={"files.stat": {}})
        result = runtime.run(work_id)
        artifact = runtime.store.get_artifact(runtime.store.list_executions(work_id)[0].output_artifact_ids[0])
        self.assertEqual(artifact.provenance_category, "generated_deliverable")
        self.assertFalse(qualifies_as_source_evidence(artifact))
        self.assertNotEqual(result.status, "completed")
        self.assertEqual(runtime.store.list_criteria(work_id)[0].status, "pending")

    def test_read_content_uses_normal_dependency_context_manifest(self) -> None:
        (self.root_path / "item.txt").write_text("exact acquired content", encoding="utf-8")
        inventory = DeploymentInventory()
        seen = {}

        def consume(request):
            seen["artifacts"] = request.context["artifacts"]
            return CapabilityOutcome("pass", output={"consumed": True})

        inventory.register(CapabilityExecutionProfile(
            capability_id="knowledge.answer",
            implementation=CapabilityBinding("knowledge.answer", "test", "consume", "1"),
            verifier_id="core.nonempty", executor_kind="deterministic",
            requires_artifact_kinds=("files_acquired_content",),
            input_schema={"type": "object"}, output_schema={"type": "object"},
        ), consume)
        runtime = self.runtime(profiles=inventory)
        brief = TaskBrief(
            "Read then consume exact acquired text",
            ("files.read", "knowledge.answer"), "read", "Consume acquired content",
        )
        work_id = runtime.accept(brief, "read", inputs={"1": self.request("item.txt")})
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        steps = runtime.store.list_steps(work_id)
        content_id = next(
            artifact_id
            for artifact_id in runtime.store.list_executions(work_id, step_id=steps[0].id)[0].output_artifact_ids
            if runtime.store.get_artifact(artifact_id).provenance_category == "acquired_content"
        )
        included = next(item for item in seen["artifacts"] if item["id"] == content_id)
        self.assertEqual(included["payload"]["text"], "exact acquired content")
        manifest = runtime.store.context_manifest_for_execution(
            runtime.store.list_executions(work_id, step_id=steps[1].id)[0].id
        )
        manifest_item = next(item for item in manifest.manifest["included"] if item["id"] == content_id)
        self.assertEqual(manifest_item["representation"], "full")

    def test_repeated_occurrences_keep_ordinal_and_request_identity(self) -> None:
        (self.root_path / "one.txt").write_text("one", encoding="utf-8")
        (self.root_path / "two.txt").write_text("two", encoding="utf-8")
        runtime = self.runtime()
        brief = TaskBrief("Stat two files", ("files.stat", "files.stat"), "read", "Observe both files")
        work_id = runtime.accept(brief, "read", inputs={"1": self.request("one.txt"), "2": self.request("two.txt")})
        result = runtime.run(work_id)
        self.assertEqual(result.status, "completed")
        steps = runtime.store.list_steps(work_id)
        self.assertEqual([item.contract_capability_ordinal for item in steps], [1, 2])
        paths = []
        for step in steps:
            execution = runtime.store.list_executions(work_id, step_id=step.id)[0]
            artifact = runtime.store.get_artifact(execution.output_artifact_ids[0])
            paths.append(artifact.payload["observation"]["source_ref"]["relative_path"])
        self.assertEqual(paths, ["one.txt", "two.txt"])

    def test_kernel_error_mapping_is_complete_and_drift_is_not_evidence(self) -> None:
        expected = {
            "root_unknown": "blocked", "root_revision_unavailable": "blocked",
            "operation_not_allowed": "blocked", "invalid_path": "fail",
            "outside_root": "fail", "missing": "fail", "permission_denied": "blocked",
            "symlink_rejected": "fail", "wrong_type": "fail",
            "special_object_rejected": "fail", "too_large": "fail",
            "unsupported_encoding": "fail", "unsupported_platform": "blocked",
            "timeout": "abstain", "cancelled": "abstain", "drifted": "rework",
            "unreadable": "abstain", "internal_invariant": "fail",
        }
        self.assertEqual(_ERROR_STATUS, expected)
        drifted = {
            "observation_id": "obs_drift", "observation_payload_sha256": "a" * 64,
            "source_ref": {"source_id": "documents:item", "relative_path": "item"},
            "consistency": "drifted", "completeness": "complete", "acquisition": {},
        }
        outcome = _error_outcome("hash", LocalSourceError(
            "drifted", "changed", root_id="documents", relative_path="item",
            details={"observation": drifted},
        ))
        self.assertEqual(outcome.status, "rework")
        self.assertEqual(outcome.artifacts[0].provenance_category, "acquired_observation")

    def test_drifted_acquisition_cannot_satisfy_a_grounded_criterion(self) -> None:
        class DriftKernel:
            def source_ref(inner, provider_namespace, root_id, relative_path):
                from atlas_core.sources import SourceRef
                return SourceRef(
                    "local_file", provider_namespace, f"{root_id}:{relative_path}",
                    root_id, relative_path, f"{root_id}/{relative_path}",
                )

            def hash(inner, provider_namespace, root_id, relative_path, **_kwargs):
                from atlas_core.sources import SourceObservation

                observation = SourceObservation.create(
                    observation_id="obs_drift",
                    source_ref=inner.source_ref(provider_namespace, root_id, relative_path),
                    observed_at="2026-01-01T00:00:00+00:00",
                    observation_kind="hash",
                    object_type="regular_file",
                    consistency="drifted",
                    completeness="complete",
                    byte_size=3,
                    metadata={"diagnostic_digest": "e" * 64},
                    acquisition={
                        "provider_namespace": provider_namespace,
                        "root_id": root_id,
                        "configuration_revision": "rev-1",
                        "operation": "hash",
                        "filesystem_policy_version": "local-files-v1",
                        "backend": "linux_openat2",
                    },
                ).to_dict()
                raise LocalSourceError(
                    "drifted", "changed", root_id=root_id,
                    relative_path=relative_path, details={"observation": observation},
                )

        runtime = build_work_runtime(
            db_path=self.base / "drift.db",
            local_source_registry=self.registry,
            local_source_kernel=DriftKernel(),
        )
        runtime._engine.grounded_criterion_verifier = _PassingGroundedVerifier()
        work_id = runtime.accept(
            self.brief("files.hash", grounded=True), "read",
            inputs={"files.hash": self.request("item.txt")},
        )
        result = runtime.run(work_id)
        executions = runtime.store.list_executions(work_id)
        artifacts = [
            runtime.store.get_artifact(artifact_id)
            for execution in executions
            for artifact_id in execution.output_artifact_ids
        ]
        self.assertEqual(result.status, "failed")
        self.assertTrue(executions)
        self.assertTrue(all(item.status == "rework" for item in executions))
        self.assertTrue(artifacts)
        self.assertTrue(all(not qualifies_as_source_evidence(item) for item in artifacts))
        self.assertEqual(runtime.store.list_criteria(work_id)[0].status, "pending")


if __name__ == "__main__":
    unittest.main()
