from __future__ import annotations
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
from atlas_core.database import WorkDatabase, as_work_database
from .models import WorkItem,WorkStep
class WorkStore:
    def __init__(self,database:WorkDatabase|str|Path)->None:
        self.database=as_work_database(database);self.path=self.database.path
    @contextmanager
    def _db(self):
        with self.database.connection() as db:yield db
    def initialize(self)->None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL");db.executescript("""
            CREATE TABLE IF NOT EXISTS work_items(work_id TEXT PRIMARY KEY,display_ref TEXT,artifact_class TEXT,workflow_class TEXT,objective TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('staged','runnable','queued','active','waiting','completed','failed','cancelled','paused')),owner_principal_id TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',source_cadence_id TEXT,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS work_route_sequences(route_code TEXT PRIMARY KEY,next_value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS work_steps(step_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,ordinal INTEGER NOT NULL,description TEXT NOT NULL,capability_id TEXT NOT NULL,input_json TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','waiting','completed','failed','cancelled')),occurrence_id TEXT,output_json TEXT,error TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(work_id) REFERENCES work_items(work_id) ON DELETE CASCADE,UNIQUE(work_id,ordinal));
            CREATE TABLE IF NOT EXISTS obligation_bindings(
                binding_id TEXT PRIMARY KEY,
                obligation_id TEXT NOT NULL,
                mechanism_kind TEXT NOT NULL CHECK(mechanism_kind IN ('work_step','occurrence')),
                mechanism_id TEXT NOT NULL,
                work_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(work_id) REFERENCES work_items(work_id) ON DELETE CASCADE,
                UNIQUE(obligation_id,mechanism_kind,mechanism_id)
            );
            CREATE INDEX IF NOT EXISTS obligation_bindings_work
                ON obligation_bindings(work_id,created_at);
            CREATE INDEX IF NOT EXISTS obligation_bindings_obligation
                ON obligation_bindings(obligation_id,created_at);
            CREATE TRIGGER IF NOT EXISTS binding_work_step_requires_step
            BEFORE INSERT ON obligation_bindings
            WHEN NEW.mechanism_kind='work_step' AND (
                NEW.work_id IS NULL OR NOT EXISTS(
                    SELECT 1 FROM work_steps s WHERE s.step_id=NEW.mechanism_id AND s.work_id=NEW.work_id
                )
            )
            BEGIN SELECT RAISE(ABORT,'work-step obligation binding requires the matching Work step'); END;
            CREATE TRIGGER IF NOT EXISTS binding_occurrence_requires_occurrence
            BEFORE INSERT ON obligation_bindings
            WHEN NEW.mechanism_kind='occurrence' AND NOT EXISTS(
                SELECT 1 FROM action_occurrences a WHERE a.occurrence_id=NEW.mechanism_id
            )
            BEGIN SELECT RAISE(ABORT,'occurrence obligation binding requires the matching Action occurrence'); END;
            CREATE TRIGGER IF NOT EXISTS work_staged_requires_binding_insert
            BEFORE INSERT ON work_items
            WHEN NEW.status='staged'
             AND NOT EXISTS(SELECT 1 FROM obligation_bindings b WHERE b.work_id=NEW.work_id)
            BEGIN SELECT RAISE(ABORT,'staged work requires a backing obligation'); END;
            CREATE TRIGGER IF NOT EXISTS work_staged_requires_binding_update
            BEFORE UPDATE OF status ON work_items
            WHEN NEW.status='staged'
             AND NOT EXISTS(SELECT 1 FROM obligation_bindings b WHERE b.work_id=NEW.work_id)
            BEGIN SELECT RAISE(ABORT,'staged work requires a backing obligation'); END;
            DROP TRIGGER IF EXISTS binding_delete_preserves_staged_work;
            DROP TRIGGER IF EXISTS binding_move_preserves_staged_work;
            """)
            binding_columns={row[1] for row in db.execute("PRAGMA table_info(obligation_bindings)")}
            required_binding_columns={"binding_id","obligation_id","mechanism_kind","mechanism_id","work_id","created_at"}
            if not required_binding_columns.issubset(binding_columns):
                raise RuntimeError("atlas-work.db requires development schema reset for obligation bindings")
            columns={row[1] for row in db.execute("PRAGMA table_info(work_items)")}
            required_work_columns={"work_id","display_ref","artifact_class","workflow_class","objective","status","owner_principal_id","metadata_json","source_cadence_id","revision","created_at","updated_at"}
            if not required_work_columns.issubset(columns):
                raise RuntimeError("atlas-work.db requires development schema reset for Work schema")
            db.execute("""CREATE TABLE IF NOT EXISTS work_adaptations(
                adaptation_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, base_revision INTEGER NOT NULL, new_revision INTEGER NOT NULL,
                from_ordinal INTEGER NOT NULL, change_intent TEXT NOT NULL, reason TEXT NOT NULL, unchanged_goal TEXT NOT NULL,
                expected_impact TEXT NOT NULL, before_steps_json TEXT NOT NULL, after_steps_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(work_id) REFERENCES work_items(work_id) ON DELETE CASCADE)""")
            db.execute("CREATE INDEX IF NOT EXISTS work_adaptations_work ON work_adaptations(work_id,new_revision)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS work_display_ref_unique ON work_items(display_ref) WHERE display_ref IS NOT NULL")
            db.execute("CREATE INDEX IF NOT EXISTS work_source_cadence_id_idx ON work_items(source_cadence_id) WHERE source_cadence_id IS NOT NULL")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS work_chat_origin_key_unique
                        ON work_items(json_extract(metadata_json,'$.chat_origin.work_key'))
                        WHERE json_extract(metadata_json,'$.chat_origin.work_key') IS NOT NULL""")
    def create(self,objective:str,owner_principal_id:str,steps:list[dict[str,Any]],*,metadata:dict[str,Any]|None=None,artifact_class:str|None=None,workflow_class:str|None=None,stage:bool=False)->WorkItem:
        if not objective.strip():raise ValueError("work objective is required")
        if not steps:raise ValueError("work requires at least one step")
        wid=f"work_{uuid4().hex}"
        mapped=tuple(
            str(oid).strip()
            for step in steps
            for oid in (step.get("obligation_ids") or ())
            if str(oid).strip()
        )
        if stage and not mapped:raise ValueError("staged Work requires at least one backing obligation")
        with self._db() as db:
            display_ref=None
            if artifact_class is not None or workflow_class is not None:
                if not (isinstance(artifact_class,str) and len(artifact_class)==1 and artifact_class.isalpha() and isinstance(workflow_class,str) and len(workflow_class)==1 and workflow_class.isalpha()):raise ValueError("routed work requires one-letter artifact and workflow classes")
                route=(artifact_class+workflow_class).upper();row=db.execute("SELECT next_value FROM work_route_sequences WHERE route_code=?",(route,)).fetchone();number=int(row[0]) if row else 1
                db.execute("INSERT INTO work_route_sequences(route_code,next_value) VALUES (?,?) ON CONFLICT(route_code) DO UPDATE SET next_value=excluded.next_value",(route,number+1));display_ref=f"{route}-{number:03d}"
            source_cadence_id=(metadata or {}).get("cadence_id")
            db.execute("INSERT INTO work_items(work_id,display_ref,artifact_class,workflow_class,objective,status,owner_principal_id,metadata_json,source_cadence_id) VALUES (?,?,?,?,?,'queued',?,?,?)",(wid,display_ref,artifact_class.upper() if artifact_class else None,workflow_class.upper() if workflow_class else None,objective,owner_principal_id,json.dumps(metadata or {},sort_keys=True,separators=(",",":")),str(source_cadence_id) if source_cadence_id else None))
            for i,step in enumerate(steps,1):
                cid=str(step.get("capability_id") or "").strip();inp=step.get("input") or {};desc=str(step.get("description") or cid).strip()
                if not cid or not isinstance(inp,dict):raise ValueError("each work step requires capability_id and object input")
                step_id=f"step_{uuid4().hex}"
                db.execute("INSERT INTO work_steps(step_id,work_id,ordinal,description,capability_id,input_json,status) VALUES (?,?,?,?,?,?,'queued')",(step_id,wid,i,desc,cid,json.dumps(inp,sort_keys=True,separators=(",",":"),default=str)))
                for obligation_id in dict.fromkeys(str(value).strip() for value in (step.get("obligation_ids") or ()) if str(value).strip()):
                    db.execute(
                        "INSERT INTO obligation_bindings(binding_id,obligation_id,mechanism_kind,mechanism_id,work_id) VALUES (?,?,'work_step',?,?)",
                        (f"binding_{uuid4().hex}",obligation_id,step_id,wid),
                    )
            if stage:db.execute("UPDATE work_items SET status='staged',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(wid,))
        return self.get(wid)
    def get(self,work_id:str)->WorkItem:
        with self._db() as db:r=db.execute("SELECT * FROM work_items WHERE work_id=?",(work_id,)).fetchone()
        if r is None:raise KeyError(work_id)
        return WorkItem(r["work_id"],r["objective"],r["status"],r["owner_principal_id"],r["created_at"],r["updated_at"],json.loads(r["metadata_json"] or "{}"),r["display_ref"],r["artifact_class"],r["workflow_class"],r["source_cadence_id"],int(r["revision"] if "revision" in r.keys() else 1))
    def bindings(self,work_id:str)->tuple[dict[str,Any],...]:
        with self._db() as db:
            rows=db.execute("SELECT * FROM obligation_bindings WHERE work_id=? ORDER BY created_at,rowid",(work_id,)).fetchall()
        return tuple(dict(row) for row in rows)

    def bind_occurrence_obligation(self,obligation_id:str,occurrence_id:str)->dict[str,Any]:
        bid=f"binding_{uuid4().hex}"
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO obligation_bindings(binding_id,obligation_id,mechanism_kind,mechanism_id,work_id) VALUES (?,?,'occurrence',?,NULL)",
                (bid,obligation_id,occurrence_id),
            )
            row=db.execute(
                "SELECT * FROM obligation_bindings WHERE obligation_id=? AND mechanism_kind='occurrence' AND mechanism_id=?",
                (obligation_id,occurrence_id),
            ).fetchone()
        if row is None:raise RuntimeError("occurrence obligation binding was not persisted")
        return dict(row)

    def staged_without_bindings(self)->tuple[str,...]:
        with self._db() as db:
            rows=db.execute("SELECT w.work_id FROM work_items w WHERE w.status='staged' AND NOT EXISTS(SELECT 1 FROM obligation_bindings b WHERE b.work_id=w.work_id)").fetchall()
        return tuple(row["work_id"] for row in rows)

    def servicing(self,obligation_id:str)->tuple[dict[str,Any],...]:
        with self._db() as db:
            rows=db.execute(
                """SELECT b.*,w.status AS work_status,s.status AS step_status,
                          s.occurrence_id AS step_occurrence_id,a.status AS occurrence_status
                   FROM obligation_bindings b
                   LEFT JOIN work_items w ON w.work_id=b.work_id
                   LEFT JOIN work_steps s
                     ON b.mechanism_kind='work_step' AND s.step_id=b.mechanism_id
                   LEFT JOIN action_occurrences a
                     ON (b.mechanism_kind='occurrence' AND a.occurrence_id=b.mechanism_id)
                     OR (b.mechanism_kind='work_step' AND a.occurrence_id=s.occurrence_id)
                   WHERE b.obligation_id=? ORDER BY b.created_at,b.rowid""",
                (obligation_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def find_by_origin_key(self,work_key:str)->WorkItem|None:
        with self._db() as db:
            row=db.execute("SELECT work_id FROM work_items WHERE json_extract(metadata_json,'$.chat_origin.work_key')=? LIMIT 1",(work_key,)).fetchone()
        return self.get(row["work_id"]) if row is not None else None

    def list(self,*,limit:int=200,cadence_id:str|None=None)->tuple[WorkItem,...]:
        with self._db() as db:
            if cadence_id:rows=db.execute("SELECT work_id FROM work_items WHERE source_cadence_id=? ORDER BY created_at DESC LIMIT ?",(cadence_id,limit)).fetchall()
            else:rows=db.execute("SELECT work_id FROM work_items ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        return tuple(self.get(r["work_id"]) for r in rows)
    def steps(self,work_id:str)->tuple[WorkStep,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM work_steps WHERE work_id=? ORDER BY ordinal",(work_id,)).fetchall()
        return tuple(_step(r) for r in rows)
    def step(self,step_id:str)->WorkStep:
        with self._db() as db:r=db.execute("SELECT * FROM work_steps WHERE step_id=?",(step_id,)).fetchone()
        if r is None:raise KeyError(step_id)
        return _step(r)
    def bind_occurrence(self,step_id:str,occurrence_id:str,*,status:str|None=None)->WorkStep:
        with self._db() as db:
            if status is None:
                changed=db.execute("UPDATE work_steps SET occurrence_id=?,updated_at=CURRENT_TIMESTAMP WHERE step_id=? AND status!='completed' AND (occurrence_id IS NULL OR occurrence_id=?)",(occurrence_id,step_id,occurrence_id)).rowcount
            else:
                changed=db.execute("UPDATE work_steps SET occurrence_id=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE step_id=? AND status!='completed' AND (occurrence_id IS NULL OR occurrence_id=?)",(occurrence_id,status,step_id,occurrence_id)).rowcount
        if changed!=1:raise ValueError("work step occurrence binding changed or is not eligible")
        return self.step(step_id)

    def set_work_status(self,work_id:str,status:str)->WorkItem:
        with self._db() as db:
            db.execute(
                "UPDATE work_items SET status=?,updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND status NOT IN ('completed','cancelled')",
                (status,work_id),
            )
        return self.get(work_id)
    def merge_metadata(self,work_id:str,patch:dict[str,Any])->WorkItem:
        """Merge top-level runtime metadata without changing Work execution truth."""
        with self._db() as db:
            row=db.execute("SELECT metadata_json FROM work_items WHERE work_id=?",(work_id,)).fetchone()
            if row is None:raise KeyError(work_id)
            metadata=json.loads(row["metadata_json"] or "{}")
            metadata.update(dict(patch or {}))
            db.execute(
                "UPDATE work_items SET metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE work_id=?",
                (json.dumps(metadata,sort_keys=True,separators=(",",":"),default=str),work_id),
            )
        return self.get(work_id)
    def set_step(self,step_id:str,*,status:str,occurrence_id:str|None=None,output:Any=None,error:str|None=None)->WorkStep:
        with self._db() as db:
            db.execute(
                """UPDATE work_steps SET status=?,occurrence_id=COALESCE(?,occurrence_id),output_json=?,error=?,updated_at=CURRENT_TIMESTAMP
                   WHERE step_id=? AND status!='completed'""",
                (status,occurrence_id,None if output is None else json.dumps(output,default=str,ensure_ascii=False),error,step_id),
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

    def adaptations(self,work_id:str)->tuple[dict[str,Any],...]:
        with self._db() as db:
            rows=db.execute("SELECT * FROM work_adaptations WHERE work_id=? ORDER BY new_revision,created_at",(work_id,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item["before_steps"]=json.loads(item.pop("before_steps_json"));item["after_steps"]=json.loads(item.pop("after_steps_json"));result.append(item)
        return tuple(result)

    def revise(self,work_id:str,*,base_revision:int,from_ordinal:int,replacement_steps:list[dict[str,Any]],change_intent:str,reason:str,unchanged_goal:str,expected_impact:str)->WorkItem:
        if from_ordinal < 1: raise ValueError("from_ordinal must be at least 1")
        with self._db() as db:
            work=db.execute("SELECT * FROM work_items WHERE work_id=?",(work_id,)).fetchone()
            if work is None: raise KeyError(work_id)
            if int(work["revision"] if "revision" in work.keys() else 1) != int(base_revision): raise ValueError("work revision changed; reload before revising")
            if work["status"] in {"completed","cancelled"}: raise ValueError("terminal Work cannot be revised")
            running=db.execute("SELECT 1 FROM work_steps WHERE work_id=? AND status='running' LIMIT 1",(work_id,)).fetchone()
            if running is not None: raise ValueError("Work cannot be revised while a step is running")
            completed=db.execute("SELECT MAX(ordinal) FROM work_steps WHERE work_id=? AND status='completed'",(work_id,)).fetchone()[0]
            if completed is not None and from_ordinal <= int(completed): raise ValueError("revision cannot replace completed Work history")
            rows=db.execute("SELECT * FROM work_steps WHERE work_id=? AND ordinal>=? ORDER BY ordinal",(work_id,from_ordinal)).fetchall()
            if not rows: raise ValueError("revision must replace an unfinished route")
            before=[{
                "step_id":r["step_id"],"ordinal":int(r["ordinal"]),"description":r["description"],"capability_id":r["capability_id"],
                "input":json.loads(r["input_json"] or "{}"),"status":r["status"],"occurrence_id":r["occurrence_id"],
                "output":None if not r["output_json"] else json.loads(r["output_json"]),"error":r["error"],
            } for r in rows]
            adaptation_id=f"adaptation_{uuid4().hex}";new_revision=int(base_revision)+1
            if work["status"] == "staged":
                db.execute("UPDATE work_items SET status='queued',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,))
            removed_step_ids=[r["step_id"] for r in rows]
            marks=",".join("?" for _ in removed_step_ids)
            db.execute(
                f"DELETE FROM obligation_bindings WHERE mechanism_kind='work_step' AND mechanism_id IN ({marks})",
                tuple(removed_step_ids),
            )
            db.execute("DELETE FROM work_steps WHERE work_id=? AND ordinal>=?",(work_id,from_ordinal))
            after=[]
            for offset,step in enumerate(replacement_steps):
                ordinal=from_ordinal+offset;cid=str(step.get("capability_id") or "").strip();inp=step.get("input") or {};desc=str(step.get("description") or cid).strip();step_id=f"step_{uuid4().hex}"
                db.execute("INSERT INTO work_steps(step_id,work_id,ordinal,description,capability_id,input_json,status) VALUES (?,?,?,?,?,?,'queued')",(step_id,work_id,ordinal,desc,cid,json.dumps(inp,sort_keys=True,separators=(",",":"),default=str)))
                obligation_ids=list(dict.fromkeys(str(value).strip() for value in (step.get("obligation_ids") or ()) if str(value).strip()))
                for obligation_id in obligation_ids:
                    db.execute(
                        "INSERT INTO obligation_bindings(binding_id,obligation_id,mechanism_kind,mechanism_id,work_id) VALUES (?,?,'work_step',?,?)",
                        (f"binding_{uuid4().hex}",obligation_id,step_id,work_id),
                    )
                after.append({"step_id":step_id,"ordinal":ordinal,"description":desc,"capability_id":cid,"input":inp,"obligation_ids":obligation_ids,"status":"queued"})
            db.execute("INSERT INTO work_adaptations(adaptation_id,work_id,base_revision,new_revision,from_ordinal,change_intent,reason,unchanged_goal,expected_impact,before_steps_json,after_steps_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(adaptation_id,work_id,int(base_revision),new_revision,from_ordinal,change_intent,reason,unchanged_goal,expected_impact,json.dumps(before,default=str,ensure_ascii=False),json.dumps(after,default=str,ensure_ascii=False)))
            db.execute("UPDATE work_items SET revision=?,status='queued',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(new_revision,work_id))
        return self.get(work_id)

    def cancel(self,work_id:str)->WorkItem:
        with self._db() as db:db.execute("UPDATE work_items SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=?",(work_id,));db.execute("UPDATE work_steps SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE work_id=? AND status NOT IN ('completed','failed')",(work_id,))
        return self.get(work_id)

def _step(r:sqlite3.Row)->WorkStep:return WorkStep(r["step_id"],r["work_id"],int(r["ordinal"]),r["description"],r["capability_id"],json.loads(r["input_json"] or "{}"),r["status"],r["occurrence_id"],None if not r["output_json"] else json.loads(r["output_json"]),r["error"],r["created_at"],r["updated_at"])
