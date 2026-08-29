from __future__ import annotations

import math
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from atlas_api.auth import (
    AuthService, client_key, forbidden, require_mutation_auth, require_session, unauthorized,
)


def _runtime(request: Request):
    return request.app.state.runtime


def _owner(request: Request, session):
    auth: AuthService = request.app.state.auth
    return auth.resolve_subject(session.subject)


async def _body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=status)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "atlas-api", "version": "3.0.0"})


async def auth_session(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    session = auth.session_from_request(request)
    if session is None:
        return JSONResponse({"authenticated": False})
    try:
        owner = auth.resolve_subject(session.subject)
    except Exception:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "subject": session.subject, "principal_id": owner.principal_id, "csrf_token": session.csrf_token})


async def auth_login(request: Request) -> JSONResponse:
    auth: AuthService = request.app.state.auth
    key = client_key(request)
    decision = auth.login_throttle.check(key)
    if not decision.allowed:
        retry = max(1, math.ceil(decision.retry_after_seconds))
        return JSONResponse({"error": "too many login attempts", "retry_after": retry}, status_code=429, headers={"Retry-After": str(retry)})
    try:
        body = await _body(request)
    except ValueError as exc:
        return _error(exc)
    if not auth.verify_password(str(body.get("password") or "")):
        auth.login_throttle.record_failure(key)
        return unauthorized("invalid credentials")
    auth.login_throttle.clear(key)
    session = auth.issue_session()
    response = JSONResponse({"authenticated": True, "subject": session.subject, "csrf_token": session.csrf_token})
    auth.set_session_cookie(response, session)
    return response


