from __future__ import annotations

from atlas_core.tasks import TaskStore


class WorkStore(TaskStore):
    """Work durable store.

    Method names stay ``get_task`` / ``add_step`` / ``begin_execution`` and
    table names remain ``task_*``. That is isolated persistence naming debt,
    not a second execution topology.
    """
