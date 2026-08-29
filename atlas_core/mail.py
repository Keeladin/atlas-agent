from __future__ import annotations

import json
from datetime import datetime,timezone
from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution
from atlas_core.identity import IdentityStore
from atlas_core.mcp import MCPRuntime

MAIL_TOOLS={
    "mail.inbox.count":"mail_inbox_count",
    "mail.messages.search":"mail_messages_search",
    "mail.messages.get":"mail_messages_get",
    "mail.threads.get":"mail_threads_get",
    "mail.drafts.create":"mail_drafts_create",
    "mail.messages.reply":"mail_messages_reply",
    "mail.messages.forward":"mail_messages_forward",
    "mail.messages.send":"mail_messages_send",
    "mail.attachments.get":"mail_attachments_get",
    "mail.messages.modify":"mail_messages_modify",
}
MAIL_OPERATIONS={
    "mail.inbox.count":"mail.read","mail.messages.search":"mail.read","mail.messages.get":"mail.read","mail.threads.get":"mail.read","mail.attachments.get":"mail.read",
    "mail.drafts.create":"mail.send","mail.messages.reply":"mail.send","mail.messages.forward":"mail.send","mail.messages.send":"mail.send",
    "mail.messages.modify":"mail.modify",
}

class MailRuntime:
    """Semantic mail over n8n MCP. n8n owns OAuth; Atlas policy owns authority."""
    def __init__(self,identities:IdentityStore,mcp:MCPRuntime,registry:CapabilityRegistry)->None:self.identities=identities;self.mcp=mcp;self.registry=registry;self._register()
    def _schema(self,capability_id:str)->dict[str,Any]:
        props={"connection_id":{"type":"string"}}
        fields={
            "mail.inbox.count":{},
            "mail.messages.search":{"query":{"type":"string"},"from":{"type":"string"},"to":{"type":"string"},"subject":{"type":"string"},"after":{"type":"string"},"before":{"type":"string"},"mailbox":{"type":"string"},"max_results":{"type":"integer","minimum":1,"maximum":100}},
            "mail.messages.get":{"message_id":{"type":"string"},"thread_id":{"type":"string"}},
            "mail.threads.get":{"thread_id":{"type":"string"},"message_id":{"type":"string"}},
            "mail.drafts.create":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"},"thread_id":{"type":"string"}},
            "mail.messages.reply":{"message_id":{"type":"string"},"thread_id":{"type":"string"},"body":{"type":"string"},"reply_all":{"type":"boolean"}},
            "mail.messages.forward":{"message_id":{"type":"string"},"to":{"type":"string"},"body":{"type":"string"}},
            "mail.messages.send":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},
            "mail.attachments.get":{"message_id":{"type":"string"},"attachment_id":{"type":"string"}},
            "mail.messages.modify":{"message_id":{"type":"string"},"thread_id":{"type":"string"},"add_labels":{"type":"array","items":{"type":"string"}},"remove_labels":{"type":"array","items":{"type":"string"}},"mark":{"type":"string","enum":["read","unread"]},"archive":{"type":"boolean"},"trash":{"type":"boolean"}},
        }[capability_id]
        props.update(fields)
        return {"type":"object","properties":props,"additionalProperties":False}
    def _register(self)->None:
        for cid,operation in MAIL_OPERATIONS.items():
            effect="none" if operation=="mail.read" else "external"
            self.registry.register(CapabilityRegistration(CapabilityDefinition(cid,cid.replace('.',' ').title(),operation,effect,self._schema(cid),source="n8n",tags=("mail","n8n")),lambda p,_cid=cid:self._scope(_cid,p),lambda p,_cid=cid:self._execute(_cid,p),availability=lambda _cid=cid:self._availability(_cid),metadata={"scope_hint":"mail"}),replace=True)
    def _mail_bindings(self):
        rows=[]
        for connection in self.identities.connections():
            if connection.status!="active":continue
            for binding in self.identities.service_bindings(connection_id=connection.connection_id):
                if binding.service=="mail" and binding.lifecycle=="connected" and binding.channel.startswith("n8n_mcp:"):rows.append((connection,binding))
        return rows
    def _select(self,connection_id:str|None,operation:str):
        rows=[row for row in self._mail_bindings() if operation in row[1].attested_operations]
        if connection_id:rows=[row for row in rows if row[0].connection_id==connection_id]
        if not rows:raise ValueError("no connected mail account attests this operation")
        if len(rows)>1:raise ValueError("mail account selection required")
        return rows[0]
    def _scope(self,cid:str,payload:dict[str,Any])->ScopeResolution:
        operation=MAIL_OPERATIONS[cid];connection,binding=self._select(str(payload.get("connection_id") or "") or None,operation);clean=dict(payload);clean["connection_id"]=connection.connection_id
        return ScopeResolution(f"mail/{connection.connection_id}",clean,self._summary(cid,clean,connection.canonical_address))
    def _summary(self,cid:str,payload:dict[str,Any],address:str)->str:
        if cid=="mail.messages.send":return f"Send email from {address} to {payload.get('to','')} with subject {payload.get('subject','')}"
        if cid=="mail.messages.reply":return f"Reply from {address} to message {payload.get('message_id') or payload.get('thread_id') or ''}"
        if cid=="mail.messages.forward":return f"Forward message from {address} to {payload.get('to','')}"
        return f"{cid} on {address}"
    def _availability(self,cid:str)->tuple[bool,str]:
        try:self._select(None,MAIL_OPERATIONS[cid]);return True,"available"
        except ValueError as exc:
            if "selection" in str(exc):return True,"multiple_accounts"
            return False,str(exc)
    def _execute(self,cid:str,payload:dict[str,Any])->ActionResult:
        try:
            connection,binding=self._select(str(payload.get("connection_id") or ""),MAIL_OPERATIONS[cid]);server_id=binding.channel.split(':',1)[1];args={k:v for k,v in payload.items() if k!="connection_id"};args["connection_ref"]=binding.dispatch_ref
            raw=self.mcp.call_tool(server_id,MAIL_TOOLS[cid],args)
            if not raw.ok:return raw
            output=_tool_json(raw.output)
            forbidden={"access_token","refresh_token","client_secret","authorization","credential_ref"}
            if any(key in output for key in forbidden):return ActionResult(False,receipt={"ok":False,"provider":"n8n","server_id":server_id},error_code="mail_result_contains_secrets",error="provider result exposed credential material")
            receipt={"ok":True,"provider":"n8n","server_id":server_id,"connection_id":connection.connection_id,"provider_subject_id":connection.provider_subject_id,"operation":MAIL_OPERATIONS[cid],"capability_id":cid,"timestamp":datetime.now(timezone.utc).isoformat()}
            return ActionResult(True,output,receipt)
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"provider":"n8n"},error_code="mail_execution_failed",error=str(exc))
    def attest_connection(self,*,server_id:str,connection_ref:str,owner_principal_id:str)->dict[str,Any]:
        result=self.mcp.call_tool(server_id,"mail_connection_attest",{"connection_ref":connection_ref})
        if not result.ok:raise RuntimeError(result.error or "mail attestation failed")
        payload=_tool_json(result.output)
        provider=str(payload.get("provider") or "google");subject=str(payload.get("provider_subject_id") or "").strip();address=str(payload.get("canonical_address") or "").strip();returned=str(payload.get("connection_ref") or connection_ref).strip()
        if not subject or not address or returned!=connection_ref:raise ValueError("mail attestation is missing or mismatched identity")
        operations=tuple(str(x) for x in (payload.get("granted_operations") or payload.get("attested_operations") or []) if str(x) in {"mail.read","mail.send","mail.modify"})
        if not operations:raise ValueError("mail attestation contains no supported operations")
        connection,_created=self.identities.put_connection(provider_id=provider,provider_subject_id=subject,canonical_address=address,display_name=address,owner_principal_id=owner_principal_id,identity_profile_version="1",provider_metadata={"attested_via":server_id}),True
        binding=self.identities.put_service_binding(connection_id=connection.connection_id,service="mail",channel=f"n8n_mcp:{server_id}",dispatch_ref=connection_ref,attested_operations=operations,service_profile_version="1",health="ready",lifecycle="connected",attested_at=str(payload.get("attested_at") or datetime.now(timezone.utc).isoformat()))
        return {"connection":connection.as_dict(),"service_binding":binding.as_dict()}

def _tool_json(raw:Any)->dict[str,Any]:
    if isinstance(raw,dict):
        if isinstance(raw.get("structuredContent"),dict):return dict(raw["structuredContent"])
        content=raw.get("content")
        if isinstance(content,list):
            for item in content:
                if isinstance(item,dict) and item.get("type")=="text":
                    try:
                        parsed=json.loads(str(item.get("text") or ""))
                        if isinstance(parsed,dict):return parsed
                    except json.JSONDecodeError:continue
        return dict(raw)
    raise ValueError("MCP tool returned no object payload")
