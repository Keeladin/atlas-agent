from __future__ import annotations

import pytest

from atlas_api.compose import build_runtime
from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition, CapabilityRegistration, ScopeResolution


def _cap(rt, owner, cid, operation, output):
    rt.capabilities_registry.register(CapabilityRegistration(
        CapabilityDefinition(cid, cid, operation, "internal", {"type": "object", "properties": {}, "additionalProperties": False}),
        lambda payload: ScopeResolution("atlas/test", dict(payload), cid),
        lambda payload: ActionResult(True, output, {"ok": True}),
        metadata={"scope_hint": "atlas/test"},
    ))
    rt.policy_store.set(principal_id=owner, scope="atlas/test", operation=operation, decision="YES")


def test_revision_preserves_completed_prefix_and_records_old_route(tmp_path):
    rt=build_runtime(tmp_path/"instance");owner=rt.identities.current_owner().principal_id
    _cap(rt,owner,"test.one","one",{"value":"one"});_cap(rt,owner,"test.two","two",{"value":"two"});_cap(rt,owner,"test.alt","alt",{"value":"alt"})
    work=rt.work.create("Adapt",[{"capability_id":"test.one","description":"First","input":{}},{"capability_id":"test.two","description":"Old second","input":{}}],owner_principal_id=owner)
    first=rt.work_store.steps(work.work_id)[0]
    rt.work_store.claim_step(first.step_id);occ=rt.capabilities.invoke("test.one",{},provenance=__import__('atlas_core.provenance',fromlist=['InvocationProvenance']).InvocationProvenance(owner,"human","work"),work_id=work.work_id,step_id=first.step_id)
    rt.work_store.set_step(first.step_id,status="completed",occurrence_id=occ.occurrence_id,output=occ.result)
    revised=rt.work.revise(work.work_id,base_revision=1,from_ordinal=2,replacement_steps=[{"capability_id":"test.alt","description":"Better route","input":{}}],change_intent="Use better evidence",reason="Old route insufficient",unchanged_goal="Same objective",expected_impact="One replacement step")
    assert revised["revision"]==2
    assert [s["description"] for s in revised["steps"]]==["First","Better route"]
    assert revised["steps"][0]["status"]=="completed"
    assert revised["adaptations"][0]["before_steps"][0]["description"]=="Old second"
    assert rt.work.run(work.work_id)["status"]=="completed"


def test_revision_rejects_completed_or_running_prefix_and_stale_revision(tmp_path):
    rt=build_runtime(tmp_path/"instance");owner=rt.identities.current_owner().principal_id
    _cap(rt,owner,"test.one","one",{"ok":True})
    work=rt.work.create("Guard",[{"capability_id":"test.one","input":{}}],owner_principal_id=owner)
    step=rt.work_store.steps(work.work_id)[0]
    assert rt.work_store.claim_step(step.step_id)
    with pytest.raises(ValueError,match="running"):
        rt.work.revise(work.work_id,base_revision=1,from_ordinal=1,replacement_steps=[{"capability_id":"test.one","input":{}}],change_intent="change",reason="reason",unchanged_goal="goal",expected_impact="impact")
