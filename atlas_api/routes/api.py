from __future__ import annotations

import math
import logging
import mimetypes
import os
from datetime import datetime, timezone
from typing import Any

from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

from atlas_api.auth import (
    AuthService, client_key, forbidden, require_mutation_auth, require_session, unauthorized,
)
from atlas_core.capabilities import RuntimeContinuityRequired
from atlas_core.provenance import InvocationProvenance


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
    return JSONResponse({"ok": True, "service": "atlas-api", "version": "3.5.0"})


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
        occurrence = await run_in_threadpool(
            _runtime(request).capabilities.invoke, request.path_params["capability_id"],
            body.get("input") if isinstance(body.get("input"), dict) else {},
            provenance=InvocationProvenance(owner.principal_id, "human", "control"),
        )
        return JSONResponse({"action": occurrence.public()})
    except RuntimeContinuityRequired as exc:
        return JSONResponse({
            "error": str(exc), "code": "runtime_continuity_required",
            "capability_id": exc.capability_id, "scope": exc.scope,
        }, status_code=409)
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
        body = await _body(request); owner = _owner(request, gate); rt = _runtime(request)
        focus = body.get("focus") if isinstance(body.get("focus"), dict) else None
        attachments = [str(value) for value in body.get("attachments", []) if str(value).strip()] if isinstance(body.get("attachments"), list) else []
        result = await run_in_threadpool(
            rt.chat.send, request.path_params["conversation_id"], str(body.get("message") or "").strip(),
            principal_id=owner.principal_id, defer_capture=True, focus=focus, attachments=attachments,
        )
        capture = result.pop("_post_turn_capture", None)
        background = BackgroundTask(rt.chat.run_post_turn_capture, **capture) if capture else None
        return JSONResponse(result, background=background)
    except Exception as exc: return _error(exc, 500)


async def chat_attachment_upload(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    staged = None
    try:
        rt=_runtime(request);owner=_owner(request,gate)
        from atlas_core.artifacts.uploads import MAX_CHAT_UPLOAD_BYTES
        declared=int(request.headers.get("content-length") or 0)
        if declared > MAX_CHAT_UPLOAD_BYTES: return _error(ValueError("upload exceeds 30 MB"),413)
        data=await request.body()
        filename=str(request.headers.get("x-atlas-filename") or "upload")
        staged=rt.uploads.stage(data,filename=filename,media_type=request.headers.get("content-type"))
        from atlas_core.provenance import InvocationProvenance
        occurrence=await run_in_threadpool(rt.capabilities.invoke,"artifacts.accept_upload",staged,provenance=InvocationProvenance(owner.principal_id,"human","chat"))
        if occurrence.status!="succeeded":
            rt.uploads.discard(staged["staging_token"]);return JSONResponse({"action":occurrence.public(),"error":occurrence.error or occurrence.error_code or occurrence.status},status_code=409)
        return JSONResponse({"action":occurrence.public(),"artifact":occurrence.result},status_code=201)
    except Exception as exc:
        if staged:
            try: _runtime(request).uploads.discard(staged["staging_token"])
            except Exception: pass
        return _error(exc,413 if "30 MB" in str(exc) else 400)


async def work_list(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse): return gate
    cadence_id = request.query_params.get("cadence_id") or None
    return JSONResponse({"work": [x.as_dict() for x in _runtime(request).work_store.list(cadence_id=cadence_id)]})


async def work_create(request: Request) -> JSONResponse:
    gate = require_mutation_auth(request)
    if isinstance(gate, JSONResponse): return gate
    try:
        body = await _body(request); owner = _owner(request, gate); rt = _runtime(request)
        from atlas_core.provenance import InvocationProvenance
        payload={"objective":str(body.get("objective") or ""),"steps":body.get("steps") or [],"run":bool(body.get("run",True))}
        occurrence=await run_in_threadpool(rt.capabilities.invoke,"work.create",payload,provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=201 if occurrence.status=="succeeded" else 202 if occurrence.status == "uncertain" else 409
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
        rt=_runtime(request);owner=_owner(request,gate);wid=request.path_params["work_id"];action=request.path_params["action"]
        capability={"run":"work.run","resume":"work.resume","pause":"work.pause","cancel":"work.cancel"}.get(action)
        if not capability:return _error(ValueError("unsupported work action"),404)
        from atlas_core.provenance import InvocationProvenance
        occurrence=await run_in_threadpool(rt.capabilities.invoke,capability,{"work_id":wid},provenance=InvocationProvenance(owner.principal_id,"human","control"))
        if occurrence.status!="succeeded":return JSONResponse({"action":occurrence.public(),"error":occurrence.error or occurrence.error_code or occurrence.status},status_code=409)
        return JSONResponse(occurrence.result)
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
        occurrence=await run_in_threadpool(rt.capabilities.invoke,"cadence.create",payload,provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=201 if occurrence.status=="succeeded" else 202 if occurrence.status == "uncertain" else 409
        return JSONResponse({"action":occurrence.public(),"cadence":occurrence.result if occurrence.status=="succeeded" else None},status_code=status)
    except Exception as exc:return _error(exc)


async def cadence_enable(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate);body=await _body(request)
        from atlas_core.provenance import InvocationProvenance
        occurrence=await run_in_threadpool(rt.capabilities.invoke,"cadence.enable",{"cadence_id":request.path_params["cadence_id"],"enabled":bool(body.get("enabled",True))},provenance=InvocationProvenance(owner.principal_id,"human","control"))
        if occurrence.status!="succeeded":return JSONResponse({"action":occurrence.public(),"error":occurrence.error or occurrence.error_code or occurrence.status},status_code=409)
        return JSONResponse(occurrence.result)
    except Exception as exc:return _error(exc)


async def cadence_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate)
        from atlas_core.provenance import InvocationProvenance
        occurrence=await run_in_threadpool(rt.capabilities.invoke,"cadence.delete",{"cadence_id":request.path_params["cadence_id"]},provenance=InvocationProvenance(owner.principal_id,"human","control"))
        if occurrence.status!="succeeded":return JSONResponse({"action":occurrence.public(),"error":occurrence.error or occurrence.error_code or occurrence.status},status_code=409)
        return JSONResponse({"ok":True,"action":occurrence.public()})
    except Exception as exc:return _error(exc)


async def source_roots_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"roots":[x.public() for x in _runtime(request).source_roots.all() if x.provider_namespace != "atlas-managed"]})


