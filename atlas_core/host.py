from __future__ import annotations

import json, os, re, shutil, socket, stat, subprocess
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

from atlas_core.actions import ActionResult,ActionStore
from atlas_core.capabilities import CapabilityDefinition,CapabilityRegistration,CapabilityRegistry,ScopeResolution

_UNIT=re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
_PACKAGE=re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
_PACKAGE_SOCKET=Path("/run/atlas-package-broker/control.sock")

def _iso()->str:return datetime.now(timezone.utc).isoformat()
def _unit(value:str)->str:
    unit=str(value or "").strip()
    if not _UNIT.fullmatch(unit):raise ValueError("unit must be an exact .service name")
    return unit
def _package(value:str)->str:
    package=str(value or "").strip().casefold()
    if not _PACKAGE.fullmatch(package):raise ValueError("package must be an exact Debian package name")
    return package
def _trusted_package_broker()->tuple[bool,str]:
    try:
        info=_PACKAGE_SOCKET.stat()
    except OSError:return False,"privileged_package_broker_not_installed"
    mode=info.st_mode
    if not stat.S_ISSOCK(mode) or info.st_uid!=0 or mode & 0o007 or mode & 0o060 != 0o060:
        return False,"privileged_package_broker_not_trusted"
    return True,"available"

def _run(args:list[str],*,timeout:float=20)->subprocess.CompletedProcess[str]:
    return subprocess.run(args,text=True,capture_output=True,timeout=timeout,check=False)

