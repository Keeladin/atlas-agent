from __future__ import annotations
from tests.capability_fixtures import make_registration, register_cap

import tempfile
import sqlite3
import unittest
from pathlib import Path

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    
    ContextPolicy,
    ExecutionBudget,
)
from atlas_core.context import ContextBuilder
from atlas_core.planner import TaskPlanner
from atlas_core.providers import ModelResponse, ModelRouter, ProviderRegistry, ProviderSpec
from atlas_core.runtime import TaskRuntime
from atlas_core.tasks import InvalidTransitionError, TaskStore
from atlas_core.tools import (
    ToolConstraints,
    ToolDescriptor,
    ToolGateway,
    ToolOrigin,
    ToolResult,
)


class InspectingProvider:
    def __init__(self, spec: ProviderSpec, store: TaskStore, text: str = "ok") -> None:
        self.spec = spec
        self.store = store
        self.text = text
        self.calls = 0
        self.manifest_seen_before_call = False
        self.last_request = None

    def generate(self, request):
        self.calls += 1
        self.last_request = request
        task_id = str(request.metadata.get("task_id") or "")
        execution_manifest = request.metadata.get("context_manifest_id")
        manifests = self.store.list_context_manifests(task_id) if task_id else ()
        self.manifest_seen_before_call = bool(
            manifests and any(item.id == execution_manifest for item in manifests)
        )
        return ModelResponse(
            self.text,
            self.spec.key,
            self.spec.model,
            {"ok": True},
            {"input_tokens": 10, "output_tokens": 5},
        )


class RuntimeGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "atlas.db")
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, *, criteria=("Done",), authority="read"):
        return self.store.create_task(
            objective="Govern one bounded action",
            success_criteria=criteria,
            authority_scope=authority,
        )

    def test_schema_v1_migrates_capability_version_and_context_manifest_storage(self):
        path = Path(self.tmp.name) / "legacy-v1.db"
        db = sqlite3.connect(path)
        try:
            db.executescript(
                """
                CREATE TABLE atlas_schema_meta (
                    component TEXT PRIMARY KEY, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO atlas_schema_meta(component,version) VALUES('task_runtime',1);
                CREATE TABLE task_steps (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    description TEXT NOT NULL, capability TEXT, status TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL, input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE task_executions (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL, capability TEXT NOT NULL,
                    provider TEXT, attempt INTEGER NOT NULL, status TEXT NOT NULL, input_artifact_ids_json TEXT NOT NULL,
                    output_artifact_ids_json TEXT NOT NULL DEFAULT '[]', verifier_artifact_id TEXT,
                    receipt_json TEXT NOT NULL DEFAULT '{}', metrics_json TEXT NOT NULL DEFAULT '{}', error TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT
                );
                """
            )
            db.commit()
        finally:
            db.close()
        migrated = TaskStore(path)
        migrated.initialize()
        with migrated._db() as check:
            version = check.execute(
                "SELECT version FROM atlas_schema_meta WHERE component='task_runtime'"
            ).fetchone()["version"]
            step_columns = {row["name"] for row in check.execute("PRAGMA table_info(task_steps)").fetchall()}
            execution_columns = {row["name"] for row in check.execute("PRAGMA table_info(task_executions)").fetchall()}
            manifest_table = check.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_context_manifests'"
            ).fetchone()
        self.assertEqual(int(version), 2)
        self.assertIn("capability_version", step_columns)
        self.assertIn("capability_version", execution_columns)
        self.assertIsNotNone(manifest_table)

    def test_capability_registry_resolves_latest_but_exact_version_can_be_pinned(self):
        registry = CapabilityRegistry()
        registry.register(
            make_registration(
                id="demo.versioned", version="1.0.0", description="v1",
                executor_kind="deterministic", verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output="v1"),
        )
        registry.register(
            make_registration(
                id="demo.versioned", version="2.0.0", description="v2",
                executor_kind="deterministic", verifier_id="core.nonempty",
            ),
            lambda request: CapabilityOutcome("pass", output="v2"),
        )
        self.assertEqual(registry.get("demo.versioned").profile.version, "2.0.0")
        self.assertEqual(registry.get("demo.versioned", "1.0.0").profile.version, "1.0.0")

    def test_task_step_pins_capability_version_into_execution_truth(self):
        registry = CapabilityRegistry()
        for version, text in (("1.0.0", "old"), ("2.0.0", "new")):
            registry.register(
                make_registration(id="demo.pin", version=version, description=text, executor_kind="deterministic", verifier_id="core.nonempty"),
                lambda request, _text=text: CapabilityOutcome("pass", output=_text),
            )
        task = self.task()
        self.store.add_step(task.id, description="Use pinned contract", capability="demo.pin", capability_version="1.0.0", metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=registry).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.capability_version, "1.0.0")
        output = self.store.get_artifact(execution.output_artifact_ids[0])
        self.assertEqual(output.payload, "old")

    def test_context_manifest_is_persisted_before_model_call(self):
        registry = CapabilityRegistry()
        spec = make_registration(id="reasoning.manifest", version="1.3.0", description="reason", executor_kind="model", verifier_id="core.nonempty")
        registry.register(spec)
        providers = ProviderRegistry()
        provider = InspectingProvider(ProviderSpec("inspect", "m", "fake", {spec.id: 1.0}), self.store, text="grounded result")
        providers.register(provider)
        task = self.task()
        self.store.add_step(task.id, description="Reason once", capability=spec.id, capability_version=spec.profile.version, metadata={"accept_all_criteria": True})
        result = TaskRuntime(store=self.store, capabilities=registry, model_router=ModelRouter(providers)).run_until_blocked(task.id)
        self.assertEqual(result.status, "completed")
        self.assertTrue(provider.manifest_seen_before_call)
        manifest = self.store.list_context_manifests(task.id)[0]
        self.assertEqual(manifest.capability_version, "1.3.0")
        self.assertLessEqual(manifest.total_tokens, manifest.budget_tokens)
        self.assertTrue(any(item["type"] == "anchor" for item in manifest.manifest["included"]))

    def test_context_manifest_is_immutable_per_execution(self):
        registry = CapabilityRegistry()
        registry.register(make_registration(id="demo.manifest", description="bounded", executor_kind="deterministic", verifier_id="core.nonempty"), lambda request: CapabilityOutcome("pass", output={"ok": True}))
        task = self.task()
        step = self.store.add_step(task.id, description="Do", capability="demo.manifest")
        execution = self.store.begin_execution(task.id, step_id=step.id, capability="demo.manifest")
        spec = registry.get("demo.manifest")
        pack = ContextBuilder(self.store).build(task.id, step.id, artifact_ids=(), execution_id=execution.id, registration=spec)
        kwargs = dict(task_id=task.id, step_id=step.id, execution_id=execution.id, capability=spec.id, capability_version=spec.profile.version, assembler_version=pack.manifest.assembler_version, budget_tokens=pack.manifest.budget_tokens, total_tokens=pack.manifest.total_tokens, manifest=pack.manifest.as_dict(), manifest_id=pack.manifest.manifest_id)
        self.store.write_context_manifest(**kwargs)
        with self.assertRaises(InvalidTransitionError):
            self.store.write_context_manifest(**kwargs)

    def test_context_policy_records_dropped_artifacts(self):
        task = self.task()
        step = self.store.add_step(task.id, description="Bound context", capability="demo.context")
        artifacts = tuple(self.store.put_artifact(task.id, kind="source", payload={"n": i, "text": "x" * 100}) for i in range(3))
        spec = make_registration(id="demo.context", description="bounded", executor_kind="deterministic", verifier_id="core.nonempty", context_policy=ContextPolicy(max_tokens=1000, max_artifact_items=1))
        execution = self.store.begin_execution(task.id, step_id=step.id, capability=spec.id)
        pack = ContextBuilder(self.store).build(task.id, step.id, artifact_ids=tuple(item.id for item in artifacts), execution_id=execution.id, registration=spec)
        self.assertEqual(len(pack.payload["artifacts"]), 1)
        dropped_ids = {item.id for item in pack.manifest.dropped}
        self.assertTrue({artifacts[1].id, artifacts[2].id} <= dropped_ids)

    def test_tool_descriptor_enforces_command_allowlist_and_schema(self):
        gateway = ToolGateway()
        gateway.register(
            ToolDescriptor(id="terminal.safe", version="1.2.0", description="safe terminal", required_authority="modify_internal", input_schema={"type": "object", "required": ["command"], "properties": {"command": {"type": "string"}}, "additionalProperties": False}, output_schema={"type": "object", "required": ["exit_code"], "properties": {"exit_code": {"type": "integer"}}}, origin=ToolOrigin(type="internal", internal_handler="terminal.safe"), permissions=("execute",), constraints=ToolConstraints(allowed_commands=("pytest",), sandbox=True)),
            lambda arguments: ToolResult(True, output={"exit_code": 0}),
        )
        bad = gateway.invoke("terminal.safe", {"command": "rm -rf /"}, authority_scope="modify_internal")
        self.assertFalse(bad.ok)
        self.assertIn("allowlist", bad.error)
        extra = gateway.invoke("terminal.safe", {"command": "pytest", "oops": True}, authority_scope="modify_internal")
        self.assertFalse(extra.ok)
        self.assertIn("schema", extra.error)
        good = gateway.invoke("terminal.safe", {"command": "pytest -q"}, authority_scope="modify_internal")
        self.assertTrue(good.ok)
        self.assertEqual(gateway.get("terminal.safe")[0].version, "1.2.0")

    def test_capability_input_schema_fails_before_handler_runs(self):
        calls = {"n": 0}
        registry = CapabilityRegistry()
        def handler(request):
            calls["n"] += 1
            return CapabilityOutcome("pass", output={"ok": True})
        registry.register(make_registration(id="demo.input", description="typed input", executor_kind="deterministic", input_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}, "additionalProperties": False}, verifier_id="core.nonempty"), handler)
        task = self.task()
        input_artifact = self.store.put_artifact(task.id, kind="input", payload={"value": "wrong"})
        self.store.add_step(task.id, description="Typed", capability="demo.input", input_artifact_ids=(input_artifact.id,))
        result = TaskRuntime(store=self.store, capabilities=registry).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls["n"], 0)
        self.assertIn("input schema", self.store.list_executions(task.id)[0].error)

    def test_capability_output_schema_is_enforced(self):
        registry = CapabilityRegistry()
        registry.register(make_registration(id="demo.output", description="typed output", executor_kind="deterministic", output_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}, verifier_id="core.nonempty", budget=ExecutionBudget(max_attempts=1)), lambda request: CapabilityOutcome("pass", output={"value": "wrong"}))
        task = self.task()
        self.store.add_step(task.id, description="Typed output", capability="demo.output")
        result = TaskRuntime(store=self.store, capabilities=registry).run_until_blocked(task.id)
        self.assertEqual(result.status, "failed")
        execution = self.store.list_executions(task.id)[0]
        self.assertEqual(execution.status, "rework")
        self.assertIn("output schema", execution.error)

    def test_planner_uses_context_manifest_and_pins_planned_capability_versions(self):
        plan_text = ('{"steps":[{"key":"a","description":"Do","capability":"demo.work","dependencies":[],"satisfies_criteria":[1]}],"notes":[]}')
        planning = make_registration(id="planning.general", version="2.1.0", description="plan", executor_kind="model", required_authority="interpret", context_profile="plan", verifier_id="core.nonempty")
        providers = ProviderRegistry()
        provider = InspectingProvider(ProviderSpec("planner", "planner-model", "fake", {planning.id: 1.0}), self.store, text=plan_text)
        providers.register(provider)
        planner = TaskPlanner(store=self.store, model_router=ModelRouter(providers), planning_capability=planning, capability_manifest=[{"id": "demo.work", "version": "3.4.5"}])
        task, plan = planner.plan_and_create(objective="Plan durably", success_criteria=("Done",), authority_scope="interpret")
        self.assertTrue(provider.manifest_seen_before_call)
        steps = self.store.list_steps(task.id)
        self.assertEqual(steps[0].capability_version, "2.1.0")
        self.assertEqual(steps[1].capability_version, "3.4.5")
        self.assertEqual(plan.steps[0].capability_version, "3.4.5")
        manifests = self.store.list_context_manifests(task.id, step_id=steps[0].id)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].capability_version, "2.1.0")


if __name__ == "__main__":
    unittest.main()
