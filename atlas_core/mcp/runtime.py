from __future__ import annotations
from datetime import datetime,timezone
from typing import Any
import json
import logging
from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution
from atlas_core.mcp_http import StreamableHTTPMCPClient
from atlas_core.mcp_stdio import StdioMCPClient
from atlas_core.secrets import CredentialStore
from .models import MCPServer,MCPTool
from .store import MCPServerStore

logger=logging.getLogger(__name__)

def _safe_id(value:str)->str:return ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in value)

def _effect(annotations:dict[str,Any])->str:
    if annotations.get("readOnlyHint") is True:return "none"
    if annotations.get("destructiveHint") is True:return "destructive"
    return "external"

class MCPRuntime:
    """Runtime-managed generic MCP inventory. Discovery exposes all advertised tools."""
    def __init__(self,store:MCPServerStore,secrets:CredentialStore,registry:CapabilityRegistry)->None:
        self.store=store;self.secrets=secrets;self.registry=registry
        self.tools:dict[str,tuple[MCPServer,MCPTool]]={}
        self._stdio_clients:dict[str,tuple[tuple[Any,...],StdioMCPClient]]={}
    def public_state(self)->tuple[dict[str,Any],...]:
        counts={s.server_id:sum(1 for server,_ in self.tools.values() if server.server_id==s.server_id) for s in self.store.all()}
        return tuple({**s.public(),"discovered_tool_count":counts.get(s.server_id,0)} for s in self.store.all())
    def refresh_all(self)->None:
        for server in self.store.all():
            if server.enabled:
                try:self.refresh(server.server_id)
                except Exception:logger.warning("MCP refresh failed for %s; retaining prior inventory when available",server.server_id,exc_info=True)
    def refresh(self,server_id:str)->tuple[MCPTool,...]:
        server=self.store.get(server_id);prefix=f"mcp.{_safe_id(server.server_id)}."
        if not server.enabled:
            self.registry.unregister_prefix(prefix);self.tools={k:v for k,v in self.tools.items() if v[0].server_id!=server_id}
            self._close_stdio_client(server_id);self.store.set_discovery(server_id,error="disabled");return ()
        try:
            client=self._client(server);raw=client.list_tools();result=[];pending=[];seen=set()
            for item in raw:
                name=str(item.get("name") or "").strip()
                if not name:continue
                description=str(item.get("description") or f"MCP tool {name}").strip();schema=item.get("inputSchema") if isinstance(item.get("inputSchema"),dict) else {};annotations=item.get("annotations") if isinstance(item.get("annotations"),dict) else {}
                _validate_advertised_schema(schema)
                tool=MCPTool(server.server_id,name,description,schema,annotations);cid=prefix+_safe_id(name)
                if cid in seen:raise RuntimeError(f"MCP tool id collision after normalization: {cid}")
                seen.add(cid);scope=f"mcp/{server.server_id}/tool/{name}"
                def resolver(payload,*,_scope=scope,_name=name):return ScopeResolution(_scope,dict(payload),f"Invoke MCP tool {_name}")
                def executor(payload,*,_sid=server.server_id,_name=name):return self._call(_sid,_name,payload)
                reg=CapabilityRegistration(CapabilityDefinition(cid,description,"invoke",_effect(annotations),dict(schema),source="n8n" if server.kind=="n8n" else "mcp",tags=("mcp",server.kind)),resolver,executor,availability=lambda _sid=server.server_id:self._availability(_sid),metadata={"scope_hint":scope,"server_id":server.server_id,"tool_name":name,"annotations":annotations})
                pending.append((cid,server,tool,reg));result.append(tool)
            self.registry.unregister_prefix(prefix);self.tools={k:v for k,v in self.tools.items() if v[0].server_id!=server_id}
            for cid,srv,tool,reg in pending:self.tools[cid]=(srv,tool);self.registry.register(reg)
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
        if s.last_error:
            if any(server.server_id==server_id for server,_ in self.tools.values()):return True,f"stale_inventory:{s.last_error}"
            return False,s.last_error
        return True,"available"
    def _client(self,server:MCPServer):
        if server.transport=="stdio":
            if not server.command:raise RuntimeError("stdio MCP server has no command")
            fingerprint=(server.command,server.args,server.cwd,server.timeout_sec,server.read_timeout_sec)
            cached=self._stdio_clients.get(server.server_id)
            if cached and cached[0]==fingerprint:return cached[1]
            self._close_stdio_client(server.server_id)
            client=StdioMCPClient(server.command,args=server.args,cwd=server.cwd,timeout_sec=server.timeout_sec,read_timeout_sec=server.read_timeout_sec)
            self._stdio_clients[server.server_id]=(fingerprint,client);return client
        headers={}
        if server.credential_ref:
            secret=self.secrets.retrieve(server.credential_ref);token=str(secret.get("token") or secret.get("api_key") or "").strip()
            if not token:raise RuntimeError("MCP credential has no token")
            headers["Authorization"]=f"Bearer {token}"
        if not server.url:raise RuntimeError("Streamable HTTP MCP server has no URL")
        return StreamableHTTPMCPClient(server.url,headers=headers,timeout_sec=server.timeout_sec,read_timeout_sec=server.read_timeout_sec)
    def _call(self,server_id:str,name:str,payload:dict[str,Any])->ActionResult:
        server=self.store.get(server_id)
        try:
            raw=self._client(server).call_tool(name,payload);is_error=bool(raw.get("isError")) if isinstance(raw,dict) else False
            detail=_mcp_error_detail(raw) if is_error else None
            return ActionResult(not is_error,output=raw,receipt={"ok":not is_error,"transport":"mcp","server_id":server_id,"tool":name,"timestamp":datetime.now(timezone.utc).isoformat()},error_code="mcp_tool_error" if is_error else None,error=detail)
        except Exception as exc:
            if server.transport=="stdio":self._close_stdio_client(server_id)
            return ActionResult(False,receipt={"ok":False,"transport":"mcp","server_id":server_id,"tool":name},error_code="mcp_unavailable",error=str(exc))
    def _close_stdio_client(self,server_id:str)->None:
        cached=self._stdio_clients.pop(server_id,None)
        if cached:
            try:cached[1].close()
            except Exception:logger.warning("failed to close stdio MCP client %s",server_id,exc_info=True)


def _mcp_error_detail(raw:Any)->str:
    if isinstance(raw,dict):
        structured=raw.get("structuredContent")
        if isinstance(structured,dict) and structured.get("error"):return str(structured["error"])[:4000]
        for item in raw.get("content") or []:
            if isinstance(item,dict) and item.get("text"):return str(item["text"])[:4000]
    return "MCP tool reported an error"

def _validate_advertised_schema(schema:dict[str,Any])->None:
    if len(json.dumps(schema,ensure_ascii=False,default=str))>100000:raise RuntimeError("MCP tool schema exceeds 100000 characters")
    def walk(value:Any,depth:int)->None:
        if depth>32:raise RuntimeError("MCP tool schema exceeds maximum nesting depth")
        if isinstance(value,dict):
            for child in value.values():walk(child,depth+1)
        elif isinstance(value,list):
            for child in value:walk(child,depth+1)
    walk(schema,0)
