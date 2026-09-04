from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class WorkStep:
    step_id:str;work_id:str;ordinal:int;description:str;capability_id:str;input:dict[str,Any];status:str;occurrence_id:str|None;output:Any;error:str|None;created_at:str;updated_at:str
    def as_dict(self)->dict[str,Any]:return {"step_id":self.step_id,"work_id":self.work_id,"ordinal":self.ordinal,"description":self.description,"capability_id":self.capability_id,"input":self.input,"status":self.status,"occurrence_id":self.occurrence_id,"output":self.output,"error":self.error,"created_at":self.created_at,"updated_at":self.updated_at}

@dataclass(frozen=True)
class WorkItem:
    work_id:str;objective:str;status:str;owner_principal_id:str;created_at:str;updated_at:str;metadata:dict[str,Any];display_ref:str|None=None;artifact_class:str|None=None;workflow_class:str|None=None;source_cadence_id:str|None=None;revision:int=1
    def as_dict(self)->dict[str,Any]:return {"work_id":self.work_id,"display_ref":self.display_ref,"artifact_class":self.artifact_class,"workflow_class":self.workflow_class,"objective":self.objective,"status":self.status,"owner_principal_id":self.owner_principal_id,"created_at":self.created_at,"updated_at":self.updated_at,"metadata":self.metadata,"source_cadence_id":self.source_cadence_id,"revision":self.revision}
