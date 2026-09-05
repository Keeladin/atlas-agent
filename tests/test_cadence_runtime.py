from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution
from atlas_core.provenance import InvocationProvenance
from atlas_api.compose import build_runtime


def _runtime(tmp_path):
    rt = build_runtime(tmp_path / "instance")
    owner = rt.identities.current_owner().principal_id
    calls: list[dict] = []

    def execute(payload):
        calls.append(dict(payload))
        return ActionResult(True, {"ran": True}, {"ok": True})

    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition("test.noop", "Test step.", "run", "internal", {"type": "object", "properties": {}, "additionalProperties": False}),
        lambda payload: ScopeResolution("atlas/test", dict(payload), "Test step"), execute,
        metadata={"scope_hint": "atlas/test"},
    ))
    rt.policy_store.set(principal_id=owner, scope="atlas/test", operation="run", decision="YES")
    return rt, owner, calls


def _steps():
    return [{"capability_id": "test.noop", "input": {}}]


def _prov(owner: str) -> InvocationProvenance:
    return InvocationProvenance(owner, "human", "control")


def test_run_now_executes_a_disabled_future_cadence_without_touching_the_schedule(tmp_path):
    rt, owner, calls = _runtime(tmp_path)
    cadence = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "daily", "hour": 8, "minute": 0},
                                steps=_steps(), owner_principal_id=owner)
    rt.cadence_store.set_enabled(cadence.cadence_id, False, None)
    disabled = rt.cadence_store.get(cadence.cadence_id)
    assert disabled.enabled is False and disabled.next_run_at is None

    result = rt.cadence.run_now(cadence.cadence_id)

    assert result["kind"] == "work_template" and result["trigger"] == "manual"
    assert calls == [{}]
    after = rt.cadence_store.get(cadence.cadence_id)
    # disabled means "do not fire automatically", not "may never execute"
    assert after.enabled is False
    assert after.next_run_at is None
    assert after.last_work_id == result["work_id"]
    assert after.last_run_at is not None


def test_run_now_does_not_advance_a_scheduled_next_run(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    cadence = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "interval", "minutes": 60},
                                steps=_steps(), owner_principal_id=owner)
    scheduled = rt.cadence_store.get(cadence.cadence_id).next_run_at

    rt.cadence.run_now(cadence.cadence_id)

    assert rt.cadence_store.get(cadence.cadence_id).next_run_at == scheduled


def test_update_merges_the_patch_and_validates_the_whole_candidate(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    cadence = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "daily", "hour": 8, "minute": 0},
                                steps=_steps(), owner_principal_id=owner)

    renamed = rt.cadence.update(cadence.cadence_id, {"name": "Morning brief"})
    assert renamed.name == "Morning brief"
    # a key absent from the patch keeps its existing value
    assert renamed.objective == "Prepare the brief"
    assert renamed.steps == _steps()

    # an empty steps list is a real supplied value, and fails the work_template invariant
    with pytest.raises(ValueError, match="work_template cadence requires steps"):
        rt.cadence.update(cadence.cadence_id, {"steps": []})

    # the merged candidate is what gets validated: kind alone is well-typed but incomplete
    with pytest.raises(ValueError, match="intake_sweep cadence requires intake_root_id"):
        rt.cadence.update(cadence.cadence_id, {"kind": "intake_sweep"})

    with pytest.raises(ValueError, match="unsupported cadence fields"):
        rt.cadence.update(cadence.cadence_id, {"cadence_id": "other"})


def test_update_recomputes_next_run_only_for_a_schedule_change_on_an_enabled_cadence(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    cadence = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "daily", "hour": 8, "minute": 0},
                                steps=_steps(), owner_principal_id=owner)
    original = rt.cadence_store.get(cadence.cadence_id).next_run_at

    assert rt.cadence.update(cadence.cadence_id, {"name": "Renamed"}).next_run_at == original

    moved = rt.cadence.update(cadence.cadence_id, {"schedule": {"kind": "daily", "hour": 21, "minute": 30}})
    assert moved.next_run_at != original
    assert datetime.fromisoformat(moved.next_run_at) > datetime.now(timezone.utc) - timedelta(days=1)

    rt.cadence_store.set_enabled(cadence.cadence_id, False, None)
    still_disabled = rt.cadence.update(cadence.cadence_id, {"schedule": {"kind": "daily", "hour": 6, "minute": 0}})
    assert still_disabled.next_run_at is None


