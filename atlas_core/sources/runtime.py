from __future__ import annotations

import ctypes, errno, hashlib, json, os, re, sqlite3, stat, uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_core.actions import ActionResult
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution
from .errors import LocalSourceError
from .local import LocalRootConfig,LocalRootRegistry,LocalSourceKernel,validate_component,validate_relative_path

_RENAME_NOREPLACE=1
_IDENTIFIER=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

@dataclass(frozen=True)
class SourceRoot:
    root_id:str;provider_namespace:str;host_path:str;display_name:str;quarantine_relative_path:str|None;enabled:bool;updated_at:str
    def public(self)->dict[str,Any]:return self.__dict__.copy()

class SourceRootStore:
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self)->None:
        with self._db() as db:db.execute("""CREATE TABLE IF NOT EXISTS source_roots(root_id TEXT PRIMARY KEY,provider_namespace TEXT NOT NULL DEFAULT 'local',host_path TEXT NOT NULL,display_name TEXT NOT NULL,quarantine_relative_path TEXT,enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    def put(self,*,root_id:str,host_path:str,display_name:str|None=None,provider_namespace:str="local",quarantine_relative_path:str|None=".atlas-quarantine",enabled:bool=True)->SourceRoot:
        if not _IDENTIFIER.fullmatch(root_id) or not _IDENTIFIER.fullmatch(provider_namespace):raise ValueError("source root and provider namespace must be safe identifiers")
        path=os.path.realpath(os.path.expanduser(host_path));
        if not os.path.isabs(path) or not os.path.isdir(path):raise ValueError("source root must be an existing absolute directory")
        if os.path.islink(host_path):raise ValueError("source root must not be a symlink")
        if quarantine_relative_path:
            q=validate_relative_path(quarantine_relative_path)
            if q==".":raise ValueError("quarantine must be below root")
            qpath=os.path.join(path,q);os.makedirs(qpath,mode=0o700,exist_ok=True)
            if os.path.islink(qpath):raise ValueError("quarantine must not be a symlink")
            quarantine_relative_path=q
        with self._db() as db:db.execute("""INSERT INTO source_roots(root_id,provider_namespace,host_path,display_name,quarantine_relative_path,enabled) VALUES (?,?,?,?,?,?) ON CONFLICT(root_id) DO UPDATE SET provider_namespace=excluded.provider_namespace,host_path=excluded.host_path,display_name=excluded.display_name,quarantine_relative_path=excluded.quarantine_relative_path,enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP""",(root_id,provider_namespace,path,display_name or root_id,quarantine_relative_path,1 if enabled else 0))
        return self.get(root_id)
    def get(self,root_id:str)->SourceRoot:
        with self._db() as db:row=db.execute("SELECT * FROM source_roots WHERE root_id=?",(root_id,)).fetchone()
        if row is None:raise KeyError(root_id)
        return SourceRoot(row["root_id"],row["provider_namespace"],row["host_path"],row["display_name"],row["quarantine_relative_path"],bool(row["enabled"]),row["updated_at"])
    def all(self)->tuple[SourceRoot,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM source_roots ORDER BY display_name").fetchall()
        return tuple(SourceRoot(r["root_id"],r["provider_namespace"],r["host_path"],r["display_name"],r["quarantine_relative_path"],bool(r["enabled"]),r["updated_at"]) for r in rows)
    def delete(self,root_id:str)->None:
        with self._db() as db:db.execute("DELETE FROM source_roots WHERE root_id=?",(root_id,))

class SourceRuntime:
    """Hardened local-files capability provider. Runtime policy, not root config, owns authority."""
    def __init__(self,store:SourceRootStore,capabilities:CapabilityRegistry)->None:
        self.store=store;self.capabilities=capabilities;self.registry:LocalRootRegistry|None=None;self.kernel:LocalSourceKernel|None=None;self.reload()
    def reload(self)->None:
        if self.registry is not None:self.registry.close()
        self.capabilities.unregister_prefix("files.");registry=LocalRootRegistry()
        for row in self.store.all():
            if not row.enabled:continue
            # read_allowed/mutation_allowed are technical enrollment only here: every enrolled root
            # exposes both classes of kernel operation. Owner discretion is exclusively policy.
            registry.register(LocalRootConfig(root_id=row.root_id,provider_namespace=row.provider_namespace,host_path=row.host_path,display_name=row.display_name,read_allowed=True,mutation_allowed=True,allow_cross_mounts=False,configuration_revision=f"runtime-{hashlib.sha256((row.host_path+row.updated_at).encode()).hexdigest()}",quarantine_relative_path=row.quarantine_relative_path))
        self.registry=registry;self.kernel=LocalSourceKernel(registry);self._register_capabilities()
    def public_state(self)->tuple[dict[str,Any],...]:return tuple(r.public() for r in self.store.all())
    def _root(self,payload:dict[str,Any]):
        root_id=str(payload.get("root_id") or "").strip();relative=validate_relative_path(str(payload.get("relative_path") or "."))
        if not root_id:raise ValueError("root_id is required")
        row=self.store.get(root_id)
        if not row.enabled:raise ValueError("source root is disabled")
        return row,relative
    def _scope(self,payload:dict[str,Any],summary:str)->ScopeResolution:
        row,relative=self._root(payload);clean=dict(payload);clean["root_id"]=row.root_id;clean["relative_path"]=relative
        scope=f"files/{row.provider_namespace}/{row.root_id}" if relative=="." else f"files/{row.provider_namespace}/{row.root_id}/{relative}";return ScopeResolution(scope,clean,summary)
    def _register_capabilities(self)->None:
        schemas={
            "list":{"type":"object","required":["root_id"],"properties":{"root_id":{"type":"string"},"relative_path":{"type":"string"},"page_size":{"type":"integer","minimum":1,"maximum":500},"cursor":{"type":["string","null"]}},"additionalProperties":False},
            "read":{"type":"object","required":["root_id","relative_path"],"properties":{"root_id":{"type":"string"},"relative_path":{"type":"string"}},"additionalProperties":False},
            "copy":{"type":"object","required":["root_id","relative_path","destination_path"],"properties":{"root_id":{"type":"string"},"relative_path":{"type":"string"},"destination_path":{"type":"string"}},"additionalProperties":False},
            "move":{"type":"object","required":["root_id","relative_path","destination_path"],"properties":{"root_id":{"type":"string"},"relative_path":{"type":"string"},"destination_path":{"type":"string"}},"additionalProperties":False},
            "delete":{"type":"object","required":["root_id","relative_path"],"properties":{"root_id":{"type":"string"},"relative_path":{"type":"string"}},"additionalProperties":False},
            "restore":{"type":"object","required":["root_id","quarantine_name","destination_path"],"properties":{"root_id":{"type":"string"},"quarantine_name":{"type":"string"},"destination_path":{"type":"string"}},"additionalProperties":False},
        }
        for op in ("list","stat","hash","read"):
            schema=schemas["list"] if op=="list" else schemas["read"]
            self.capabilities.register(CapabilityRegistration(CapabilityDefinition(f"files.{op}",f"{op.title()} content from an enrolled local source.",op,"none",schema,source="files",tags=("files","local")),lambda p,_op=op:self._scope(p,f"{_op.title()} local source"),lambda p,_op=op:self._read_execute(_op,p),metadata={"scope_hint":"files"}),replace=True)
        self.capabilities.register(CapabilityRegistration(CapabilityDefinition("files.copy","Copy a regular file without overwriting an existing destination.","copy","reversible",schemas["copy"],source="files",tags=("files","local")),lambda p:self._mutation_scope(p,"copy"),lambda p:self._copy(p),metadata={"scope_hint":"files"}),replace=True)
        self.capabilities.register(CapabilityRegistration(CapabilityDefinition("files.move","Move a regular file within one enrolled root without overwrite.","move","reversible",schemas["move"],source="files",tags=("files","local")),lambda p:self._mutation_scope(p,"move"),lambda p:self._move(p),metadata={"scope_hint":"files"}),replace=True)
        self.capabilities.register(CapabilityRegistration(CapabilityDefinition("files.rename","Rename a regular file within one enrolled root without overwrite.","rename","reversible",schemas["move"],source="files",tags=("files","local")),lambda p:self._mutation_scope(p,"rename"),lambda p:self._move(p,rename_only=True),metadata={"scope_hint":"files"}),replace=True)
        self.capabilities.register(CapabilityRegistration(CapabilityDefinition("files.delete","Move a regular file into managed quarantine.","delete","reversible",schemas["delete"],source="files",tags=("files","local")),lambda p:self._scope(p,"Quarantine local file"),lambda p:self._delete(p),metadata={"scope_hint":"files"}),replace=True)
        self.capabilities.register(CapabilityRegistration(CapabilityDefinition("files.restore","Restore a quarantined file without overwriting the destination.","restore","reversible",schemas["restore"],source="files",tags=("files","local")),lambda p:self._restore_scope(p),lambda p:self._restore(p),metadata={"scope_hint":"files"}),replace=True)
    def _revision(self,row:SourceRoot)->str:
        root=self.registry.get(row.provider_namespace,row.root_id);return root.config.configuration_revision
    def _read_execute(self,op:str,payload:dict[str,Any])->ActionResult:
        try:
            row,rel=self._root(payload);kw={"configuration_revision":self._revision(row)};k=self.kernel
            if op=="list":r=k.list(row.provider_namespace,row.root_id,rel,page_size=int(payload.get("page_size") or 100),cursor=payload.get("cursor"),**kw);out=r.to_dict()
            elif op=="stat":out=k.stat(row.provider_namespace,row.root_id,rel,**kw).to_dict()
            elif op=="hash":out=k.hash(row.provider_namespace,row.root_id,rel,**kw).to_dict()
            else:out=k.read(row.provider_namespace,row.root_id,rel,**kw).to_dict()
            return ActionResult(True,out,{"ok":True,"provider":"local_files","operation":op,"root_id":row.root_id,"relative_path":rel})
        except LocalSourceError as exc:return ActionResult(False,receipt={"ok":False,"provider":"local_files"},error_code=exc.code,error=exc.message)
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"provider":"local_files"},error_code="files_error",error=str(exc))
    def _mutation_scope(self,payload:dict[str,Any],op:str)->ScopeResolution:
        row,src=self._root(payload);dst=validate_relative_path(str(payload.get("destination_path") or ""));clean={"root_id":row.root_id,"relative_path":src,"destination_path":dst};return ScopeResolution(f"files/{row.provider_namespace}/{row.root_id}/{src}",clean,f"{op.title()} {src} to {dst}")
    def _restore_scope(self,payload:dict[str,Any])->ScopeResolution:
        row=self.store.get(str(payload.get("root_id") or ""));name=str(payload.get("quarantine_name") or "");validate_component(name);dst=validate_relative_path(str(payload.get("destination_path") or ""));return ScopeResolution(f"files/{row.provider_namespace}/{row.root_id}/{dst}",{"root_id":row.root_id,"quarantine_name":name,"destination_path":dst},f"Restore quarantined file to {dst}")
    def _parent(self,root_fd:int,path:str)->tuple[int,str]:
        clean=validate_relative_path(path)
        if clean==".":raise ValueError("file path is required")
        parts=clean.split('/');name=parts.pop();validate_component(name);fd=os.dup(root_fd)
        try:
            for component in parts:
                validate_component(component);next_fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=next_fd
            return fd,name
        except Exception:
            os.close(fd);raise
    def _require_regular(self,parent:int,name:str)->os.stat_result:
        info=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):raise ValueError("source must be a regular file")
        return info
    def _rename_noreplace(self,src_parent:int,src_name:str,dst_parent:int,dst_name:str)->None:
        libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,"renameat2",None)
        if fn is None:raise RuntimeError("renameat2 is required for no-overwrite mutation")
        rc=fn(src_parent,src_name.encode(),dst_parent,dst_name.encode(),_RENAME_NOREPLACE)
        if rc!=0:
            e=ctypes.get_errno();raise OSError(e,os.strerror(e))
    def _copy(self,payload:dict[str,Any])->ActionResult:
        try:
            row,src=self._root(payload);dst=validate_relative_path(payload["destination_path"]);root=self.registry.get(row.provider_namespace,row.root_id);sp,sn=self._parent(root.fd,src);dp,dn=self._parent(root.fd,dst);self._require_regular(sp,sn);tmp=f".atlas-copy-{uuid.uuid4().hex}";digest=hashlib.sha256();size=0
            try:
                sfd=os.open(sn,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=sp);dfd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600,dir_fd=dp)
                try:
                    while True:
                        chunk=os.read(sfd,65536)
                        if not chunk:break
                        digest.update(chunk);size+=len(chunk);view=memoryview(chunk)
                        while view:view=view[os.write(dfd,view):]
                    os.fsync(dfd)
                finally:os.close(sfd);os.close(dfd)
                self._rename_noreplace(dp,tmp,dp,dn);os.fsync(dp)
            finally:
                try:os.unlink(tmp,dir_fd=dp)
                except OSError:pass
                os.close(sp);os.close(dp)
            return ActionResult(True,{"root_id":row.root_id,"source":src,"destination":dst,"byte_size":size,"sha256":digest.hexdigest()},{"ok":True,"operation":"copy","root_id":row.root_id,"source":src,"destination":dst})
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"operation":"copy"},error_code="files_copy_failed",error=str(exc))
    def _move(self,payload:dict[str,Any],rename_only:bool=False)->ActionResult:
        op="rename" if rename_only else "move"
        try:
            row,src=self._root(payload);dst=validate_relative_path(payload["destination_path"]);root=self.registry.get(row.provider_namespace,row.root_id);sp,sn=self._parent(root.fd,src);dp,dn=self._parent(root.fd,dst);self._require_regular(sp,sn)
            try:
                if rename_only and sp!=dp:
                    # File-descriptor numbers are not useful for same-parent identity; compare stat.
                    src_dir=os.fstat(sp);dst_dir=os.fstat(dp)
                    if (src_dir.st_dev,src_dir.st_ino)!=(dst_dir.st_dev,dst_dir.st_ino):raise ValueError("rename requires same parent directory")
                self._rename_noreplace(sp,sn,dp,dn);os.fsync(sp);os.fsync(dp)
            finally:os.close(sp);os.close(dp)
            return ActionResult(True,{"root_id":row.root_id,"source":src,"destination":dst},{"ok":True,"operation":op,"root_id":row.root_id,"source":src,"destination":dst})
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"operation":op},error_code=f"files_{op}_failed",error=str(exc))
    def _delete(self,payload:dict[str,Any])->ActionResult:
        try:
            row,src=self._root(payload);root=self.registry.get(row.provider_namespace,row.root_id)
            if root.quarantine_fd is None:raise ValueError("managed quarantine is unavailable")
            sp,sn=self._parent(root.fd,src);self._require_regular(sp,sn);qn=f"quarantine-{uuid.uuid4().hex}"
            try:self._rename_noreplace(sp,sn,root.quarantine_fd,qn);os.fsync(sp);os.fsync(root.quarantine_fd)
            finally:os.close(sp)
            token={"root_id":row.root_id,"quarantine_name":qn,"original_path":src};return ActionResult(True,{"quarantined":True,"recovery_token":token},{"ok":True,"operation":"delete","root_id":row.root_id,"source":src,"recovery_token":token})
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"operation":"delete"},error_code="files_delete_failed",error=str(exc))
    def _restore(self,payload:dict[str,Any])->ActionResult:
        try:
            row=self.store.get(payload["root_id"]);root=self.registry.get(row.provider_namespace,row.root_id);qn=str(payload["quarantine_name"]);validate_component(qn);dst=validate_relative_path(payload["destination_path"])
            if root.quarantine_fd is None:raise ValueError("managed quarantine is unavailable")
            self._require_regular(root.quarantine_fd,qn);dp,dn=self._parent(root.fd,dst)
            try:self._rename_noreplace(root.quarantine_fd,qn,dp,dn);os.fsync(root.quarantine_fd);os.fsync(dp)
            finally:os.close(dp)
            return ActionResult(True,{"restored":True,"root_id":row.root_id,"destination":dst},{"ok":True,"operation":"restore","root_id":row.root_id,"destination":dst})
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"operation":"restore"},error_code="files_restore_failed",error=str(exc))
