from __future__ import annotations

import hashlib, json
from typing import Any
from uuid import uuid4

class TaskStoreError(RuntimeError): pass
class UnknownRecordError(TaskStoreError): pass
class InvalidTransitionError(TaskStoreError): pass

def _new_id(prefix: str) -> str: return f"{prefix}_{uuid4().hex}"
def _json_dump(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def _json_load(value: str | None, default: Any) -> Any: return default if value in (None, "") else json.loads(value)
def _payload_hash(payload: Any) -> tuple[str, str]:
    encoded=_json_dump(payload); return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

_TASK_TRANSITIONS={"planned":{"active","cancelled","failed"},"active":{"waiting","completed","failed","cancelled"},"waiting":{"active","failed","cancelled"},"completed":set(),"failed":set(),"cancelled":set()}
_STEP_TRANSITIONS={"pending":{"running","blocked","skipped","failed"},"running":{"pass","rework","blocked","failed"},"rework":{"running","blocked","failed","skipped"},"blocked":{"pending","running","failed","skipped"},"pass":set(),"failed":set(),"skipped":set()}
RUNTIME_SCHEMA_VERSION=2
