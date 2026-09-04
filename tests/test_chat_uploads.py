from __future__ import annotations

import json

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance
from tests.test_chat_runtime import SequenceProvider


def test_owner_upload_crosses_capability_and_establishes_artifact(tmp_path):
    rt=build_runtime(tmp_path/"instance");owner=rt.identities.current_owner().principal_id
    staged=rt.uploads.stage(b"hello atlas",filename="notes.txt",media_type="text/plain")
    occurrence=rt.capabilities.invoke("artifacts.accept_upload",staged,provenance=InvocationProvenance(owner,"human","chat"))
    assert occurrence.status=="succeeded",occurrence.error
    artifact=rt.artifact_store.get(occurrence.result["artifact_id"])
    facet=artifact["facets"][0]
    assert facet["root_id"]=="atlas-owner-uploads"
    assert (rt.instance_root/"owner-uploads"/facet["relative_path"]).read_bytes()==b"hello atlas"
    assert not list((rt.instance_root/"upload-staging").glob("*.part"))
    assert "artifacts.accept_upload" not in {row["id"] for row in rt.chat.search_capabilities("upload a file",limit=100)}


def test_chat_turn_carries_exact_attached_artifact_into_composition_context(tmp_path):
    rt=build_runtime(tmp_path/"instance");owner=rt.identities.current_owner().principal_id
    staged=rt.uploads.stage(b"manual",filename="loader-manual.txt",media_type="text/plain")
    uploaded=rt.capabilities.invoke("artifacts.accept_upload",staged,provenance=InvocationProvenance(owner,"human","chat"))
    artifact_id=uploaded.result["artifact_id"];cid=rt.chat_store.create_conversation("Attachment")["conversation_id"]
    rt.chat.provider=SequenceProvider('{"kind":"reply","reply":"I can use the attached manual."}')
    result=rt.chat.send(cid,"Index this for future reference",principal_id=owner,defer_capture=True,attachments=[artifact_id])
    user=rt.chat_store.turns(cid)[0]
    assert user["metadata"]["attachments"][0]["artifact_id"]==artifact_id
    prompt=json.loads(rt.chat.provider.requests[0].input)
    attached=next(row for row in prompt["relevant_durable_context"] if row["kind"]=="attached_artifact")
    assert attached["reference"]["artifact_id"]==artifact_id
    assert result["turn"]["content"]=="I can use the attached manual."