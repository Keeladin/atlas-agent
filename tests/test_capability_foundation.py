from __future__ import annotations

import unittest

from atlas_core.capabilities import (
    CapabilityBinding,
    CapabilityBindingIndex,
    CapabilityOutcome,
    CapabilityRegistry,
    CapabilitySpec,
)
from atlas_core.integrations.n8n_mcp import N8NMCPConfig, N8NMCPProvider
from atlas_core.tools import ToolGateway


class FakeMCP:
    def list_tools(self):
        return [
            {"name": "execute_workflow", "description": "Run a workflow", "inputSchema": {"type": "object"}},
            {"name": "list_credentials", "description": "List credentials", "inputSchema": {"type": "object"}},
            {"name": "update_workflow", "description": "Update a workflow", "inputSchema": {"type": "object"}},
        ]

    def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": name}], "isError": False}


def _execute_spec(**kwargs) -> CapabilitySpec:
    defaults = dict(
        id="automation.workflow.execute",
        description="Execute an automation workflow",
        executor_kind="tool",
        required_authority="execute_external",
        side_effect_class="external_effect",
        confirmation="required",
        verifier_id="core.receipt",
        side_effects=("external_workflow",),
        idempotent=False,
    )
    defaults.update(kwargs)
    return CapabilitySpec(**defaults)


class CapabilityFoundationTests(unittest.TestCase):
    def test_mcp_discovered_tools_are_not_automatically_capabilities(self):
        gateway = ToolGateway()
        provider = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={"ATLAS_N8N_MCP_TOKEN": "token"},
            client_factory=lambda url, **kwargs: FakeMCP(),
        )
        status = provider.connect(gateway)
        self.assertTrue(status.available)
        self.assertEqual(len(provider.tool_ids), 3)

        capabilities = CapabilityRegistry()
        bindings = CapabilityBindingIndex()
        self.assertEqual(capabilities.specs(), ())
        self.assertEqual(bindings.mapped_implementations(), frozenset())
        for tool_id in provider.tool_ids:
            origin = gateway.get(tool_id)[0].origin.tool_name
            self.assertEqual(bindings.for_implementation("n8n", origin), ())

    def test_capability_can_exist_without_a_binding(self):
        spec = CapabilitySpec(
            id="communication.email.send",
            description="Send an authorized email",
            executor_kind="tool",
            required_authority="communicate",
            side_effect_class="external_effect",
            confirmation="required",
            verifier_id="core.receipt",
            side_effects=("external_email",),
            idempotent=False,
        )
        self.assertEqual(spec.confirmation, "required")
        self.assertEqual(spec.effective_side_effect_class, "external_effect")
        self.assertFalse(hasattr(spec, "bindings"))

        registry = CapabilityRegistry()
        registry.register(spec, lambda request: CapabilityOutcome("fail", error="unbound"))
        self.assertEqual(registry.get("communication.email.send").spec.id, spec.id)
        self.assertEqual(CapabilityBindingIndex().for_capability(spec.id), ())

    def test_provider_tool_names_are_not_required_for_capability_identity(self):
        spec = _execute_spec()
        self.assertEqual(spec.id, "automation.workflow.execute")
        dumped = spec.id + spec.description + spec.required_authority + spec.confirmation
        self.assertNotIn("n8n", dumped)
        self.assertNotIn("execute_workflow", dumped)
        self.assertNotIn("mcp", dumped)

    def test_binding_does_not_grant_authority(self):
        spec = _execute_spec()
        self.assertEqual(spec.required_authority, "execute_external")
        bindings = CapabilityBindingIndex()
        bindings.register(
            CapabilityBinding(
                capability_id=spec.id,
                provider="n8n",
                implementation="execute_workflow",
                version="1",
            )
        )
        self.assertEqual(spec.required_authority, "execute_external")
        self.assertEqual(bindings.for_capability(spec.id)[0].provider, "n8n")
        self.assertNotEqual(spec.required_authority, "read")

    def test_multiple_providers_can_bind_to_one_capability(self):
        spec = _execute_spec()
        bindings = CapabilityBindingIndex()
        bindings.register(CapabilityBinding(spec.id, "n8n", "execute_workflow", "1"))
        bindings.register(CapabilityBinding(spec.id, "temporal", "run_workflow", "1"))
        self.assertEqual(
            {(row.provider, row.implementation) for row in bindings.for_capability(spec.id)},
            {("n8n", "execute_workflow"), ("temporal", "run_workflow")},
        )
        self.assertEqual(
            [row.capability_id for row in bindings.for_implementation("temporal", "run_workflow")],
            [spec.id],
        )

    def test_replacing_n8n_binding_does_not_change_capability_identity(self):
        spec = _execute_spec()
        bindings = CapabilityBindingIndex()
        bindings.register(CapabilityBinding(spec.id, "n8n", "execute_workflow", "1"))
        replaced = CapabilityBindingIndex()
        replaced.register(CapabilityBinding(spec.id, "temporal", "run_workflow", "1"))
        self.assertEqual(spec.id, "automation.workflow.execute")
        self.assertEqual(spec.required_authority, "execute_external")
        self.assertEqual(spec.confirmation, "required")
        self.assertEqual(replaced.mapped_implementations(), frozenset({("temporal", "run_workflow")}))
        self.assertEqual(bindings.mapped_implementations(), frozenset({("n8n", "execute_workflow")}))

    def test_unmapped_mcp_tool_is_invisible_to_capability_consumers(self):
        gateway = ToolGateway()
        status = N8NMCPProvider(
            N8NMCPConfig(enabled=True),
            environ={"ATLAS_N8N_MCP_TOKEN": "token"},
            client_factory=lambda url, **kwargs: FakeMCP(),
        ).connect(gateway)
        self.assertTrue(status.available)
        gateway.get("mcp.n8n.list_credentials")
        gateway.get("mcp.n8n.update_workflow")

        registry = CapabilityRegistry()
        registry.register(_execute_spec(), lambda request: CapabilityOutcome("fail", error="unbound"))
        bindings = CapabilityBindingIndex()
        bindings.register(CapabilityBinding("automation.workflow.execute", "n8n", "execute_workflow"))

        self.assertEqual(bindings.mapped_implementations(), frozenset({("n8n", "execute_workflow")}))
        self.assertEqual(bindings.for_implementation("n8n", "list_credentials"), ())
        self.assertEqual(bindings.for_implementation("n8n", "update_workflow"), ())
        self.assertEqual(
            {row.implementation for row in bindings.for_capability("automation.workflow.execute")},
            {"execute_workflow"},
        )

    def test_duplicate_bindings_are_rejected(self):
        bindings = CapabilityBindingIndex()
        bindings.register(CapabilityBinding("automation.workflow.execute", "n8n", "execute_workflow"))
        with self.assertRaises(ValueError):
            bindings.register(CapabilityBinding("automation.workflow.execute", "n8n", "execute_workflow"))


if __name__ == "__main__":
    unittest.main()
