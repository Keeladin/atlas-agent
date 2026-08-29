from __future__ import annotations

import json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
from .models import ATLAS_PRINCIPAL_ID, AccountConnection, Principal, ServiceBinding

class IdentityError(ValueError): pass

def _dump(value:Any)->str:return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)

class IdentityStore:
    """Fresh provider-neutral identity/custody store. It contains no owner authority grants."""
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON");db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self,*,owner_display_name:str="Owner")->None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS principals(principal_id TEXT PRIMARY KEY,principal_kind TEXT NOT NULL CHECK(principal_kind IN ('atlas','human','service')),display_name TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','suspended','retired')),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE UNIQUE INDEX IF NOT EXISTS one_atlas_principal ON principals(principal_kind) WHERE principal_kind='atlas';
            CREATE TABLE IF NOT EXISTS identity_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_connections(connection_id TEXT PRIMARY KEY,provider_id TEXT NOT NULL,provider_subject_id TEXT NOT NULL,canonical_address TEXT NOT NULL,tenant_id TEXT,display_name TEXT NOT NULL,owner_principal_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','inactive','disconnected','revoked')),identity_profile_version TEXT NOT NULL,provider_metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(owner_principal_id) REFERENCES principals(principal_id),UNIQUE(provider_id,owner_principal_id,provider_subject_id));
            CREATE TABLE IF NOT EXISTS service_bindings(binding_id TEXT PRIMARY KEY,connection_id TEXT NOT NULL,service TEXT NOT NULL,channel TEXT NOT NULL,dispatch_ref TEXT NOT NULL,attested_operations_json TEXT NOT NULL,service_profile_version TEXT NOT NULL,health TEXT NOT NULL CHECK(health IN ('unknown','ready','degraded','down')),lifecycle TEXT NOT NULL CHECK(lifecycle IN ('pending_attest','connected','disabled')),attested_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(connection_id) REFERENCES account_connections(connection_id),UNIQUE(connection_id,service,channel));
            """)
            db.execute("INSERT OR IGNORE INTO principals(principal_id,principal_kind,display_name,status) VALUES (?,?,?,'active')",(ATLAS_PRINCIPAL_ID,"atlas","Atlas"))
            owner=db.execute("SELECT value FROM identity_settings WHERE key='current_human_principal_id'").fetchone()
            if owner is None:
                oid=f"principal_{uuid4().hex}";db.execute("INSERT INTO principals(principal_id,principal_kind,display_name,status) VALUES (?,?,?,'active')",(oid,"human",owner_display_name));db.execute("INSERT INTO identity_settings(key,value) VALUES ('current_human_principal_id',?)",(oid,))
    def principal(self,principal_id:str,*,require_active:bool=True)->Principal:
        with self._db() as db:row=db.execute("SELECT * FROM principals WHERE principal_id=?",(principal_id,)).fetchone()
        if row is None:raise IdentityError("principal_unknown")
        item=Principal(**dict(row));
        if require_active and item.status!="active":raise IdentityError("principal_inactive")
        return item
    def current_owner(self)->Principal:
        with self._db() as db:row=db.execute("SELECT value FROM identity_settings WHERE key='current_human_principal_id'").fetchone()
        if row is None:raise IdentityError("owner_not_configured")
        return self.principal(row["value"])
    def set_principal_display_name(self,principal_id:str,display_name:str)->Principal:
        name=str(display_name or "").strip()
        if not name:raise IdentityError("display_name_required")
        with self._db() as db:changed=db.execute("UPDATE principals SET display_name=? WHERE principal_id=?",(name,principal_id)).rowcount
        if changed!=1:raise IdentityError("principal_unknown")
        return self.principal(principal_id,require_active=False)
    def put_connection(self,*,provider_id:str,provider_subject_id:str,canonical_address:str,display_name:str,owner_principal_id:str,connection_id:str|None=None,tenant_id:str|None=None,status:str="active",identity_profile_version:str="1",provider_metadata:dict[str,Any]|None=None)->AccountConnection:
        self.principal(owner_principal_id); cid=connection_id or f"connection_{uuid4().hex}"
        with self._db() as db:
            existing=db.execute("SELECT connection_id FROM account_connections WHERE provider_id=? AND owner_principal_id=? AND provider_subject_id=?",(provider_id,owner_principal_id,provider_subject_id)).fetchone()
            if existing: cid=existing["connection_id"];db.execute("UPDATE account_connections SET canonical_address=?,tenant_id=?,display_name=?,status=?,identity_profile_version=?,provider_metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE connection_id=?",(canonical_address,tenant_id,display_name,status,identity_profile_version,_dump(provider_metadata or {}),cid))
            else:db.execute("INSERT INTO account_connections(connection_id,provider_id,provider_subject_id,canonical_address,tenant_id,display_name,owner_principal_id,status,identity_profile_version,provider_metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",(cid,provider_id,provider_subject_id,canonical_address,tenant_id,display_name,owner_principal_id,status,identity_profile_version,_dump(provider_metadata or {})))
        return self.connection(cid,require_active=False)
    def connection(self,connection_id:str,*,require_active:bool=True)->AccountConnection:
        with self._db() as db:row=db.execute("SELECT * FROM account_connections WHERE connection_id=?",(connection_id,)).fetchone()
        if row is None:raise IdentityError("connection_unknown")
        data=dict(row);data["provider_metadata"]=json.loads(data.pop("provider_metadata_json") or "{}");item=AccountConnection(**data)
        if require_active and item.status!="active":raise IdentityError("connection_inactive")
        return item
    def connections(self,*,owner_principal_id:str|None=None)->tuple[AccountConnection,...]:
        sql="SELECT connection_id FROM account_connections";args=()
        if owner_principal_id:sql+=" WHERE owner_principal_id=?";args=(owner_principal_id,)
        sql+=" ORDER BY display_name,connection_id"
        with self._db() as db:rows=db.execute(sql,args).fetchall()
        return tuple(self.connection(row["connection_id"],require_active=False) for row in rows)
    def set_connection_status(self,connection_id:str,status:str)->AccountConnection:
        with self._db() as db:changed=db.execute("UPDATE account_connections SET status=?,updated_at=CURRENT_TIMESTAMP WHERE connection_id=?",(status,connection_id)).rowcount
        if not changed:raise IdentityError("connection_unknown")
        return self.connection(connection_id,require_active=False)
    def put_service_binding(self,*,connection_id:str,service:str,channel:str,dispatch_ref:str,attested_operations:tuple[str,...],service_profile_version:str="1",health:str="ready",lifecycle:str="connected",attested_at:str|None=None,binding_id:str|None=None)->ServiceBinding:
        self.connection(connection_id,require_active=False);bid=binding_id or f"binding_{uuid4().hex}"
        with self._db() as db:
            existing=db.execute("SELECT binding_id FROM service_bindings WHERE connection_id=? AND service=? AND channel=?",(connection_id,service,channel)).fetchone()
            if existing:bid=existing["binding_id"];db.execute("UPDATE service_bindings SET dispatch_ref=?,attested_operations_json=?,service_profile_version=?,health=?,lifecycle=?,attested_at=?,updated_at=CURRENT_TIMESTAMP WHERE binding_id=?",(dispatch_ref,_dump(list(attested_operations)),service_profile_version,health,lifecycle,attested_at,bid))
            else:db.execute("INSERT INTO service_bindings(binding_id,connection_id,service,channel,dispatch_ref,attested_operations_json,service_profile_version,health,lifecycle,attested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(bid,connection_id,service,channel,dispatch_ref,_dump(list(attested_operations)),service_profile_version,health,lifecycle,attested_at))
        return self.service_binding(bid,require_connected=False)
    def service_binding(self,binding_id:str,*,require_connected:bool=True)->ServiceBinding:
        with self._db() as db:row=db.execute("SELECT * FROM service_bindings WHERE binding_id=?",(binding_id,)).fetchone()
        if row is None:raise IdentityError("service_binding_unknown")
        data=dict(row);data["attested_operations"]=tuple(json.loads(data.pop("attested_operations_json") or "[]"));item=ServiceBinding(**data)
        if require_connected and item.lifecycle!="connected":raise IdentityError("service_binding_inactive")
        return item
    def service_bindings(self,*,connection_id:str|None=None)->tuple[ServiceBinding,...]:
        sql="SELECT binding_id FROM service_bindings";args=()
        if connection_id:sql+=" WHERE connection_id=?";args=(connection_id,)
        sql+=" ORDER BY connection_id,service,channel"
        with self._db() as db:rows=db.execute(sql,args).fetchall()
        return tuple(self.service_binding(row["binding_id"],require_connected=False) for row in rows)
    def service_binding_for(self,connection_id:str,service:str,*,channel:str|None=None)->ServiceBinding:
        sql="SELECT binding_id FROM service_bindings WHERE connection_id=? AND service=? AND lifecycle='connected'";args:list[Any]=[connection_id,service]
        if channel:sql+=" AND channel=?";args.append(channel)
        sql+=" ORDER BY updated_at DESC LIMIT 1"
        with self._db() as db:row=db.execute(sql,args).fetchone()
        if row is None:raise IdentityError("service_binding_unknown")
        return self.service_binding(row["binding_id"])
