from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
from .models import Cadence

CADENCE_DEFINITION_FIELDS=("name","objective","schedule","steps","kind","intake_root_id","max_candidates")

def _validate(candidate:dict[str,Any])->None:
    kind=str(candidate.get("kind") or "")
    if kind not in {"work_template","intake_sweep"}: raise ValueError("unknown cadence kind")
    if kind=="work_template" and not candidate.get("steps"): raise ValueError("work_template cadence requires steps")
    if kind=="intake_sweep" and not candidate.get("intake_root_id"): raise ValueError("intake_sweep cadence requires intake_root_id")
    if not 1<=int(candidate.get("max_candidates") or 0)<=250: raise ValueError("max_candidates must be between 1 and 250")

class CadenceStore:
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self)->None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cadences(cadence_id TEXT PRIMARY KEY,name TEXT NOT NULL,objective TEXT NOT NULL,schedule_json TEXT NOT NULL,steps_json TEXT NOT NULL,owner_principal_id TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,next_run_at TEXT,last_run_at TEXT,last_work_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns={row[1] for row in db.execute("PRAGMA table_info(cadences)")}
            additions={"kind":"TEXT NOT NULL DEFAULT 'work_template'","intake_root_id":"TEXT","max_candidates":"INTEGER NOT NULL DEFAULT 25","last_result_json":"TEXT","run_token":"TEXT","run_claimed_at":"TEXT"}
            for name,definition in additions.items():
                if name not in columns: db.execute(f"ALTER TABLE cadences ADD COLUMN {name} {definition}")
    def create(self,*,name:str,objective:str,schedule:dict[str,Any],steps:list[dict[str,Any]],owner_principal_id:str,next_run_at:str|None,kind:str="work_template",intake_root_id:str|None=None,max_candidates:int=25)->Cadence:
        _validate({"name":name,"objective":objective,"schedule":schedule,"steps":steps,"kind":kind,"intake_root_id":intake_root_id,"max_candidates":max_candidates})
        cid=f"cadence_{uuid4().hex}"
        with self._db() as db:db.execute("INSERT INTO cadences(cadence_id,name,objective,schedule_json,steps_json,owner_principal_id,next_run_at,kind,intake_root_id,max_candidates) VALUES (?,?,?,?,?,?,?,?,?,?)",(cid,name,objective,json.dumps(schedule,sort_keys=True,separators=(",",":")),json.dumps(steps,sort_keys=True,separators=(",",":"),default=str),owner_principal_id,next_run_at,kind,intake_root_id,int(max_candidates)))
        return self.get(cid)
    def get(self,cadence_id:str)->Cadence:
        with self._db() as db:r=db.execute("SELECT * FROM cadences WHERE cadence_id=?",(cadence_id,)).fetchone()
        if r is None:raise KeyError(cadence_id)
        return _cadence(r)
    def list(self)->tuple[Cadence,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM cadences ORDER BY name").fetchall()
        return tuple(_cadence(r) for r in rows)
    def claim_due(self,cadence_id:str,expected_next_run_at:str,token:str)->bool:
        with self._db() as db:
            changed=db.execute("UPDATE cadences SET run_token=?,run_claimed_at=CURRENT_TIMESTAMP WHERE cadence_id=? AND run_token IS NULL AND enabled=1 AND next_run_at=?",(token,cadence_id,expected_next_run_at)).rowcount
        return changed==1
    def claim_manual(self,cadence_id:str,token:str)->bool:
        with self._db() as db:changed=db.execute("UPDATE cadences SET run_token=?,run_claimed_at=CURRENT_TIMESTAMP WHERE cadence_id=? AND run_token IS NULL",(token,cadence_id)).rowcount
        return changed==1
    def release_claim(self,cadence_id:str,token:str)->None:
        with self._db() as db:db.execute("UPDATE cadences SET run_token=NULL,run_claimed_at=NULL WHERE cadence_id=? AND run_token=?",(cadence_id,token))
    def due(self,now_iso:str)->tuple[Cadence,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM cadences WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at",(now_iso,)).fetchall()
        return tuple(_cadence(r) for r in rows)
    def update(self,cadence_id:str,patch:dict[str,Any],*,next_run_at:str|None)->Cadence:
        unknown=set(patch)-set(CADENCE_DEFINITION_FIELDS)
        if unknown:raise ValueError(f"unsupported cadence fields: {sorted(unknown)}")
        current=self.get(cadence_id)
        candidate={field:getattr(current,field) for field in CADENCE_DEFINITION_FIELDS}
        candidate.update(patch)
        _validate(candidate)
        with self._db() as db:db.execute("UPDATE cadences SET name=?,objective=?,schedule_json=?,steps_json=?,kind=?,intake_root_id=?,max_candidates=?,next_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE cadence_id=?",(candidate["name"],candidate["objective"],json.dumps(candidate["schedule"],sort_keys=True,separators=(",",":")),json.dumps(candidate["steps"],sort_keys=True,separators=(",",":"),default=str),candidate["kind"],candidate["intake_root_id"],int(candidate["max_candidates"]),next_run_at,cadence_id))
        return self.get(cadence_id)
    def mark_run(self,cadence_id:str,*,last_run_at:str,last_work_id:str|None,next_run_at:str,last_result:dict[str,Any]|None=None)->Cadence:
        with self._db() as db:db.execute("UPDATE cadences SET last_run_at=?,last_work_id=?,next_run_at=?,last_result_json=?,run_token=NULL,run_claimed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE cadence_id=?",(last_run_at,last_work_id,next_run_at,None if last_result is None else json.dumps(last_result,sort_keys=True,separators=(",",":"),default=str),cadence_id))
        return self.get(cadence_id)
    def mark_manual_run(self,cadence_id:str,*,last_run_at:str,last_work_id:str|None,last_result:dict[str,Any]|None=None)->Cadence:
        """Record an owner-triggered run without advancing the schedule."""
        with self._db() as db:db.execute("UPDATE cadences SET last_run_at=?,last_work_id=?,last_result_json=?,run_token=NULL,run_claimed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE cadence_id=?",(last_run_at,last_work_id,None if last_result is None else json.dumps(last_result,sort_keys=True,separators=(",",":"),default=str),cadence_id))
        return self.get(cadence_id)
    def set_enabled(self,cadence_id:str,enabled:bool,next_run_at:str|None)->Cadence:
        with self._db() as db:db.execute("UPDATE cadences SET enabled=?,next_run_at=?,updated_at=CURRENT_TIMESTAMP WHERE cadence_id=?",(1 if enabled else 0,next_run_at,cadence_id))
        return self.get(cadence_id)
    def delete(self,cadence_id:str)->None:
        with self._db() as db:db.execute("DELETE FROM cadences WHERE cadence_id=?",(cadence_id,))

def _cadence(r:sqlite3.Row)->Cadence:
    keys=set(r.keys())
    result=json.loads(r["last_result_json"]) if "last_result_json" in keys and r["last_result_json"] else None
    return Cadence(r["cadence_id"],r["name"],r["objective"],json.loads(r["schedule_json"]),json.loads(r["steps_json"]),r["owner_principal_id"],bool(r["enabled"]),r["next_run_at"],r["last_run_at"],r["last_work_id"],r["created_at"],r["updated_at"],r["kind"] if "kind" in keys else "work_template",r["intake_root_id"] if "intake_root_id" in keys else None,int(r["max_candidates"] if "max_candidates" in keys else 25),result)
