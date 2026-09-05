from __future__ import annotations

from atlas_api.compose import build_runtime
from atlas_core.actions import ActionRequest
from atlas_core.provenance import InvocationProvenance


def test_restart_recovers_claimed_step_without_occurrence_to_queue(tmp_path):
    root=tmp_path/"instance";rt=build_runtime(root);owner=rt.identities.current_owner().principal_id
    work=rt.work.create("Recover",[{"capability_id":"memory.search","description":"Search memory","input":{"query":"x"}}],owner_principal_id=owner)
    step=rt.work_store.steps(work.work_id)[0];assert rt.work_store.claim_step(step.step_id)
    rt2=build_runtime(root);detail=rt2.work.detail(work.work_id)
    assert detail["steps"][0]["status"]=="queued"
    assert "restart" in (detail["steps"][0]["error"] or "")


def test_restart_marks_abandoned_action_uncertain_and_work_waiting(tmp_path):
    root=tmp_path/"instance";rt=build_runtime(root);owner=rt.identities.current_owner().principal_id
    work=rt.work.create("Uncertain",[{"capability_id":"memory.search","description":"Search memory","input":{"query":"x"}}],owner_principal_id=owner)
    step=rt.work_store.steps(work.work_id)[0];assert rt.work_store.claim_step(step.step_id)
    request=ActionRequest("memory.search","search","atlas/memory",{"query":"x"},InvocationProvenance(owner,"human","work"),work_id=work.work_id,step_id=step.step_id)
    occurrence=rt.actions_store.create(request,decision="YES",revision=rt.policy_store.revision(),event_id=None,status="executing")
    rt.work_store.set_step(step.step_id,status="running",occurrence_id=occurrence.occurrence_id)
    rt2=build_runtime(root);detail=rt2.work.detail(work.work_id)
    assert rt2.actions_store.get(occurrence.occurrence_id).status=="uncertain"
    assert detail["status"]=="waiting"
    assert detail["steps"][0]["status"]=="waiting"


def test_restart_recovers_action_created_before_step_binding_without_replay(tmp_path):
    root=tmp_path/"instance";rt=build_runtime(root);owner=rt.identities.current_owner().principal_id
    work=rt.work.create("Bind",[{"capability_id":"memory.search","description":"Search memory","input":{"query":"x"}}],owner_principal_id=owner)
    step=rt.work_store.steps(work.work_id)[0];assert rt.work_store.claim_step(step.step_id)
    request=ActionRequest("memory.search","search","atlas/memory",{"query":"x"},InvocationProvenance(owner,"human","work"),work_id=work.work_id,step_id=step.step_id)
    occurrence=rt.actions_store.create(request,decision="YES",revision=rt.policy_store.revision(),event_id=None,status="executing")
    occurrence=rt.actions_store.transition(occurrence.occurrence_id,from_status=("executing",),to_status="succeeded",result_json='{"items":[]}',receipt_json='{"ok":true}')
    assert rt.work_store.step(step.step_id).occurrence_id is None

    rt2=build_runtime(root);detail=rt2.work.detail(work.work_id)
    assert detail["status"]=="completed"
    assert detail["steps"][0]["occurrence_id"]==occurrence.occurrence_id
    rows=rt2.actions_store.for_work_step(work.work_id,step.step_id)
    assert len(rows)==1
