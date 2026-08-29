from __future__ import annotations
from datetime import datetime,timezone
from typing import Any
from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution
from atlas_core.mcp_http import StreamableHTTPMCPClient
from atlas_core.secrets import CredentialStore
from .models import MCPServer,MCPTool
from .store import MCPServerStore

def _safe_id(value:str)->str:return ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in value)

def _effect(annotations:dict[str,Any])->str:
    if annotations.get("readOnlyHint") is True:return "none"
    if annotations.get("destructiveHint") is True:return "destructive"
    return "external"

class MCPRuntime:
    """Runtime-managed generic MCP inventory. Discovery exposes all advertised tools."""
    def __init__(self,store:MCPServerStore,secrets:CredentialStore,registry:CapabilityRegistry)->None:self.store=store;self.secrets=secrets;self.registry=registry;self.tools:dict[str,tuple[MCPServer,MCPTool]]={}
    def public_state(self)->tuple[dict[str,Any],...]:
        counts={s.server_id:sum(1 for server,_ in self.tools.values() if server.server_id==s.server_id) for s in self.store.all()}
        return tuple({**s.public(),"discovered_tool_count":counts.get(s.server_id,0)} for s in self.store.all())
    def refresh_all(self)->None:
        for server in self.store.all():
            if server.enabled:
                try:self.refresh(server.server_id)
                except Exception:pass
    def refresh(self,server_id:str)->tuple[MCPTool,...]:
        server=self.store.get(server_id);prefix=f"mcp.{_safe_id(server.server_id)}.";self.registry.unregister_prefix(prefix);self.tools={k:v for k,v in self.tools.items() if v[0].server_id!=server_id}
        if not server.enabled:self.store.set_discovery(server_id,error="disabled");return ()
        try:
            client=self._client(server);raw=client.list_tools();result=[]
            for item in raw:
                name=str(item.get("name") or "").strip()
                if not name:continue
                description=str(item.get("description") or f"MCP tool {name}").strip();schema=item.get("inputSchema") if isinstance(item.get("inputSchema"),dict) else {};annotations=item.get("annotations") if isinstance(item.get("annotations"),dict) else {}
                tool=MCPTool(server.server_id,name,description,schema,annotations);cid=prefix+_safe_id(name);self.tools[cid]=(server,tool)
                scope=f"mcp/{server.server_id}/tool/{name}"
                def resolver(payload,*,_scope=scope,_name=name):return ScopeResolution(_scope,dict(payload),f"Invoke MCP tool {_name}")
                def executor(payload,*,_sid=server.server_id,_name=name):return self._call(_sid,_name,payload)
                self.registry.register(CapabilityRegistration(CapabilityDefinition(cid,description,"invoke",_effect(annotations),dict(schema),source="n8n" if server.kind=="n8n" else "mcp",tags=("mcp",server.kind)),resolver,executor,availability=lambda _sid=server.server_id:self._availability(_sid),metadata={"scope_hint":scope,"server_id":server.server_id,"tool_name":name,"annotations":annotations}),replace=True);result.append(tool)
            self.store.set_discovery(server_id,error=None);return tuple(result)
        except Exception as exc:
            self.store.set_discovery(server_id,error=str(exc));raise
    def call_tool(self,server_id:str,name:str,payload:dict[str,Any])->ActionResult:
        """Technical provider call. Callers must already have crossed Atlas policy."""
        return self._call(server_id,name,payload)
    def raw_tools(self,server_id:str)->tuple[MCPTool,...]:
        return tuple(tool for server,tool in self.tools.values() if server.server_id==server_id)
    def _availability(self,server_id:str)->tuple[bool,str]:
        try:s=self.store.get(server_id)
        except KeyError:return False,"server_removed"
        if not s.enabled:return False,"server_disabled"
        if s.last_error:return False,s.last_error
        return True,"available"
    def _client(self,server:MCPServer):
        headers={}
        if server.credential_ref:
            secret=self.secrets.retrieve(server.credential_ref);token=str(secret.get("token") or secret.get("api_key") or "").strip()
            if not token:raise RuntimeError("MCP credential has no token")
            headers["Authorization"]=f"Bearer {token}"
        return StreamableHTTPMCPClient(server.url,headers=headers,timeout_sec=server.timeout_sec,read_timeout_sec=server.read_timeout_sec)
    def _call(self,server_id:str,name:str,payload:dict[str,Any])->ActionResult:
        server=self.store.get(server_id)
        try:
            raw=self._client(server).call_tool(name,payload);is_error=bool(raw.get("isError")) if isinstance(raw,dict) else False
            return ActionResult(not is_error,output=raw,receipt={"ok":not is_error,"transport":"mcp","server_id":server_id,"tool":name,"timestamp":datetime.now(timezone.utc).isoformat()},error_code="mcp_tool_error" if is_error else None,error="MCP tool reported an error" if is_error else None)
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"transport":"mcp","server_id":server_id,"tool":name},error_code="mcp_unavailable",error=str(exc))
