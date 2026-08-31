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
            CREATE TABLE IF NOT EXISTS work_items(work_id TEXT PRIMARY KEY,display_ref TEXT,artifact_class TEXT,workflow_class TEXT,objective TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','active','waiting_confirmation','waiting','completed','failed','cancelled','paused')),owner_principal_id TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS work_route_sequences(route_code TEXT PRIMARY KEY,next_value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS work_steps(step_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,ordinal INTEGER NOT NULL,description TEXT NOT NULL,capability_id TEXT NOT NULL,input_json TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_confirmation','waiting','completed','failed','cancelled')),occurrence_id TEXT,output_json TEXT,error TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(work_id) REFERENCES work_items(work_id) ON DELETE CASCADE,UNIQUE(work_id,ordinal));
            """)
            columns={row[1] for row in db.execute("PRAGMA table_info(work_items)")}
            for name in ("display_ref","artifact_class","workflow_class"):
                if name not in columns:db.execute(f"ALTER TABLE work_items ADD COLUMN {name} TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS work_display_ref_unique ON work_items(display_ref) WHERE display_ref IS NOT NULL")
    def create(self,objective:str,owner_principal_id:str,steps:list[dict[str,Any]],*,metadata:dict[str,Any]|None=None,artifact_class:str|None=None,workflow_class:str|None=None)->WorkItem:
        if not objective.strip():raise ValueError("work objective is required")
        if not steps:raise ValueError("work requires at least one step")
        wid=f"work_{uuid4().hex}"
        with self._db() as db:
            display_ref=None
            if artifact_class is not None or workflow_class is not None:
                if not (isinstance(artifact_class,str) and len(artifact_class)==1 and artifact_class.isalpha() and isinstance(workflow_class,str) and len(workflow_class)==1 and workflow_class.isalpha()):raise ValueError("routed work requires one-letter artifact and workflow classes")
                route=(artifact_class+workflow_class).upper();row=db.execute("SELECT next_value FROM work_route_sequences WHERE route_code=?",(route,)).fetchone();number=int(row[0]) if row else 1
                db.execute("INSERT INTO work_route_sequences(route_code,next_value) VALUES (?,?) ON CONFLICT(route_code) DO UPDATE SET next_value=excluded.next_value",(route,number+1));display_ref=f"{route}-{number:03d}"
            db.execute("INSERT INTO work_items(work_id,display_ref,artifact_class,workflow_class,objective,status,owner_principal_id,metadata_json) VALUES (?,?,?,?,?,'queued',?,?)",(wid,display_ref,artifact_class.upper() if artifact_class else None,workflow_class.upper() if workflow_class else None,objective,owner_principal_id,json.dumps(metadata or {},sort_keys=True,separators=(",",":"))))
            for i,s in enumerate(steps,1):
                cid=str(s.get("capability_id") or "").strip();inp=s.get("input") or {};desc=str(s.get("description") or cid).strip()
                if not cid or not isinstance(inp,dict):raise ValueError("each work step requires capability_id and object input")
                db.execute("INSERT INTO work_steps(step_id,work_id,ordinal,description,capability_id,input_json,status) VALUES (?,?,?,?,?,?,'queued')",(f"step_{uuid4().hex}",wid,i,desc,cid,json.dumps(inp,sort_keys=True,separators=(",",":"),default=str)))
        return self.get(wid)
    def get(self,work_id:str)->WorkItem:
        with self._db() as db:r=db.execute("SELECT * FROM work_items WHERE work_id=?",(work_id,)).fetchone()
        if r is None:raise KeyError(work_id)
        return WorkItem(r["work_id"],r["objective"],r["status"],r["owner_principal_id"],r["created_at"],r["updated_at"],json.loads(r["metadata_json"] or "{}"),r["display_ref"],r["artifact_class"],r["workflow_class"])
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
        with self._db() as db:
            db.execute(
                "UPDATE work_items SET status=?,updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND status NOT IN ('completed','cancelled')",
                (status,work_id),
            )
        return self.get(work_id)
    def set_step(self,step_id:str,*,status:str,occurrence_id:str|None=None,output:Any=None,error:str|None=None)->WorkStep:
        with self._db() as db:
            db.execute(
                """UPDATE work_steps SET status=?,occurrence_id=COALESCE(?,occurrence_id),output_json=?,error=?,updated_at=CURRENT_TIMESTAMP
                   WHERE step_id=? AND (status!='completed' OR ?='completed')""",
                (status,occurrence_id,None if output is None else json.dumps(output,default=str,ensure_ascii=False),error,step_id,status),
            )
        return self.step(step_id)
    def claim_step(self,step_id:str)->bool:
        """Atomically claim one queued step for execution.

        Concurrent Work runners may inspect the same durable step, but only one
        is allowed to move it from queued to running. The loser must observe the
        durable state instead of invoking the capability a second time.
        """
        with self._db() as db:
            cursor=db.execute(
                "UPDATE work_steps SET status='running',updated_at=CURRENT_TIMESTAMP WHERE step_id=? AND status='queued'",
                (step_id,),
            )
            return cursor.rowcount==1
    def reset_failed(self,work_id:str)->WorkItem:
        """Retry from the first failed step without replaying completed evidence."""
        with self._db() as db:
            row=db.execute("SELECT MIN(ordinal) FROM work_steps WHERE work_id=? AND status='failed'",(work_id,)).fetchone()
            ordinal=row[0] if row else None
            if ordinal is None:raise ValueError("work has no failed step to retry")
            db.execute("UPDATE work_steps SET status='queued',occurrence_id=NULL,output_json=NULL,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND ordinal>=? AND status!='completed'",(work_id,int(ordinal)))
            db.execute("UPDATE work_items SET status='queued',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,))
        return self.get(work_id)
    def reset_retryable(self,work_id:str)->WorkItem:
        """Retry a paused operational failure without replaying completed evidence."""
        with self._db() as db:
            row=db.execute("SELECT MIN(ordinal) FROM work_steps WHERE work_id=? AND status IN ('waiting','failed') AND error IS NOT NULL",(work_id,)).fetchone()
            ordinal=row[0] if row else None
            if ordinal is None:
                db.execute("UPDATE work_items SET status='active',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,))
            else:
                db.execute("UPDATE work_steps SET status='queued',occurrence_id=NULL,output_json=NULL,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND ordinal>=? AND status!='completed'",(work_id,int(ordinal)))
                db.execute("UPDATE work_items SET status='queued',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,))
        return self.get(work_id)
    def cancel(self,work_id:str)->WorkItem:
        with self._db() as db:db.execute("UPDATE work_items SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,));db.execute("UPDATE work_steps SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND status NOT IN ('completed','failed')",(work_id,))
        return self.get(work_id)

def _step(r:sqlite3.Row)->WorkStep:return WorkStep(r["step_id"],r["work_id"],int(r["ordinal"]),r["description"],r["capability_id"],json.loads(r["input_json"] or "{}"),r["status"],r["occurrence_id"],None if not r["output_json"] else json.loads(r["output_json"]),r["error"],r["created_at"],r["updated_at"])
