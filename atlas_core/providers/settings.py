from __future__ import annotations

import json,sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class ProviderSettings:
    key:str;kind:str;model:str;base_url:str|None;enabled:bool;local:bool;priority:int;credential_ref:str|None;metadata:dict[str,Any];updated_at:str
    def public(self)->dict[str,Any]:return {"key":self.key,"kind":self.kind,"model":self.model,"base_url":self.base_url,"enabled":self.enabled,"local":self.local,"priority":self.priority,"credential_configured":bool(self.credential_ref),"metadata":self.metadata,"updated_at":self.updated_at}

class ProviderSettingsStore:
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self)->None:
        with self._db() as db:db.execute("""CREATE TABLE IF NOT EXISTS provider_settings(key TEXT PRIMARY KEY,kind TEXT NOT NULL,model TEXT NOT NULL,base_url TEXT,enabled INTEGER NOT NULL DEFAULT 1,local INTEGER NOT NULL DEFAULT 0,priority INTEGER NOT NULL DEFAULT 50,credential_ref TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    def put(self,*,key:str,kind:str,model:str,base_url:str|None=None,enabled:bool=True,local:bool=False,priority:int=50,credential_ref:str|None=None,metadata:dict[str,Any]|None=None)->ProviderSettings:
        if not key.strip() or not kind.strip() or not model.strip():raise ValueError("provider key/kind/model are required")
        with self._db() as db:db.execute("""INSERT INTO provider_settings(key,kind,model,base_url,enabled,local,priority,credential_ref,metadata_json) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,model=excluded.model,base_url=excluded.base_url,enabled=excluded.enabled,local=excluded.local,priority=excluded.priority,credential_ref=excluded.credential_ref,metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP""",(key,kind,model,base_url,1 if enabled else 0,1 if local else 0,int(priority),credential_ref,json.dumps(metadata or {},sort_keys=True,separators=(",",":"))))
        return self.get(key)
    def get(self,key:str)->ProviderSettings:
        with self._db() as db:row=db.execute("SELECT * FROM provider_settings WHERE key=?",(key,)).fetchone()
        if row is None:raise KeyError(key)
        return _settings(row)
    def all(self)->tuple[ProviderSettings,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM provider_settings ORDER BY priority DESC,key").fetchall()
        return tuple(_settings(row) for row in rows)
    def delete(self,key:str)->None:
        with self._db() as db:db.execute("DELETE FROM provider_settings WHERE key=?",(key,))
    def seed_local(self)->None:
        with self._db() as db:row=db.execute("SELECT 1 FROM provider_settings LIMIT 1").fetchone()
        if row is None:self.put(key="atlas-local",kind="openai_compatible",model="atlas",base_url="http://127.0.0.1:1234",enabled=True,local=True,priority=100)

def _settings(row:sqlite3.Row)->ProviderSettings:return ProviderSettings(row["key"],row["kind"],row["model"],row["base_url"],bool(row["enabled"]),bool(row["local"]),int(row["priority"]),row["credential_ref"],json.loads(row["metadata_json"] or "{}"),row["updated_at"])