def test_chat_facing_update_refuses_an_intake_sweep_target_before_mutating(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    sweep = rt.cadence.create(name="Sweep", objective="Watch the folder", schedule={"kind": "interval", "minutes": 30},
                              steps=[], owner_principal_id=owner, kind="intake_sweep", intake_root_id="root_1")

    with pytest.raises(ValueError, match="cadence_update_not_supported"):
        rt.cadence.update_work_template(sweep.cadence_id, {"name": "Renamed sweep"})

    assert rt.cadence_store.get(sweep.cadence_id).name == "Sweep"
    # the generic primitive still supports it for a future controlled surface
    assert rt.cadence.update(sweep.cadence_id, {"name": "Renamed sweep"}).name == "Renamed sweep"


def test_cadence_capabilities_are_work_template_only_for_authoring(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    create_schema = rt.capabilities_registry.get("cadence.create").definition.input_schema
    update_schema = rt.capabilities_registry.get("cadence.update").definition.input_schema

    for schema in (create_schema, update_schema):
        assert schema["additionalProperties"] is False
        assert "kind" not in schema["properties"]
        assert "root_id" not in schema["properties"]
        assert "intake_root_id" not in schema["properties"]
        assert "max_candidates" not in schema["properties"]
    assert create_schema["properties"]["steps"]["minItems"] == 1
    assert update_schema["properties"]["steps"]["minItems"] == 1
    assert "steps" in create_schema["required"]

    created = rt.capabilities.invoke("cadence.create", {
        "name": "Brief", "objective": "Prepare the brief",
        "schedule": {"kind": "daily", "hour": 8, "minute": 0}, "steps": _steps(),
    }, provenance=_prov(owner))
    assert created.status == "succeeded"
    assert created.result["kind"] == "work_template"

    # the schema rejects intake-sweep authoring before an occurrence is ever created
    with pytest.raises(ValueError, match="additional properties are not allowed"):
        rt.capabilities.invoke("cadence.create", {
            "name": "Sweep", "objective": "Watch", "schedule": {"kind": "interval", "minutes": 30},
            "steps": _steps(), "kind": "intake_sweep", "root_id": "root_1",
        }, provenance=_prov(owner))


def test_cadence_read_and_run_capabilities_cover_both_kinds(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    work_template = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "daily", "hour": 8, "minute": 0},
                                      steps=_steps(), owner_principal_id=owner)
    sweep = rt.cadence.create(name="Sweep", objective="Watch the folder", schedule={"kind": "interval", "minutes": 30},
                              steps=[], owner_principal_id=owner, kind="intake_sweep", intake_root_id="root_1")

    listed = rt.capabilities.invoke("cadence.list", {}, provenance=_prov(owner))
    assert listed.status == "succeeded"
    assert {row["cadence_id"] for row in listed.result} == {work_template.cadence_id, sweep.cadence_id}

    narrowed = rt.capabilities.invoke("cadence.list", {"query": "brief"}, provenance=_prov(owner))
    assert [row["cadence_id"] for row in narrowed.result] == [work_template.cadence_id]

    fetched = rt.capabilities.invoke("cadence.get", {"cadence_id": sweep.cadence_id}, provenance=_prov(owner))
    assert fetched.status == "succeeded" and fetched.result["kind"] == "intake_sweep"

    ran = rt.capabilities.invoke("cadence.run_now", {"cadence_id": work_template.cadence_id}, provenance=_prov(owner))
    assert ran.status == "succeeded" and ran.result["trigger"] == "manual"


def test_cadence_capabilities_resolve_owner_policy(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    rt.policy_store.set(principal_id=owner, scope="atlas/cadence", operation="list", decision="NO")
    blocked = rt.capabilities.invoke("cadence.list", {}, provenance=_prov(owner))
    assert blocked.status == "blocked"

    rt.policy_store.set(principal_id=owner, scope="atlas/cadence", operation="list", decision="YES")
    assert rt.capabilities.invoke("cadence.list", {}, provenance=_prov(owner)).status == "succeeded"


def test_work_is_linked_to_its_cadence_for_run_history(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    cadence = rt.cadence.create(name="Brief", objective="Prepare the brief", schedule={"kind": "daily", "hour": 8, "minute": 0},
                                steps=_steps(), owner_principal_id=owner)
    other = rt.work.create("Unrelated", _steps(), owner_principal_id=owner)

    first = rt.cadence.run_now(cadence.cadence_id)["work_id"]
    second = rt.cadence.run_now(cadence.cadence_id)["work_id"]

    scoped = rt.work_store.list(cadence_id=cadence.cadence_id)
    assert {item.work_id for item in scoped} == {first, second}
    assert all(item.source_cadence_id == cadence.cadence_id for item in scoped)
    assert rt.work_store.get(other.work_id).source_cadence_id is None
    assert other.work_id in {item.work_id for item in rt.work_store.list()}


def test_existing_cadence_work_is_not_backfilled_after_clean_schema_cutover(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    work = rt.work.create("Current run", _steps(), owner_principal_id=owner, metadata={"cadence_id": "cadence_current"})
    with rt.work_store._db() as db:
        db.execute("UPDATE work_items SET source_cadence_id=NULL WHERE work_id=?", (work.work_id,))
    rt.work_store.initialize()
    assert rt.work_store.get(work.work_id).source_cadence_id is None

def test_work_read_capabilities_expose_steps_and_recorded_output(tmp_path):
    rt, owner, _ = _runtime(tmp_path)
    work = rt.work.create("Read me back", _steps(), owner_principal_id=owner)
    rt.work.run(work.work_id)

    fetched = rt.capabilities.invoke("work.get", {"work_id": work.work_id}, provenance=_prov(owner))
    assert fetched.status == "succeeded"
    assert fetched.result["status"] == "completed"
    assert fetched.result["steps"][0]["output"] == {"ran": True}

    listed = rt.capabilities.invoke("work.list", {"query": "read me"}, provenance=_prov(owner))
    assert [row["work_id"] for row in listed.result] == [work.work_id]

    missing = rt.capabilities.invoke("work.get", {"work_id": "work_missing"}, provenance=_prov(owner))
    assert missing.status == "failed" and missing.error_code == "work_unknown"
