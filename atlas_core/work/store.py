from __future__ import annotations
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
from .models import WorkItem,WorkStep
class WorkStore:
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self)->None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL");db.executescript("""
            CREATE TABLE IF NOT EXISTS work_items(work_id TEXT PRIMARY KEY,objective TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','active','waiting_confirmation','waiting','completed','failed','cancelled','paused')),owner_principal_id TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS work_steps(step_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,ordinal INTEGER NOT NULL,description TEXT NOT NULL,capability_id TEXT NOT NULL,input_json TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_confirmation','waiting','completed','failed','cancelled')),occurrence_id TEXT,output_json TEXT,error TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(work_id) REFERENCES work_items(work_id) ON DELETE CASCADE,UNIQUE(work_id,ordinal));
            """)
    def create(self,objective:str,owner_principal_id:str,steps:list[dict[str,Any]],*,metadata:dict[str,Any]|None=None)->WorkItem:
        if not objective.strip():raise ValueError("work objective is required")
        if not steps:raise ValueError("work requires at least one step")
        wid=f"work_{uuid4().hex}"
        with self._db() as db:
            db.execute("INSERT INTO work_items(work_id,objective,status,owner_principal_id,metadata_json) VALUES (?,?,'queued',?,?)",(wid,objective,owner_principal_id,json.dumps(metadata or {},sort_keys=True,separators=(",",":"))))
            for i,s in enumerate(steps,1):
                cid=str(s.get("capability_id") or "").strip();inp=s.get("input") or {};desc=str(s.get("description") or cid).strip()
                if not cid or not isinstance(inp,dict):raise ValueError("each work step requires capability_id and object input")
                db.execute("INSERT INTO work_steps(step_id,work_id,ordinal,description,capability_id,input_json,status) VALUES (?,?,?,?,?,?,'queued')",(f"step_{uuid4().hex}",wid,i,desc,cid,json.dumps(inp,sort_keys=True,separators=(",",":"),default=str)))
        return self.get(wid)
    def get(self,work_id:str)->WorkItem:
        with self._db() as db:r=db.execute("SELECT * FROM work_items WHERE work_id=?",(work_id,)).fetchone()
        if r is None:raise KeyError(work_id)
        return WorkItem(r["work_id"],r["objective"],r["status"],r["owner_principal_id"],r["created_at"],r["updated_at"],json.loads(r["metadata_json"] or "{}"))
    def list(self,*,limit:int=200)->tuple[WorkItem,...]:
        with self._db() as db:rows=db.execute("SELECT work_id FROM work_items ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return tuple(self.get(r["work_id"]) for r in rows)
    def steps(self,work_id:str)->tuple[WorkStep,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM work_steps WHERE work_id=? ORDER BY ordinal",(work_id,)).fetchall()
        return tuple(_step(r) for r in rows)
    def step(self,step_id:str)->WorkStep:
        with self._db() as db:r=db.execute("SELECT * FROM work_steps WHERE step_id=?",(step_id,)).fetchone()
        if r is None:raise KeyError(step_id)
        return _step(r)
    def set_work_status(self,work_id:str,status:str)->WorkItem:
        with self._db() as db:db.execute("UPDATE work_items SET status=?,updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(status,work_id))
        return self.get(work_id)
    def set_step(self,step_id:str,*,status:str,occurrence_id:str|None=None,output:Any=None,error:str|None=None)->WorkStep:
        with self._db() as db:db.execute("UPDATE work_steps SET status=?,occurrence_id=COALESCE(?,occurrence_id),output_json=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE step_id=?",(status,occurrence_id,None if output is None else json.dumps(output,default=str,ensure_ascii=False),error,step_id))
        return self.step(step_id)
    def cancel(self,work_id:str)->WorkItem:
        with self._db() as db:db.execute("UPDATE work_items SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,));db.execute("UPDATE work_steps SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND status NOT IN ('completed','failed')",(work_id,))
        return self.get(work_id)

def _step(r:sqlite3.Row)->WorkStep:return WorkStep(r["step_id"],r["work_id"],int(r["ordinal"]),r["description"],r["capability_id"],json.loads(r["input_json"] or "{}"),r["status"],r["occurrence_id"],None if not r["output_json"] else json.loads(r["output_json"]),r["error"],r["created_at"],r["updated_at"])