async def source_file_view(request: Request) -> Response:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    fd = None
    try:
        rt = _runtime(request); owner = _owner(request, gate)
        root_id = str(request.query_params.get("root_id") or "")
        relative_path = str(request.query_params.get("relative_path") or "")
        row = rt.source_roots.get(root_id)
        from atlas_core.sources import validate_relative_path
        relative_path = validate_relative_path(relative_path)
        scope = f"files/{row.provider_namespace}/{row.root_id}/{relative_path}"
        decision = rt.policy.resolve(principal_id=owner.principal_id, scope=scope, operation="read")
        if decision.decision != "YES":
            return forbidden("file viewing is not allowed by current owner policy")
        fd, _ref, info = rt.sources.kernel.open_binary(
            row.provider_namespace, row.root_id, relative_path,
            configuration_revision=rt.sources._revision(row),
        )
        media_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        opened_fd = fd; fd = None
        def chunks():
            try:
                while True:
                    chunk = os.read(opened_fd, 256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                os.close(opened_fd)
        return StreamingResponse(chunks(), media_type=media_type, headers={
            "Content-Length": str(info.st_size),
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        })
    except Exception as exc:
        if fd is not None:
            os.close(fd)
        return _error(exc)


async def source_root_put(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request)
        root_id=str(body.get("root_id") or "");provider_namespace=str(body.get("provider_namespace") or "local")
        if root_id in {"atlas-managed-intake", "atlas-library-clean", "atlas-owner-uploads"} or provider_namespace in {"atlas-managed", "atlas-library", "atlas-upload"}: raise ValueError("Atlas-managed root identity is reserved")
        item=rt.source_roots.put(root_id=root_id,host_path=str(body.get("host_path") or ""),display_name=body.get("display_name"),provider_namespace=provider_namespace,quarantine_relative_path=body.get("quarantine_relative_path",".atlas-quarantine"),enabled=bool(body.get("enabled",True)));rt.sources.reload();rt.seed_policy();return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def source_root_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request); root_id=request.path_params["root_id"]
        if root_id in {"atlas-managed-intake", "atlas-library-clean", "atlas-owner-uploads"}: raise ValueError("Atlas-managed root cannot be removed")
        rt.source_roots.delete(root_id);rt.sources.reload();return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def library_scans(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    return JSONResponse({"scans": list(_runtime(request).library_store.recent_scans())})


async def library_scan_detail(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        scan_id = request.path_params["scan_id"]
        rt = _runtime(request)
        return JSONResponse({
            "scan": rt.library_store.get_scan(scan_id),
            "files": list(rt.library_store.files(scan_id)),
            "duplicate_groups": list(rt.library_store.duplicate_groups(scan_id)),
        })
    except KeyError:
        return _error(KeyError("library scan not found"), 404)


async def library_reviews(request: Request) -> JSONResponse:
    gate = require_session(request)
    if isinstance(gate, JSONResponse):
        return gate
    root_id = request.query_params.get("root_id")
    return JSONResponse({"reviews": list(_runtime(request).library_store.reviews(root_id=root_id))})


async def knowledge_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    q=request.query_params.get("q")
    rows=_runtime(request).knowledge_store.search(q,limit=50) if q else _runtime(request).knowledge_store.recent(limit=100)
    return JSONResponse({"items":list(rows)})


async def knowledge_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate)
        from atlas_core.provenance import InvocationProvenance
        occurrence=rt.capabilities.invoke("knowledge.delete",{"item_id":request.path_params["item_id"]},provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=200 if occurrence.status=="succeeded" else 202 if occurrence.status == "uncertain" else 409
        return JSONResponse({"action":occurrence.public()},status_code=status)
    except Exception as exc:return _error(exc)


async def knowledge_promote(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request);owner=_owner(request,gate)
        from atlas_core.provenance import InvocationProvenance
        payload={k:v for k,v in {
            "content":body.get("content"),"title":body.get("title"),"source_ref":body.get("source_ref"),
            "kind":body.get("kind"),"metadata":body.get("metadata") if isinstance(body.get("metadata"),dict) else None,
        }.items() if v is not None}
        occurrence=rt.capabilities.invoke("knowledge.promote",payload,provenance=InvocationProvenance(owner.principal_id,"human","control"))
        status=201 if occurrence.status=="succeeded" else 202 if occurrence.status == "uncertain" else 409
        return JSONResponse({"action":occurrence.public(),"item":occurrence.result if occurrence.status=="succeeded" else None},status_code=status)
    except Exception as exc:return _error(exc)


async def artifacts_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    rt=_runtime(request);owner=_owner(request,gate)
    rows=rt.artifact_store.list(owner.principal_id,name_like=request.query_params.get("q"),byte_sha256=request.query_params.get("hash"),state=request.query_params.get("state"),limit=200)
    return JSONResponse({"artifacts":list(rows)})


async def artifact_detail(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);owner=_owner(request,gate);item=rt.artifact_store.get(request.path_params["artifact_id"])
        if item["principal_id"]!=owner.principal_id:return _error(KeyError("artifact not found"),404)
        return JSONResponse({"artifact":item,"passages":[{k:v for k,v in row.items() if k!="content"} for row in rt.passages.for_source(item["artifact_id"])]})
    except KeyError:return _error(KeyError("artifact not found"),404)


async def artifact_content(request: Request) -> Response:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    fd=None
    try:
        rt=_runtime(request);owner=_owner(request,gate);item=rt.artifact_store.get(request.path_params["artifact_id"])
        if item["principal_id"]!=owner.principal_id:return _error(KeyError("artifact not found"),404)
        facet=next((row for row in item.get("facets",[]) if row.get("kind")=="local_file" and row.get("state")=="present"),None)
        if facet is None:return _error(ValueError("artifact has no readable local representation"),404)
        root=rt.source_roots.get(facet["root_id"]);relative_path=str(facet["relative_path"] or "")
        scope=f"files/{root.provider_namespace}/{root.root_id}/{relative_path}"
        decision=rt.policy.resolve(principal_id=owner.principal_id,scope=scope,operation="read")
        if decision.decision!="YES":return forbidden("artifact viewing is not allowed by current owner policy")
        fd,_ref,info=rt.sources.kernel.open_binary(root.provider_namespace,root.root_id,relative_path,configuration_revision=rt.sources._revision(root))
        media_type=item.get("media_type") or mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        opened_fd=fd;fd=None
        def chunks():
            try:
                while True:
                    chunk=os.read(opened_fd,256*1024)
                    if not chunk:break
                    yield chunk
            finally:os.close(opened_fd)
        return StreamingResponse(chunks(),media_type=media_type,headers={"Content-Length":str(info.st_size),"Content-Disposition":"inline","X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store"})
    except KeyError:return _error(KeyError("artifact not found"),404)
    except Exception as exc:
        if fd is not None:os.close(fd)
        return _error(exc)


async def knowledge_generations(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    rt=_runtime(request)
    return JSONResponse({"generations":list(rt.generations.list())})


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
        chain=rt.memory_store.chain_for(owner.principal_id,item["item_id"])
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
        status=200 if occurrence.status=="succeeded" else 202 if occurrence.status == "uncertain" else 409
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
                except Exception:logger.warning("failed to disable obsolete MCP credential", exc_info=True)
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
        if item.enabled:await run_in_threadpool(rt.mcp.refresh,sid)
        rt.seed_policy();return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def mcp_refresh(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);tools=await run_in_threadpool(rt.mcp.refresh,request.path_params["server_id"]);rt.seed_policy();return JSONResponse({"tools":[tool.__dict__ for tool in tools]})
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
            except Exception:logger.warning("failed to disable deleted MCP credential", exc_info=True)
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
    try:return JSONResponse(await run_in_threadpool(_runtime(request).providers.verify,request.path_params["provider_key"]))
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
            except Exception:logger.warning("failed to disable deleted provider credential", exc_info=True)
        return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def web_providers_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    return JSONResponse({"providers":list(_runtime(request).web_providers.public_state())})


async def web_provider_put(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        body=await _body(request);rt=_runtime(request);key=str(body.get("key") or "")
        try:existing=rt.web_provider_settings.get(key);credential_ref=existing.credential_ref
        except KeyError:credential_ref=None
        api_key=str(body.get("api_key") or "").strip()
        if api_key:
            if credential_ref:rt.credentials.replace(credential_ref,{"api_key":api_key})
            else:credential_ref=rt.credentials.create(kind="web_provider_api_key",secret={"api_key":api_key})
        item=rt.web_provider_settings.put(
            key=key,kind=str(body.get("kind") or ""),enabled=bool(body.get("enabled",True)),
            priority=int(body.get("priority",50)),credential_ref=credential_ref,
            metadata=body.get("metadata") if isinstance(body.get("metadata"),dict) else None,
        )
        return JSONResponse(item.public())
    except Exception as exc:return _error(exc)


async def web_provider_verify(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:return JSONResponse(await run_in_threadpool(_runtime(request).web_providers.verify,request.path_params["provider_key"]))
    except Exception as exc:return _error(exc)


async def web_provider_delete(request: Request) -> JSONResponse:
    gate=require_mutation_auth(request)
    if isinstance(gate,JSONResponse):return gate
    try:
        rt=_runtime(request);key=request.path_params["provider_key"]
        try:item=rt.web_provider_settings.get(key)
        except KeyError:return _error(KeyError("web provider not found"),404)
        rt.web_provider_settings.delete(key)
        if item.credential_ref:
            try:rt.credentials.disable(item.credential_ref)
            except Exception:logger.warning("failed to disable deleted web provider credential",exc_info=True)
        return JSONResponse({"ok":True})
    except Exception as exc:return _error(exc)


async def connections_list(request: Request) -> JSONResponse:
    gate=require_session(request)
    if isinstance(gate,JSONResponse):return gate
    rt=_runtime(request);owner=_owner(request,gate);return JSONResponse({"connections":[x.as_dict() for x in rt.identities.connections(owner_principal_id=owner.principal_id)],"service_bindings":[x.as_dict() for x in rt.identities.service_bindings()]})
