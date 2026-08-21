from __future__ import annotations

from .store_common import InvalidTransitionError, UnknownRecordError, WorkStoreError
from .store_core import WorkStoreCoreMixin
from .store_execution import WorkStoreExecutionMixin
from .store_records import WorkStoreRecordsMixin
from .store_schema import WorkStoreSchemaMixin


class WorkStore(
    WorkStoreSchemaMixin,
    WorkStoreCoreMixin,
    WorkStoreExecutionMixin,
    WorkStoreRecordsMixin,
):
    """Durable SQLite source of truth for Atlas Work execution."""


__all__ = [
    "InvalidTransitionError",
    "UnknownRecordError",
    "WorkStore",
    "WorkStoreError",
]
