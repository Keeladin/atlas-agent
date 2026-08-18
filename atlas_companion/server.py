from __future__ import annotations
import argparse, json
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from wsgiref.simple_server import WSGIRequestHandler, make_server
from atlas_core.bootstrap import build_runtime
from atlas_core.planner import TaskPlanner
from atlas_core.presentation import TaskPresenter
from atlas_core.tasks import InvalidTransitionError, TaskStoreError, UnknownRecordError

class CompanionService:
    """HTTP adapter; task state and execution stay in TaskRuntime."""
    def __init__(self, *, db_path: str | Path, provider_config: str | Path | None = None): self.runtime=build_runtime(db_path=db_path, provider_config=provider_config)
    def tasks(self): return [asdict(t) for t in reversed(self.runtime.store.list_tasks())]
    def detail(self, task_id):
        p=TaskPresenter(self.runtime.store).build(task_id)
        return {"snapshot":self.runtime.store.snapshot(task_id,include_artifact_payloads=True),"presentation":p.as_dict(),"markdown":p.render_markdown()}
    def create_and_run(self, body):
        criteria=body.get("criteria",[])
        if isinstance(criteria,str): criteria=[x.strip() for x in criteria.splitlines() if x.strip()]
        if not isinstance(criteria,list): raise ValueError("criteria must be a list of non-empty strings")
        if self.runtime.model_router is None: raise ValueError("Task creation requires a configured server-side provider registry.")
        planning=self.runtime.capabilities.get("planning.general").spec
        manifest=[x for x in self.runtime.capabilities.manifest() if x["id"] != planning.id]
        task,_=TaskPlanner(store=self.runtime.store,model_router=self.runtime.model_router,planning_capability=planning,capability_manifest=manifest).plan_and_create(objective=str(body.get("objective","")),success_criteria=tuple(criteria),constraints=tuple(body.get("constraints",[])),authority_scope=str(body.get("authority","interpret")),metadata={"interface":"companion_pwa"})
        self.runtime.run_until_blocked(task.id); return self.detail(task.id)
    def run(self, task_id): self.runtime.resume_blocked(task_id); self.runtime.run_until_blocked(task_id); return self.detail(task_id)
    def decide(self, approval_id, decision, note=None): return self.detail(self.runtime.store.decide_approval(approval_id,status=decision,note=note).task_id)
    def cancel(self, task_id):
        task=self.runtime.store.set_task_status(task_id,"cancelled"); self.runtime.store.create_checkpoint(task.id,reason="task cancelled from Companion PWA"); return self.detail(task.id)

class CompanionApp:
    def __init__(self,service,static_dir): self.service,self.static_dir=service,static_dir
    def __call__(self,environ,start_response):
        try:
            method,path=environ["REQUEST_METHOD"],urlparse(environ["PATH_INFO"]).path
            if method=="GET" and path=="/api/tasks": return self._json(start_response,HTTPStatus.OK,self.service.tasks())
            if method=="GET" and path.startswith("/api/tasks/"): return self._json(start_response,HTTPStatus.OK,self.service.detail(path.rsplit("/",1)[-1]))
            if method=="POST" and path=="/api/tasks": return self._json(start_response,HTTPStatus.CREATED,self.service.create_and_run(self._body(environ)))
            if method=="POST" and path.endswith("/run") and path.startswith("/api/tasks/"): return self._json(start_response,HTTPStatus.OK,self.service.run(path.split("/")[3]))
            if method=="POST" and path.endswith("/cancel") and path.startswith("/api/tasks/"): return self._json(start_response,HTTPStatus.OK,self.service.cancel(path.split("/")[3]))
            if method=="POST" and path.startswith("/api/approvals/"):
                bits=path.split("/")
                if len(bits)==5 and bits[4] in {"approve","deny"}: return self._json(start_response,HTTPStatus.OK,self.service.decide(bits[3],"approved" if bits[4]=="approve" else "denied",self._body(environ).get("note")))
            if method=="GET": return self._static(start_response,path)
            return self._json(start_response,HTTPStatus.NOT_FOUND,{"error":"not found"})
        except (ValueError,TaskStoreError,UnknownRecordError,InvalidTransitionError) as exc: return self._json(start_response,HTTPStatus.BAD_REQUEST,{"error":str(exc)})
    @staticmethod
    def _body(environ): return json.loads(environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0)) or b"{}")
    @staticmethod
    def _json(start_response,status,data):
        payload=json.dumps(data,ensure_ascii=False,default=str).encode(); start_response(f"{status.value} {status.phrase}",[("Content-Type","application/json; charset=utf-8"),("Content-Length",str(len(payload)))]); return [payload]
    def _static(self,start_response,path):
        name="index.html" if path in {"/","/index.html"} else path.lstrip("/"); target=(self.static_dir/name).resolve()
        if self.static_dir.resolve() not in target.parents or not target.is_file(): return self._json(start_response,HTTPStatus.NOT_FOUND,{"error":"not found"})
        mime={".html":"text/html",".css":"text/css",".js":"application/javascript",".webmanifest":"application/manifest+json"}.get(target.suffix,"application/octet-stream"); payload=target.read_bytes(); start_response("200 OK",[("Content-Type",f"{mime}; charset=utf-8"),("Content-Length",str(len(payload)))]); return [payload]

def main():
    p=argparse.ArgumentParser(description="Atlas Companion PWA (LAN-local TaskRuntime interface)"); p.add_argument("--db",default="instance/atlas.db"); p.add_argument("--providers"); p.add_argument("--host",default="127.0.0.1",help="Use a LAN address only on a trusted network."); p.add_argument("--port",type=int,default=8787); a=p.parse_args()
    app=CompanionApp(CompanionService(db_path=a.db,provider_config=a.providers),Path(__file__).resolve().parent/"web")
    with make_server(a.host,a.port,app,handler_class=WSGIRequestHandler) as server: print(f"Atlas Companion PWA listening on http://{a.host}:{a.port}"); server.serve_forever()
if __name__=="__main__": main()
