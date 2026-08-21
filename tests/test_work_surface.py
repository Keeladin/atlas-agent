from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest

from atlas_core.advanced import TaskBrief
from atlas_core.capabilities import CapabilityBinding, CapabilityOutcome
from atlas_core.tools import MCPToolBridge, ToolDescriptor, ToolGateway, ToolResult
from atlas_core.work import (
    CapabilityExecutionProfile,
    DeploymentInventory,
    ExecutionSurface,
    ImplementationResolver,
    SurfaceError,
    build_work_runtime,
    compile_contract,
    project_surface,
)


SURFACE_SOURCE = (
    Path(__file__).resolve().parents[1] / "atlas_core" / "work" / "surface.py"
).read_text(encoding="utf-8")


def _handler(_request):
    return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})


class _TrackingGateway(ToolGateway):
    def __init__(self) -> None:
        super().__init__()
        self.manifest_calls = 0
        self.invocations: list[tuple[str, str | None]] = []

    def manifest(self, *, include_all_versions: bool = False):
        self.manifest_calls += 1
        return super().manifest(include_all_versions=include_all_versions)

    def invoke(self, tool_id, arguments, *, authority_scope, version=None):
        self.invocations.append((tool_id, version))
        return super().invoke(
            tool_id, arguments, authority_scope=authority_scope, version=version
        )


class _FakeMCP:
    def list_tools(self):
        return [
            {
                "name": "list_credentials",
                "description": "List credentials",
                "inputSchema": {"type": "object"},
            }
        ]

    def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": name}], "isError": False}


def _gateway_with_mail() -> _TrackingGateway:
    gateway = _TrackingGateway()
    gateway.register(
        ToolDescriptor(id="mail.deliver", description="v1", version="1.0.0"),
        lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
    )
    return gateway


def _mail_surface(gateway: ToolGateway, allowed: tuple[str, ...] = ("mail.deliver@1.0.0",)) -> ExecutionSurface:
    return ExecutionSurface(
        work_id="work_1",
        step_id="step_1",
        authority_scope="communicate",
        capability_id="communication.email.send",
        allowed_tools=frozenset(allowed),
        confirmation_required=True,
        eligible_providers=(),
        _kernel=gateway,
    )


