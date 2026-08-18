from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas_core.bootstrap import build_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atlas 2.0 durable task runtime")
    parser.add_argument("--db", default="atlas-agent.db", help="SQLite runtime database")
    sub = parser.add_subparsers(dest="command", required=True)

    morning = sub.add_parser("morning", help="Run the existing Morning Workflow through TaskRuntime")
    morning.add_argument("input")
    morning.add_argument("--config", required=True)
    morning.add_argument("--aliases")
    morning.add_argument("--corrections")
    morning.add_argument("--day")

    status = sub.add_parser("status", help="Show one task snapshot")
    status.add_argument("task_id")

    tasks = sub.add_parser("tasks", help="List durable tasks")
    tasks.add_argument("--status")
    return parser


def main() -> None:
    args = _parser().parse_args()
    runtime = build_runtime(db_path=args.db)
    store = runtime.store
    if args.command == "tasks":
        for task in store.list_tasks(status=args.status):
            print(f"{task.id}\t{task.status}\t{task.objective}")
        return
    if args.command == "status":
        print(json.dumps(store.snapshot(args.task_id, include_artifact_payloads=False), ensure_ascii=False, indent=2, default=str))
        return
    if args.command == "morning":
        task = store.create_task(
            objective="Generate the TMM morning operational pack.",
            success_criteria=("The frozen Morning Workflow produces a verified non-empty pack for the requested operational day.",),
            authority_scope="read",
            metadata={"interface": "cli", "workflow": "morning_v1"},
        )
        input_artifact = store.put_artifact(
            task.id,
            kind="morning_request",
            payload={
                "input": str(Path(args.input)),
                "config": str(Path(args.config)),
                "aliases": str(Path(args.aliases)) if args.aliases else None,
                "corrections": str(Path(args.corrections)) if args.corrections else None,
                "day": args.day,
            },
        )
        store.add_step(
            task.id,
            description="Generate and verify the morning pack.",
            capability="operations.morning_pack.generate",
            input_artifact_ids=(input_artifact.id,),
            metadata={"accept_all_criteria": True},
        )
        result = runtime.run_until_blocked(task.id)
        print(f"Atlas task {task.id}: {result.status}")
        packs = [artifact for artifact in store.list_artifacts(task.id) if artifact.kind == "morning_pack"]
        if packs:
            print(packs[-1].payload["markdown"])


if __name__ == "__main__":
    main()
