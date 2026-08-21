from __future__ import annotations

from atlas_core.tasks import TaskStore


class WorkStore(TaskStore):
    """Work durable store.

    Method names stay ``get_task`` / ``add_step`` / ``begin_execution`` while
    the legacy CLI composition still uses TaskStore on a separate database.
    Table names remain ``task_*`` in this wrap; the Work DB file is already
    separate from Chat and Companion.
    """