class HostRuntime:
    """Deterministic host observation and user-systemd administration."""
    def __init__(self,registry:CapabilityRegistry,actions:ActionStore,*,protected_paths:tuple[Path,...]=())->None:
        self.registry=registry;self.actions=actions
        self.protected_paths=tuple(path.expanduser().resolve(strict=False) for path in protected_paths)
        self._register()
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
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.status","Inspect an exact service in the Atlas user-systemd manager only; do not use for system services such as tailscaled.service.","status","none",unit_schema,source="host",tags=("host","systemd","user-service")),lambda p:self._service_scope(p,"status"),lambda p:self.service_status(p),metadata={"scope_hint":"host/service"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.logs","Read journal entries for an exact service in the Atlas user-systemd manager only.","logs","none",logs_schema,source="host",tags=("host","systemd","user-service")),lambda p:self._service_scope(p,"logs"),lambda p:self.service_logs(p),metadata={"scope_hint":"host/service"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.system.status","Inspect an exact system-level systemd service such as tailscaled.service, docker.service or caddy.service.","status","none",unit_schema,source="host",tags=("host","systemd","system-service","observation")),lambda p:self._system_service_scope(p,"status"),lambda p:self.system_service_status(p),metadata={"scope_hint":"host/service/system"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.service.system.logs","Read journal entries for an exact system-level systemd service when host permissions allow it.","logs","none",logs_schema,source="host",tags=("host","systemd","system-service","observation")),lambda p:self._system_service_scope(p,"logs"),lambda p:self.system_service_logs(p),metadata={"scope_hint":"host/service/system"}),replace=True)
        for op in ("start","stop","restart"):
            self.registry.register(CapabilityRegistration(CapabilityDefinition(f"host.service.{op}",f"{op.title()} an exact user-systemd service.",op,"external",unit_schema,source="host",tags=("host","systemd","mutation")),lambda p,_op=op:self._service_scope(p,_op),lambda p,_op=op:self.service_mutate(_op,p),metadata={"scope_hint":"host/service"}),replace=True)
        package_schema={"type":"object","required":["package"],"properties":{"package":{"type":"string","minLength":1,"maxLength":128}},"additionalProperties":False}
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.package.inspect","Inspect installation and candidate version state for an exact Debian package.","inspect","none",package_schema,source="host",tags=("host","package","observation")),lambda p:self._package_scope(p,"inspect"),lambda p:self.package_inspect(p),metadata={"scope_hint":"host/package"}),replace=True)
        for op,effect in (("install","external"),("remove","destructive")):
            self.registry.register(CapabilityRegistration(CapabilityDefinition(f"host.package.{op}",f"{op.title()} an exact Debian package from configured APT sources.",op,effect,package_schema,source="host",tags=("host","package","mutation")),lambda p,_op=op:self._package_scope(p,_op),lambda p,_op=op:self.package_mutate(_op,p),availability=_trusted_package_broker,metadata={"scope_hint":"host/package"}),replace=True)
        self.registry.register(CapabilityRegistration(CapabilityDefinition("host.package.refresh","Refresh configured APT package metadata.","refresh","external",empty,source="host",tags=("host","package","mutation")),lambda p:ScopeResolution("host/package/index",{},"Refresh APT package metadata"),lambda p:self.package_refresh(),availability=_trusted_package_broker,metadata={"scope_hint":"host/package/index"}),replace=True)
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
        canonical=path.resolve(strict=True)
        if any(canonical == protected or protected in canonical.parents for protected in self.protected_paths):
            raise ValueError("path is outside the host filesystem capability boundary")
        clean={"path":str(canonical)}
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
    def _system_service_scope(self,payload:dict[str,Any],op:str)->ScopeResolution:
        unit=_unit(payload.get("unit"));clean={"unit":unit}
        if "lines" in payload:clean["lines"]=int(payload["lines"])
        return ScopeResolution(f"host/service/system/{unit}",clean,f"{op.title()} system service {unit}")
    def system_service_status(self,payload:dict[str,Any])->ActionResult:
        unit=_unit(payload["unit"]);proc=_run(["systemctl","show",unit,"--property=Id,LoadState,ActiveState,SubState,MainPID,InvocationID,ExecMainStatus","--no-pager"]);data={}
        for line in proc.stdout.splitlines():
            if '=' in line:k,v=line.split('=',1);data[k]=v
        ok=proc.returncode==0;return ActionResult(ok,{"unit":unit,"manager":"system","properties":data},{"ok":ok,"operation":"status","manager":"system","unit":unit,"returncode":proc.returncode,"observed_at":_iso()},None if ok else "systemd_system_status_failed",None if ok else proc.stderr.strip())
    def system_service_logs(self,payload:dict[str,Any])->ActionResult:
        unit=_unit(payload["unit"]);lines=int(payload.get("lines") or 100);proc=_run(["journalctl","--unit",unit,"-n",str(lines),"--no-pager","--output=short-iso"],timeout=30);ok=proc.returncode==0;return ActionResult(ok,{"unit":unit,"manager":"system","logs":proc.stdout},{"ok":ok,"operation":"logs","manager":"system","unit":unit,"returncode":proc.returncode,"observed_at":_iso()},None if ok else "systemd_system_journal_failed",None if ok else proc.stderr.strip())
    def _package_scope(self,payload:dict[str,Any],op:str)->ScopeResolution:
        package=_package(payload.get("package"));return ScopeResolution(f"host/package/{package}",{"package":package},f"{op.title()} package {package}")
    def package_inspect(self,payload:dict[str,Any])->ActionResult:
        package=_package(payload["package"]);installed=False;installed_version=None
        query=_run(["/usr/bin/dpkg-query","-W","-f=${Status}\n${Version}\n",package])
        if query.returncode==0:
            lines=query.stdout.splitlines();installed=bool(lines and lines[0].strip()=="install ok installed");installed_version=lines[1].strip() if installed and len(lines)>1 else None
        policy=_run(["/usr/bin/apt-cache","policy",package]);candidate=None
        for line in policy.stdout.splitlines():
            if line.strip().startswith("Candidate:"):
                value=line.split(":",1)[1].strip();candidate=None if value=="(none)" else value;break
        ok=policy.returncode==0;out={"package":package,"installed":installed,"installed_version":installed_version,"candidate_version":candidate}
        return ActionResult(ok,out,{"ok":ok,"operation":"inspect","package":package,"observed_at":_iso()},None if ok else "host_package_inspect_failed",None if ok else policy.stderr.strip())
    def package_mutate(self,op:str,payload:dict[str,Any])->ActionResult:
        package=_package(payload["package"]);available,reason=_trusted_package_broker()
        if not available:return ActionResult(False,receipt={"ok":False,"operation":op,"package":package},error_code="host_package_broker_unavailable",error=reason)
        return self._package_broker_result(op,package,self._package_broker_call(op,package))
    def package_refresh(self)->ActionResult:
        available,reason=_trusted_package_broker()
        if not available:return ActionResult(False,receipt={"ok":False,"operation":"refresh"},error_code="host_package_broker_unavailable",error=reason)
        return self._package_broker_result("refresh",None,self._package_broker_call("refresh",None))
    @staticmethod
    def _package_broker_call(op:str,package:str|None)->dict[str,Any]:
        request={"operation":op};
        if package is not None:request["package"]=package
        data=json.dumps(request,separators=(",",":")).encode()+b"\n";chunks=bytearray()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(900);client.connect(str(_PACKAGE_SOCKET));client.sendall(data);client.shutdown(socket.SHUT_WR)
            while len(chunks)<=65536:
                part=client.recv(4096)
                if not part:break
                chunks.extend(part)
                if b"\n" in part:break
        if len(chunks)>65536:raise RuntimeError("package broker response exceeded limit")
        parsed=json.loads(bytes(chunks).split(b"\n",1)[0].decode("utf-8"))
        if not isinstance(parsed,dict):raise RuntimeError("package broker returned invalid response")
        return parsed
    @staticmethod
    def _package_broker_result(op:str,package:str|None,payload:dict[str,Any])->ActionResult:
        ok=payload.get("ok") is True;receipt={"ok":ok,"operation":op,"completed_at":_iso()}
        if package:receipt["package"]=package
        if ok:return ActionResult(True,payload,receipt)
        detail=str(payload.get("error") or payload.get("stderr_tail") or "package operation failed").strip()[:4000]
        return ActionResult(False,payload,receipt,"host_package_operation_failed",detail)
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
