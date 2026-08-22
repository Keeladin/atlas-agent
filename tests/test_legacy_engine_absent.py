from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    ROOT / "atlas_core",
    ROOT / "atlas_api",
    ROOT / "atlas_companion",
    ROOT / "atlas_morning",
)
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "Atlas Architecture — Runtime and Topology.md",
    ROOT / "Atlas Product Definition.md",
    ROOT / "Atlas Product Direction.md",
    ROOT / "Atlas Morning Workflow — Behavioural Specification.md",
    ROOT / "Mobile Capture V1 — Behavioural Contract.md",
    *sorted((ROOT / "docs" / "architecture").glob("*.md")),
)
FORBIDDEN_IDENTIFIERS = (
    "TaskRuntime",
    "TaskPlanner",
    "TaskStore",
    "TaskStoreError",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "RuntimeFrame",
    "work_surfaces",
)
FORBIDDEN_PERSISTENCE = (
    "create_task(",
    "get_task(",
    "list_tasks(",
    "set_task_status(",
    "task_steps",
    "task_artifacts",
    "task_executions",
    "task_approvals",
    "task_events",
    "task_checkpoints",
    "task_criteria",
    "task_claims",
    "task_context_manifests",
    "task_runtime",
)
DELETED_MODULES = re.compile(
    r"atlas_core\.(planner|bootstrap|runtime_lifecycle|runtime_execution|runtime_finish)"
    r"|atlas_core\.runtime(?!_types)"
)
DOC_JUSTIFICATION = (
    "removed",
    "deleted",
    "disconnected",
    "dark",
    "unplugged",
    "do not restore",
    "no longer",
    "has no owner",
)
CONTRACTS_SOURCE = (
    ROOT / "atlas_core" / "capabilities" / "contracts.py"
).read_text(encoding="utf-8")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    return files


class LegacyEngineAbsentTests(unittest.TestCase):
    def test_production_python_has_no_legacy_execution_engine(self) -> None:
        files = _python_files()
        self.assertTrue(files)
        offenders: list[str] = []
        for path in files:
            source = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(ROOT))
            stripped = source.replace("build_work_runtime", "")
            if "build_runtime" in stripped:
                offenders.append(f"{relative}: build_runtime")
            if DELETED_MODULES.search(source):
                offenders.append(f"{relative}: deleted module import")
            for token in FORBIDDEN_IDENTIFIERS:
                if token in source:
                    offenders.append(f"{relative}: {token}")
            if "atlas_companion" in relative:
                continue
            if relative.endswith("advanced/intent.py"):
                continue
            stripped = stripped.replace("TaskBrief", "").replace("task_brief", "")
            for token in FORBIDDEN_PERSISTENCE:
                if token in stripped:
                    offenders.append(f"{relative}: {token}")
        self.assertEqual(offenders, [])

    def test_work_schema_has_no_task_tables(self) -> None:
        schema = (ROOT / "atlas_core" / "work" / "store_schema.py").read_text(
            encoding="utf-8"
        )
        for token in (
            "CREATE TABLE IF NOT EXISTS tasks",
            "task_steps",
            "task_id",
            "task_runtime",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS work (", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS work_contracts", schema)
        self.assertIn("FOREIGN KEY (work_id) REFERENCES work(id)", schema)

    def test_runtime_results_use_work_id(self) -> None:
        source = (ROOT / "atlas_core" / "runtime_types.py").read_text(encoding="utf-8")
        self.assertIn("work_id: str", source)
        self.assertNotIn("task_id", source)

    def test_companion_does_not_import_work_execution(self) -> None:
        companion = ROOT / "atlas_companion"
        forbidden = (
            "from atlas_core.work",
            "import atlas_core.work",
            "build_work_runtime",
            "atlas_core.planner",
            "atlas_core.bootstrap",
        )
        for path in sorted(companion.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(token, source)

    def test_capability_request_surface_is_the_tool_protocol(self) -> None:
        self.assertIn("class ToolSurface(Protocol)", CONTRACTS_SOURCE)
        self.assertIn("surface: ToolSurface | None", CONTRACTS_SOURCE)
        self.assertNotIn("surface: Any", CONTRACTS_SOURCE)
        self.assertNotIn("from atlas_core.work", CONTRACTS_SOURCE)
        self.assertNotIn("import atlas_core.work", CONTRACTS_SOURCE)
        self.assertNotIn("ExecutionSurface", CONTRACTS_SOURCE)

    def test_docs_do_not_describe_a_second_execution_engine(self) -> None:
        self.assertTrue(DOC_PATHS)
        tokens = ("TaskRuntime", "TaskPlanner", "CapabilityRegistry", "build_runtime")
        offenders: list[str] = []
        for path in DOC_PATHS:
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), 1):
                checked = line.replace("build_work_runtime", "")
                if not any(token in checked for token in tokens):
                    continue
                lowered = line.casefold()
                if any(token in lowered for token in DOC_JUSTIFICATION):
                    continue
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
