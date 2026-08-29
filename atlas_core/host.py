from __future__ import annotations

import json, os, re, shutil, subprocess
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

from atlas_core.actions import ActionResult,ActionStore
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution

_UNIT=re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")

def _iso()->str:return datetime.now(timezone.utc).isoformat()
def _unit(value:str)->str:
    unit=str(value or "").strip()
    if not _UNIT.fullmatch(unit):raise ValueError("unit must be an exact .service name")
    return unit

def _run(args:list[str],*,timeout:float=20)->subprocess.CompletedProcess[str]:
    return subprocess.run(args,text=True,capture_output=True,timeout=timeout,check=False)

class HostRuntime:
    """Deterministic host observation and user-systemd administration."""
    def __init__(self,registry:CapabilityRegistry,actions:ActionStore)->None:self.registry=registry;self.actions=actions;self._register()
    def _register(self)->None:
        empty={"type":"object","properties":{},"additionalProperties":False}
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.status.inspect","Inspect Atlas host runtime and process status.","inspect","none",empty,source="host",tags=("host","observation")),lambda p:ScopeResolution("host/status",{},"Inspect host status"),lambda p:self.status(),metadata={"scope_hint":"host/status"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.resources.inspect","Inspect CPU, memory and load telemetry.","inspect","none",empty,source="host",tags=("host","observation")),lambda p:ScopeResolution("host/resources",{},"Inspect host resources"),lambda p:self.resources(),metadata={"scope_hint":"host/resources"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.storage.inspect","Inspect filesystem capacity for Atlas and root filesystems.","inspect","none",empty,source="host",tags=("host","observation")),lambda p:ScopeResolution("host/storage",{},"Inspect host storage"),lambda p:self.storage(),metadata={"scope_hint":"host/storage"}),replace=True)
        fs_schema={"type":"object","required":["path"],"properties":{"path":{"type":"string"},"max_entries":{"type":"integer","minimum":1,"maximum":500}},"additionalProperties":False}
        for op in ("list","stat","read"):
            self.registry.register(CapabilityRegistration(CapabilityDefinition(f"host.filesystem.{op}",f"{op.title()} a host filesystem path using Atlas OS access.",op,"none",fs_schema,source="host",tags=("host","filesystem")),lambda p,_op=op:self._fs_scope(p,_op),lambda p,_op=op:self._fs_execute(_op,p),metadata={"scope_hint":"host/filesystem"}),replace=True)
        unit_schema={"type":"object","required":["unit"],"properties":{"unit":{"type":"string"}},"additionalProperties":False}
        logs_schema={"type":"object","required":["unit"],"properties":{"unit":{"type":"string"},"lines":{"type":"integer","minimum":1,"maximum":1000}},"additionalProperties":False}
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.status","Inspect an exact user-systemd service.","status","none",unit_schema,source="host",tags=("host","systemd")),lambda p:self._service_scope(p,"status"),lambda p:self.service_status(p),metadata={"scope_hint":"host/service"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.logs","Read journal entries for an exact user-systemd service.","logs","none",logs_schema,source="host",tags=("host","systemd")),lambda p:self._service_scope(p,"logs"),lambda p:self.service_logs(p),metadata={"scope_hint":"host/service"}),replace=True)
        for op in ("start","stop","restart"):
            self.registry.register(CapabilityRegistration(CapabilityDefinition(f"host.service.{op}",f"{op.title()} an exact user-systemd service.",op,"external",unit_schema,source="host",tags=("host","systemd","mutation")),lambda p,_op=op:self._service_scope(p,_op),lambda p,_op=op:self.service_mutate(_op,p),metadata={"scope_hint":"host/service"}),replace=True)
    def status(self)->ActionResult:
        out={"hostname":os.uname().nodename,"kernel":os.uname().release,"pid":os.getpid(),"uid":os.getuid(),"invocation_id":os.environ.get("INVOCATION_ID"),"timestamp":_iso()};return ActionResult(True,out,{"ok":True,"observed_at":out["timestamp"]})
    def resources(self)->ActionResult:
        mem={}
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                key,val=line.split(':',1);mem[key]=val.strip()
        except OSError:pass
        loads=os.getloadavg();out={"load_1":loads[0],"load_5":loads[1],"load_15":loads[2],"cpu_count":os.cpu_count(),"memory":{k:mem.get(k) for k in ("MemTotal","MemAvailable","SwapTotal","SwapFree")},"timestamp":_iso()};return ActionResult(True,out,{"ok":True,"observed_at":out["timestamp"]})
    def storage(self)->ActionResult:
        roots=[]
        for path in ('/',str(Path.cwd())):
            try:u=shutil.disk_usage(path);roots.append({"path":path,"total":u.total,"used":u.used,"free":u.free})
            except OSError:pass
        out={"filesystems":roots,"timestamp":_iso()};return ActionResult(True,out,{"ok":True,"observed_at":out["timestamp"]})
    def _fs_scope(self,payload:dict[str,Any],op:str)->ScopeResolution:
        path=Path(str(payload.get("path") or "")).expanduser()
        # strict=True for observation blocks policy path-smuggling through a symlink alias.
        canonical=path.resolve(strict=True);clean={"path":str(canonical)}
        if "max_entries" in payload:clean["max_entries"]=int(payload["max_entries"])
        return ScopeResolution("host/filesystem"+str(canonical),clean,f"{op.title()} {canonical}")
    def _fs_execute(self,op:str,payload:dict[str,Any])->ActionResult:
        try:
            path=Path(payload["path"])
            if op=="stat":
                s=path.stat();out={"path":str(path),"type":"directory" if path.is_dir() else "file" if path.is_file() else "other","size":s.st_size,"mode":oct(s.st_mode & 0o7777),"mtime_ns":s.st_mtime_ns,"uid":s.st_uid,"gid":s.st_gid}
            elif op=="list":
                if not path.is_dir():raise ValueError("path is not a directory")
                limit=int(payload.get("max_entries") or 100);entries=[]
                for child in sorted(path.iterdir(),key=lambda x:x.name)[:limit]:
                    try:s=child.lstat();entries.append({"name":child.name,"type":"symlink" if child.is_symlink() else "directory" if child.is_dir() else "file" if child.is_file() else "other","size":s.st_size})
                    except OSError as exc:entries.append({"name":child.name,"error":str(exc)})
                out={"path":str(path),"entries":entries,"truncated":len(entries)>=limit}
            else:
                if not path.is_file():raise ValueError("path is not a regular file")
                data=path.read_bytes()
                if len(data)>4*1024*1024:raise ValueError("host file exceeds 4 MiB read limit")
                out={"path":str(path),"content":data.decode('utf-8',errors='replace'),"bytes":len(data)}
            return ActionResult(True,out,{"ok":True,"operation":op,"path":str(path),"observed_at":_iso()})
        except Exception as exc:return ActionResult(False,receipt={"ok":False,"operation":op},error_code="host_filesystem_error",error=str(exc))
    def _service_scope(self,payload:dict[str,Any],op:str)->ScopeResolution:
        unit=_unit(payload.get("unit"));clean={"unit":unit}
        if "lines" in payload:clean["lines"]=int(payload["lines"])
        return ScopeResolution(f"host/service/{unit}",clean,f"{op.title()} user service {unit}")
    def service_status(self,payload:dict[str,Any])->ActionResult:
        unit=_unit(payload["unit"]);proc=_run(["systemctl","--user","show",unit,"--property=Id,LoadState,ActiveState,SubState,MainPID,InvocationID,ExecMainStatus","--no-pager"]);data={}
        for line in proc.stdout.splitlines():
            if '=' in line:k,v=line.split('=',1);data[k]=v
        ok=proc.returncode==0;return ActionResult(ok,{"unit":unit,"properties":data},{"ok":ok,"operation":"status","unit":unit,"returncode":proc.returncode,"observed_at":_iso()},None if ok else "systemd_status_failed",None if ok else proc.stderr.strip())
    def service_logs(self,payload:dict[str,Any])->ActionResult:
        unit=_unit(payload["unit"]);lines=int(payload.get("lines") or 100);proc=_run(["journalctl","--user-unit",unit,"-n",str(lines),"--no-pager","--output=short-iso"],timeout=30);ok=proc.returncode==0;return ActionResult(ok,{"unit":unit,"logs":proc.stdout},{"ok":ok,"operation":"logs","unit":unit,"returncode":proc.returncode,"observed_at":_iso()},None if ok else "journal_failed",None if ok else proc.stderr.strip())
    def service_mutate(self,op:str,payload:dict[str,Any])->ActionResult:
        unit=_unit(payload["unit"]);before_invocation=os.environ.get("INVOCATION_ID") if unit=="atlas-api.service" else None
        args=["systemctl","--user",op,unit,"--no-block"] if op in {"restart","stop","start"} else []
        proc=_run(args);ok=proc.returncode==0;receipt={"ok":ok,"operation":op,"unit":unit,"returncode":proc.returncode,"dispatched_at":_iso()}
        if ok and op=="restart" and unit=="atlas-api.service":receipt.update({"verification_pending":True,"predecessor_invocation_id":before_invocation})
        return ActionResult(ok,{"unit":unit,"dispatched":ok},receipt,None if ok else "systemd_mutation_failed",None if ok else proc.stderr.strip())
    def reconcile_self_restart(self)->tuple[dict[str,Any],...]:
        current=os.environ.get("INVOCATION_ID")
        if not current:return ()
        changed=[]
        for occurrence in self.actions.recent(limit=100):
            if occurrence.status!="uncertain" or occurrence.capability_id!="host.service.restart" or occurrence.scope!="host/service/atlas-api.service":continue
            predecessor=str(occurrence.receipt.get("predecessor_invocation_id") or "")
            if predecessor and predecessor!=current:
                result=self.actions.transition(occurrence.occurrence_id,from_status=("uncertain",),to_status="succeeded",receipt_json=json.dumps({**occurrence.receipt,"verified":True,"successor_invocation_id":current,"verified_at":_iso()}),completed_at=_iso());changed.append(result.public())
        return tuple(changed)
