from __future__ import annotations

import pytest

from atlas_api.compose import build_runtime
from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution
from atlas_core.work import WorkflowValidationError


def _register(rt, capability_id: str, schema: dict, *, work_composable: bool = True) -> None:
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition(capability_id, f"Test {capability_id}", "read", "none", schema),
        lambda payload: ScopeResolution("atlas/test", dict(payload), capability_id),
        lambda payload: ActionResult(True, payload, {"ok": True}),
        metadata={"scope_hint": "atlas/test", "work_composable": work_composable},
    ))


def test_work_preflight_rejects_unknown_capability_before_persistence(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    with pytest.raises(WorkflowValidationError, match="unknown capability"):
        rt.work.create("Bad plan", [{"capability_id": "missing.tool", "input": {}}], owner_principal_id=owner)
    assert rt.work_store.list() == ()


def test_work_preflight_accepts_backward_ref_as_schema_placeholder(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    _register(rt, "test.produce", {"type": "object", "properties": {}, "additionalProperties": False})
    _register(rt, "test.consume", {
        "type": "object", "required": ["value"],
        "properties": {"value": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    })
    work = rt.work.create("Reference prior output", [
        {"capability_id": "test.produce", "description": "Produce evidence", "input": {}},
        {"capability_id": "test.consume", "description": "Use the evidence", "input": {"value": {"$ref": {"step": 1, "output": "/value"}}}},
    ], owner_principal_id=owner)
    assert work.objective == "Reference prior output"


def test_work_preflight_rejects_forward_ref_and_nested_orchestration(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    _register(rt, "test.consume", {"type": "object", "properties": {"value": {"type": "string"}}, "additionalProperties": False})
    with pytest.raises(WorkflowValidationError, match="earlier step"):
        rt.work.create("Forward ref", [{"capability_id": "test.consume", "input": {"value": {"$ref": {"step": 1, "output": "/value"}}}}], owner_principal_id=owner)
    with pytest.raises(WorkflowValidationError, match="cannot be nested"):
        rt.work.create("Nested work", [{"capability_id": "work.create", "input": {"objective": "nested", "steps": []}}], owner_principal_id=owner)