class ExecutionSurfaceTests(unittest.TestCase):
    def test_pinned_tool_can_be_looked_up_and_invoked(self) -> None:
        gateway = _gateway_with_mail()
        surface = _mail_surface(gateway)
        spec = surface.descriptor("mail.deliver")
        self.assertEqual(spec.ref, "mail.deliver@1.0.0")
        result = surface.invoke("mail.deliver", {"to": "ops@example.invalid"})
        self.assertTrue(result.ok)
        self.assertEqual(gateway.invocations, [("mail.deliver", "1.0.0")])
        self.assertEqual(gateway.manifest_calls, 0)

    def test_extra_global_tool_is_unusable(self) -> None:
        gateway = _gateway_with_mail()
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Should stay inventory"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        surface = _mail_surface(gateway)
        with self.assertRaises(SurfaceError):
            surface.descriptor("bait.tool")
        result = surface.invoke("bait.tool", {})
        self.assertFalse(result.ok)
        self.assertIn("not on this work surface", result.error or "")
        self.assertEqual(gateway.invocations, [])

    def test_wrong_version_is_rejected(self) -> None:
        gateway = _gateway_with_mail()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v2", version="2.0.0"),
            lambda arguments: ToolResult(True, output={"v": 2}, receipt={"ok": True}),
        )
        surface = _mail_surface(gateway)
        result = surface.invoke("mail.deliver", {}, version="2.0.0")
        self.assertFalse(result.ok)
        result = surface.invoke("mail.deliver@2.0.0", {})
        self.assertFalse(result.ok)
        ok = surface.invoke("mail.deliver", {})
        self.assertTrue(ok.ok)
        self.assertEqual(gateway.invocations, [("mail.deliver", "1.0.0")])

    def test_bare_id_cannot_resolve_to_another_version(self) -> None:
        gateway = _gateway_with_mail()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="v2", version="2.0.0"),
            lambda arguments: ToolResult(True, output={"v": 2}, receipt={"ok": True}),
        )
        surface = _mail_surface(
            gateway,
            allowed=("mail.deliver@1.0.0", "mail.deliver@2.0.0"),
        )
        result = surface.invoke("mail.deliver", {})
        self.assertFalse(result.ok)
        self.assertEqual(gateway.invocations, [])

    def test_other_capability_tool_is_rejected(self) -> None:
        gateway = _gateway_with_mail()
        gateway.register(
            ToolDescriptor(id="index.write", description="Write index"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        surface = _mail_surface(gateway)
        result = surface.invoke("index.write", {})
        self.assertFalse(result.ok)
        self.assertEqual(gateway.invocations, [])

    def test_empty_surface_cannot_reach_global_inventory(self) -> None:
        gateway = _gateway_with_mail()
        surface = ExecutionSurface(
            work_id="work_1",
            step_id="step_1",
            authority_scope="read",
            capability_id="knowledge.index",
            allowed_tools=frozenset(),
            confirmation_required=False,
            eligible_providers=(),
            _kernel=gateway,
        )
        result = surface.invoke("mail.deliver", {})
        self.assertFalse(result.ok)
        self.assertEqual(gateway.invocations, [])

    def test_mcp_discovered_tools_are_inert(self) -> None:
        gateway = _gateway_with_mail()
        MCPToolBridge(_FakeMCP()).register_discovered(
            gateway, prefix="mcp.n8n", server_name="n8n"
        )
        surface = _mail_surface(gateway)
        result = surface.invoke("mcp.n8n.list_credentials", {})
        self.assertFalse(result.ok)
        self.assertEqual(gateway.invocations, [])

    def test_construction_does_not_enumerate_gateway(self) -> None:
        gateway = _gateway_with_mail()
        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            _handler,
        )
        contract = compile_contract(
            work_id="work_1",
            brief=TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            authority_scope="communicate",
            inventory=inventory,
            tools=gateway,
        )
        report = ImplementationResolver().resolve(contract, inventory, gateway)
        surface = project_surface(
            report.resolved.capabilities["communication.email.send"],
            work_id="work_1",
            step_id="step_1",
            authority_scope="communicate",
            kernel=gateway,
        )
        self.assertEqual(surface.allowed_tools, frozenset({"mail.deliver@1.0.0"}))
        self.assertEqual(gateway.manifest_calls, 0)

    def test_source_does_not_scan_inventory(self) -> None:
        for token in ("manifest(", "descriptors(", ".all("):
            with self.subTest(token=token):
                self.assertNotIn(token, SURFACE_SOURCE)


class WorkRuntimeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "atlas-work.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handler_receives_surface_and_cannot_use_bait_tool(self) -> None:
        gateway = _TrackingGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="bait.tool", description="Bait"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        seen: list[str] = []

        def handler(request):
            self.assertIsNotNone(request.surface)
            bait = request.surface.invoke("bait.tool", {})
            self.assertFalse(bait.ok)
            result = request.surface.invoke("mail.deliver", {"to": "ops@example.invalid"})
            seen.append(request.surface.capability_id)
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                receipt=result.receipt,
                error=result.error,
            )

        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            handler,
        )
        runtime = build_work_runtime(
            db_path=self.db, profiles=inventory, tool_gateway=gateway
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Send the report",
                capabilities=("communication.email.send",),
                required_authority="communicate",
                expected_effect="external communication",
            ),
            "communicate",
        )
        gateway.register(
            ToolDescriptor(id="late.tool", description="Registered after accept"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        from tests.work_helpers import run_with_confirmation

        result = run_with_confirmation(runtime, work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(seen, ["communication.email.send"])
        self.assertEqual(gateway.invocations, [("mail.deliver", "1.0.0")])
        self.assertNotIn("late.tool", [item[0] for item in gateway.invocations])
        self.assertNotIn("bait.tool", [item[0] for item in gateway.invocations])

    def test_empty_tool_capability_gets_an_empty_surface(self) -> None:
        gateway = _TrackingGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )

        def handler(request):
            self.assertEqual(request.surface.allowed_tools, frozenset())
            used = request.surface.invoke("mail.deliver", {})
            self.assertFalse(used.ok)
            return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})

        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            handler,
        )
        runtime = build_work_runtime(
            db_path=self.db, profiles=inventory, tool_gateway=gateway
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Create automation",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        from tests.work_helpers import run_with_confirmation

        result = run_with_confirmation(runtime, work_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(gateway.invocations, [])

    def test_handler_cannot_use_another_capability_tool(self) -> None:
        gateway = _TrackingGateway()
        gateway.register(
            ToolDescriptor(id="mail.deliver", description="Deliver"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        gateway.register(
            ToolDescriptor(id="index.write", description="Write"),
            lambda arguments: ToolResult(True, output=arguments, receipt={"ok": True}),
        )
        seen: list[frozenset[str]] = []

        def mail_handler(request):
            seen.append(request.surface.allowed_tools)
            other = request.surface.invoke("index.write", {})
            self.assertFalse(other.ok)
            result = request.surface.invoke("mail.deliver", {})
            return CapabilityOutcome(
                "pass" if result.ok else "fail",
                output=result.output,
                receipt=result.receipt,
                error=result.error,
            )

        def index_handler(request):
            return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})

        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="communication.email.send",
                implementation=CapabilityBinding(
                    "communication.email.send", "smtp", "send", "1"
                ),
                tools=("mail.deliver",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            mail_handler,
        )
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="knowledge.index",
                implementation=CapabilityBinding(
                    "knowledge.index", "internal", "index", "1"
                ),
                tools=("index.write",),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            index_handler,
        )
        runtime = build_work_runtime(
            db_path=self.db, profiles=inventory, tool_gateway=gateway
        )
        work_id = runtime.accept(
            TaskBrief(
                objective="Index then email",
                capabilities=("knowledge.index", "communication.email.send"),
                required_authority="communicate",
                expected_effect="Index and send",
            ),
            "communicate",
        )
        from tests.work_helpers import run_with_confirmation

        result = run_with_confirmation(runtime, work_id)
        self.assertEqual(result.status, "completed")
        self.assertIn(frozenset({"mail.deliver@1.0.0"}), seen)
        self.assertEqual(gateway.invocations, [("mail.deliver", "1.0.0")])

    def test_concurrent_runs_keep_run_local_surfaces(self) -> None:
        seen: list[tuple[str, str]] = []
        lock = threading.Lock()

        def handler(request):
            first = request.surface.work_id
            time.sleep(0.05)
            second = request.surface.work_id
            with lock:
                seen.append((first, second))
            return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})

        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            handler,
        )
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        first = runtime.accept(
            TaskBrief(
                objective="Create automation A",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        second = runtime.accept(
            TaskBrief(
                objective="Create automation B",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        from tests.work_helpers import run_with_confirmation

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda work_id: run_with_confirmation(runtime, work_id),
                    (first, second),
                )
            )
        self.assertEqual({item.status for item in results}, {"completed"})
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(start == end for start, end in seen))
        self.assertEqual({start for start, _end in seen}, {first, second})

    def test_nested_run_does_not_steal_outer_surface(self) -> None:
        holder: dict[str, object] = {}
        after_inner: list[str] = []

        def handler(request):
            from tests.work_helpers import run_with_confirmation

            runtime = holder["runtime"]
            work_ids = holder["ids"]
            if request.surface.work_id == work_ids[0]:
                inner = run_with_confirmation(runtime, work_ids[1])
                self.assertEqual(inner.status, "completed")
                after_inner.append(request.surface.work_id)
            return CapabilityOutcome("pass", output={"ok": True}, receipt={"ok": True})

        inventory = DeploymentInventory()
        inventory.register(
            CapabilityExecutionProfile(
                capability_id="automation.workflow.create",
                implementation=CapabilityBinding(
                    "automation.workflow.create", "internal", "record", "1"
                ),
                verifier_id="core.nonempty",
                executor_kind="deterministic",
            ),
            handler,
        )
        runtime = build_work_runtime(db_path=self.db, profiles=inventory)
        outer = runtime.accept(
            TaskBrief(
                objective="Create automation outer",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        inner = runtime.accept(
            TaskBrief(
                objective="Create automation inner",
                capabilities=("automation.workflow.create",),
                required_authority="execute_external",
                expected_effect="Create an automation workflow",
            ),
            "execute_external",
        )
        holder["runtime"] = runtime
        holder["ids"] = (outer, inner)
        from tests.work_helpers import run_with_confirmation

        result = run_with_confirmation(runtime, outer)
        self.assertEqual(result.status, "completed")
        self.assertEqual(after_inner, [outer])


if __name__ == "__main__":
    unittest.main()
