from __future__ import annotations

from datetime import datetime, timezone

from atlas_api.compose import build_runtime
from atlas_core.provenance import InvocationProvenance


class FakeMailMCP:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "mail_connection_attest":
            return {
                "isError": False,
                "structuredContent": {
                    "connection_ref": arguments["connection_ref"],
                    "provider": "google",
                    "provider_subject_id": "subject-1",
                    "canonical_address": "owner@example.com",
                    "granted_operations": ["mail.read", "mail.send", "mail.modify"],
                    "attested_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        if name == "mail_inbox_count":
            return {"isError": False, "structuredContent": {"count": 4}}
        if name == "mail_messages_send":
            return {"isError": False, "structuredContent": {"message_id": "msg-1", "sent": True}}
        return {"isError": False, "structuredContent": {"ok": True}}


def test_semantic_mail_uses_custody_plus_one_runtime_policy(tmp_path, monkeypatch):
    rt = build_runtime(tmp_path / "instance")
    fake = FakeMailMCP()
    monkeypatch.setattr(rt.mcp, "_client", lambda server: fake)
    rt.mcp_store.put(
        server_id="mail-n8n",
        display_name="Mail n8n",
        kind="n8n",
        url="http://127.0.0.1:5678/mcp",
    )
    owner = rt.identities.current_owner().principal_id
    attested = rt.mail.attest_connection(
        server_id="mail-n8n",
        connection_ref="google-main",
        owner_principal_id=owner,
    )
    connection_id = attested["connection"]["connection_id"]
    rt.seed_policy()
    provenance = InvocationProvenance(owner, "human", "chat")

    unread = rt.capabilities.invoke("mail.inbox.count", {}, provenance=provenance)
    assert unread.status == "succeeded"
    assert unread.result["count"] == 4
    assert unread.scope == f"mail/{connection_id}"

    send = rt.capabilities.invoke(
        "mail.messages.send",
        {"to": "friend@example.com", "subject": "Hello", "body": "Hi"},
        provenance=provenance,
    )
    assert send.status == "pending_confirmation"
    send_calls = [call for call in fake.calls if call[0] == "mail_messages_send"]
    assert send_calls == []

    confirmed = rt.actions.confirm(send.occurrence_id, principal_id=owner)
    assert confirmed.status == "succeeded"
    send_calls = [call for call in fake.calls if call[0] == "mail_messages_send"]
    assert len(send_calls) == 1
    assert send_calls[0][1]["connection_ref"] == "google-main"
    assert send_calls[0][1]["to"] == "friend@example.com"

    assert not hasattr(attested["connection"], "authority_grant_id")
    assert "authority_grant" not in str(attested).casefold()