async def auth_logout(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    response = JSONResponse({"ok": True})
    request.app.state.auth.clear_session_cookie(response)
    return response


async def system_state(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    rt = _runtime(request)
    state = rt.public_state()
    state["host"] = {
        "status": rt.host.status().output,
        "resources": rt.host.resources().output,
        "storage": rt.host.storage().output,
    }
    return JSONResponse(state)


async def capabilities_list(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    owner = _owner(request, gate)
    return JSONResponse({"capabilities": [row.as_dict() for row in _runtime(request).capabilities.snapshot(principal_id=owner.principal_id)]})


async def capability_invoke(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await _body(request)
        owner = _owner(request, gate)
        from atlas_core.provenance import InvocationProvenance
        occurrence = _runtime(request).capabilities.invoke(
            request.path_params["capability_id"],
            body.get("input") if isinstance(body.get("input"), dict) else {},
            provenance=InvocationProvenance(owner.principal_id, "human", "control"),
        )
        return JSONResponse({"action": occurrence.public()})
    except Exception as exc:
        return _error(exc)


async def capabilities_search(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    query = request.query_params.get("q") or ""
    return JSONResponse({"capabilities": _runtime(request).chat.search_capabilities(query, limit=100)})


async def policy_list(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    rt = _runtime(request); owner = _owner(request, gate)
    rules = [rule.__dict__ for rule in rt.policy_store.latest_rules(owner.principal_id)]
    return JSONResponse({"revision": rt.policy_store.revision(), "rules": rules})


async def policy_history(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    rt = _runtime(request); owner = _owner(request, gate)
    return JSONResponse({"events": [rule.__dict__ for rule in rt.policy_store.history(owner.principal_id, limit=500)]})


async def policy_set(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        body = await _body(request); rt = _runtime(request); owner = _owner(request, gate)
        rule = rt.policy_store.set(principal_id=owner.principal_id, scope=str(body.get("scope") or ""), operation=str(body.get("operation") or ""), decision=str(body.get("decision") or ""), reason=str(body.get("reason") or "runtime control policy update") or None)
        return JSONResponse({"rule": rule.__dict__, "revision": rt.policy_store.revision()})
    except Exception as exc:
        return _error(exc)


async def actions_pending(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    owner = _owner(request, gate); rt = _runtime(request)
    return JSONResponse({"actions": [x.public() for x in rt.actions_store.pending(principal_id=owner.principal_id)]})


async def action_confirm(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        rt = _runtime(request); owner = _owner(request, gate); occurrence = rt.actions.confirm(request.path_params["occurrence_id"], principal_id=owner.principal_id)
        payload: dict[str, Any] = {"action": occurrence.public()}
        if occurrence.work_id and occurrence.status == "succeeded": payload["work"] = rt.work.run(occurrence.work_id)
        return JSONResponse(payload)
    except PermissionError as exc: return forbidden(str(exc))
    except Exception as exc: return _error(exc)


async def action_cancel(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        rt = _runtime(request); owner = _owner(request, gate); occurrence = rt.actions.cancel(request.path_params["occurrence_id"], principal_id=owner.principal_id)
        return JSONResponse({"action": occurrence.public()})
    except PermissionError as exc: return forbidden(str(exc))
    except Exception as exc: return _error(exc)


async def conversations_list(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    return JSONResponse({"conversations": list(_runtime(request).chat_store.conversations())})


async def conversation_create(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        body = await _body(request); item = _runtime(request).chat_store.create_conversation(str(body.get("title") or "New conversation")); return JSONResponse(item, status_code=201)
    except Exception as exc: return _error(exc)


async def conversation_detail(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        rt = _runtime(request); cid = request.path_params["conversation_id"]; return JSONResponse({"conversation": rt.chat_store.conversation(cid), "turns": list(rt.chat_store.turns(cid))})
    except KeyError: return _error(KeyError("conversation not found"), 404)


async def conversation_delete(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        _runtime(request).chat_store.delete_conversation(request.path_params["conversation_id"])
        return JSONResponse({"ok": True})
    except KeyError: return _error(KeyError("conversation not found"), 404)


async def conversation_send(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        body = await _body(request); owner = _owner(request, gate); result = _runtime(request).chat.send(request.path_params["conversation_id"], str(body.get("message") or "").strip(), principal_id=owner.principal_id); return JSONResponse(result)
    except Exception as exc: return _error(exc, 500)


async def work_list(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    return JSONResponse({"work": [x.as_dict() for x in _runtime(request).work_store.list()]})


async def work_create(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        body = await _body(request); owner = _owner(request, gate); rt = _runtime(request)
        from atlas_core.provenance import InvocationProvenance
        payload={"objective":str(body.get("objective") or ""),"steps":body.get("steps") or [],"run":bool(body.get("run",True))}
        occurrence=rt.capabilities.invoke("work.create",payload,provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=201 if occurrence.status=="succeeded" else 202 if occurrence.status in {"pending_confirmation","uncertain"} else 409
        return JSONResponse({"action":occurrence.public(),"work":occurrence.result if occurrence.status=="succeeded" else None},status_code=status)
    except Exception as exc: return _error(exc)


async def work_detail(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    try:return JSONResponse(_runtime(request).work.detail(request.path_params["work_id"]))
    except KeyError:return _error(KeyError("work not found"),404)


async def work_action(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        rt=_runtime(request);wid=request.path_params["work_id"];action=request.path_params["action"]
        if action=="run":result=rt.work.run(wid)
        elif action=="resume":result=rt.work.resume(wid)
        elif action=="pause":rt.work.pause(wid);result=rt.work.detail(wid)
        elif action=="cancel":rt.work_store.cancel(wid);result=rt.work.detail(wid)
        else:return _error(ValueError("unsupported work action"),404)
        return JSONResponse(result)
    except Exception as exc:return _error(exc)


async def cadence_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"cadences":[x.as_dict() for x in _runtime(request).cadence_store.list()]})


async def cadence_create(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);owner=_owner(request,gate);rt=_runtime(request)
        from atlas_core.provenance import InvocationProvenance
        payload={"name":str(body.get("name") or ""),"objective":str(body.get("objective") or ""),"schedule":body.get("schedule") or {},"steps":body.get("steps") or []}
        occurrence=rt.capabilities.invoke("cadence.create",payload,provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=201 if occurrence.status=="succeeded" else 202 if occurrence.status in {"pending_confirmation","uncertain"} else 409
        return JSONResponse({"action":occurrence.public(),"cadence":occurrence.result if occurrence.status=="succeeded" else None},status_code=status)
    except Exception as exc:return _error(exc)


async def cadence_enable(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);body=await _body(request);item=rt.cadence_store.get(request.path_params["cadence_id"]);enabled=bool(body.get("enabled",True));next_run=None if not enabled else rt.cadence.next_after(item.schedule,__import__('datetime').datetime.now(__import__('datetime').timezone.utc)).isoformat();return JSONResponse(rt.cadence_store.set_enabled(item.cadence_id,enabled,next_run).as_dict())
    except Exception as exc:return _error(exc)


async def cadence_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:_runtime(request).cadence_store.delete(request.path_params["cadence_id"]);return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def source_roots_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"roots":[x.public() for x in _runtime(request).source_roots.all()]})


async def source_root_put(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request);item=rt.source_roots.put(root_id=str(body.get("root_id") or ""),host_path=str(body.get("host_path") or ""),display_name=body.get("display_name"),provider_namespace=str(body.get("provider_namespace") or "local"),quarantine_relative_path=body.get("quarantine_relative_path",".atlas-quarantine"),enabled=bool(body.get("enabled",True)));rt.sources.reload();rt.seed_policy();return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def source_root_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);rt.source_roots.delete(request.path_params["root_id"]);rt.sources.reload();return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def knowledge_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    q=request.query_params.get("q")
    rows=_runtime(request).knowledge_store.search(q,limit=50) if q else _runtime(request).knowledge_store.recent(limit=100)
    return JSONResponse({"items":list(rows)})


async def knowledge_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:_runtime(request).knowledge_store.delete(request.path_params["item_id"]);return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def memory_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    rt=_runtime(request);owner=_owner(request,gate);q=request.query_params.get("q")
    rows=rt.memory_store.search(owner.principal_id,q,limit=100) if q else rt.memory_store.recent(owner.principal_id,limit=200,include_history=True)
    return JSONResponse({"items":list(rows)})


async def memory_detail(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate);item=rt.memory_store.get(owner.principal_id,request.path_params["item_id"])
        with rt.memory_store._db() as db:chain=rt.memory_store.chain(owner.principal_id,item["item_id"],db=db)
        return JSONResponse({"item":item,"chain":list(chain)})
    except KeyError:return _error(KeyError("memory not found"),404)


async def memory_action(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate);action=request.path_params["action"]
        if action not in {"purge","restore","retract"}:return _error(ValueError("unsupported memory action"),404)
        from atlas_core.provenance import InvocationProvenance
        occurrence=rt.capabilities.invoke(f"memory.{action}",{"item_id":request.path_params["item_id"]},provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=200 if occurrence.status=="succeeded" else 202 if occurrence.status in {"pending_confirmation","uncertain"} else 409
        return JSONResponse({"action":occurrence.public()},status_code=status)
    except Exception as exc:return _error(exc)


async def mcp_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"servers":list(_runtime(request).mcp.public_state())})


async def mcp_put(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request);sid=str(body.get("server_id") or "")
        try:
            existing=rt.mcp_store.get(sid);credential_ref=existing.credential_ref;default_transport=existing.transport
        except KeyError:
            credential_ref=None;default_transport="streamable-http"
        transport=str(body.get("transport") or default_transport)
        token=str(body.get("token") or "").strip()
        if transport=="stdio":
            if token:raise ValueError("stdio MCP servers do not use Atlas HTTP bearer tokens")
            if credential_ref:
                try:rt.credentials.disable(credential_ref)
                except Exception:pass
                credential_ref=None
        elif token:
            if credential_ref:rt.credentials.replace(credential_ref,{"token":token})
            else:credential_ref=rt.credentials.create(kind="mcp_token",secret={"token":token})
        raw_args=body.get("args") or []
        if not isinstance(raw_args,list) or not all(isinstance(item,str) for item in raw_args):raise ValueError("MCP args must be an array of strings")
        item=rt.mcp_store.put(
            server_id=sid,display_name=str(body.get("display_name") or sid),kind=str(body.get("kind") or "mcp"),
            transport=transport,url=body.get("url"),command=body.get("command"),args=raw_args,cwd=body.get("cwd"),
            enabled=bool(body.get("enabled",True)),credential_ref=credential_ref,
            timeout_sec=float(body.get("timeout_sec",30)),read_timeout_sec=float(body.get("read_timeout_sec",300)),
        )
        if item.enabled:rt.mcp.refresh(sid)
        rt.seed_policy();return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def mcp_refresh(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);tools=rt.mcp.refresh(request.path_params["server_id"]);rt.seed_policy();return JSONResponse({"tools":[tool.__dict__ for tool in tools]})
    except Exception as exc:return _error(exc)


async def mcp_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);sid=request.path_params["server_id"]
        try:server=rt.mcp_store.get(sid)
        except KeyError:return _error(KeyError("MCP server not found"),404)
        rt.capabilities_registry.unregister_prefix(f"mcp.{sid}.");rt.mcp_store.delete(sid)
        if server.credential_ref:
            try:rt.credentials.disable(server.credential_ref)
            except Exception:pass
        return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def providers_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"providers":list(_runtime(request).providers.public_state())})


async def provider_put(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request);key=str(body.get("key") or "")
        try:existing=rt.provider_settings.get(key);credential_ref=existing.credential_ref
        except KeyError:credential_ref=None
        api_key=str(body.get("api_key") or "").strip()
        if api_key:
            if credential_ref:rt.credentials.replace(credential_ref,{"api_key":api_key})
            else:credential_ref=rt.credentials.create(kind="provider_api_key",secret={"api_key":api_key})
        item=rt.provider_settings.put(key=key,kind=str(body.get("kind") or "openai_compatible"),model=str(body.get("model") or ""),base_url=body.get("base_url"),enabled=bool(body.get("enabled",True)),local=bool(body.get("local",False)),priority=int(body.get("priority",50)),credential_ref=credential_ref,metadata=body.get("metadata") if isinstance(body.get("metadata"),dict) else None)
        return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def provider_verify(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:return JSONResponse(_runtime(request).providers.verify(request.path_params["provider_key"]))
    except Exception as exc:return _error(exc)


async def provider_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);key=request.path_params["provider_key"]
        try:item=rt.provider_settings.get(key)
        except KeyError:return _error(KeyError("provider not found"),404)
        rt.provider_settings.delete(key)
        if item.credential_ref:
            try:rt.credentials.disable(item.credential_ref)
            except Exception:pass
        return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def connections_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    rt=_runtime(request);owner=_owner(request,gate);return JSONResponse({"connections":[x.as_dict() for x in rt.identities.connections(owner_principal_id=owner.principal_id)],"service_bindings":[x.as_dict() for x in rt.identities.service_bindings()]})
