from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas_core.bootstrap import build_runtime
from atlas_core.knowledge import source_content_sha256
from atlas_core.planner import TaskPlanner
from atlas_core.presentation import TaskPresenter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atlas 2.0 durable task runtime")
    parser.add_argument("--db", default="atlas-agent.db", help="SQLite runtime database")
    parser.add_argument(
        "--providers",
        default=None,
        help="Provider registry JSON (required for planning/model execution)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    morning = sub.add_parser("morning", help="Run the existing Morning Workflow through TaskRuntime")
    morning.add_argument("input")
    morning.add_argument("--config", required=True)
    morning.add_argument("--aliases")
    morning.add_argument("--corrections")
    morning.add_argument("--day")

    plan = sub.add_parser("plan", help="Create a durable task and bounded capability plan")
    plan.add_argument("objective")
    plan.add_argument("--criterion", action="append", required=True, help="Success criterion (repeatable)")
    plan.add_argument("--constraint", action="append", default=[], help="Constraint (repeatable)")
    plan.add_argument("--authority", default="interpret", help="Task authority scope")
    plan.add_argument("--run", action="store_true", help="Immediately run ready steps after planning")

    run = sub.add_parser("run", help="Run or resume a durable task")
    run.add_argument("task_id")
    recover = sub.add_parser("recover", help="Explicitly resolve executions left running by an interrupted process")
    recover.add_argument("task_id")
    approve = sub.add_parser("approve", help="Approve one pending authority gate")
    approve.add_argument("approval_id"); approve.add_argument("--note")
    deny = sub.add_parser("deny", help="Deny one pending authority gate")
    deny.add_argument("approval_id"); deny.add_argument("--note")
    result = sub.add_parser("result", help="Render durable task truth as a user-facing report")
    result.add_argument("task_id")
    index_text = sub.add_parser("index-text", help="Index a UTF-8 text file into Atlas local knowledge")
    index_text.add_argument("path"); index_text.add_argument("--title"); index_text.add_argument("--source-uri")
    index_text.add_argument("--chunk-chars", type=int, default=4000); index_text.add_argument("--overlap-chars", type=int, default=400)
    search = sub.add_parser("search", help="Search Atlas local full-text knowledge")
    search.add_argument("query"); search.add_argument("--limit", type=int, default=8)
    cancel = sub.add_parser("cancel", help="Cancel a non-terminal durable task")
    cancel.add_argument("task_id")
    status = sub.add_parser("status", help="Show one task snapshot")
    status.add_argument("task_id"); status.add_argument("--payloads", action="store_true")
    tasks = sub.add_parser("tasks", help="List durable tasks")
    tasks.add_argument("--status")
    return parser


def _print_result(result) -> None:
    print(json.dumps({"task_id": result.task_id, "status": result.status, "cycles": result.cycles, "executions": result.executions, "reason": result.reason}, ensure_ascii=False, indent=2))


def main() -> None:
    args = _parser().parse_args()
    runtime = build_runtime(db_path=args.db, provider_config=args.providers)
    store = runtime.store
    if args.command == "result":
        print(TaskPresenter(store).build(args.task_id).render_markdown()); return
    if args.command == "cancel":
        task = store.set_task_status(args.task_id, "cancelled"); store.create_checkpoint(task.id, reason="task cancelled from CLI"); print(f"{task.id}\t{task.status}"); return
    if args.command == "index-text":
        source = Path(args.path).expanduser()
        if not source.is_file():
            raise SystemExit(f"index-text source is missing or not a file: {source}")
        text = source.read_text(encoding="utf-8")
        resolved = str(source.resolve())
        task = store.create_task(objective=f"Index local knowledge source {source.name}", success_criteria=("The source is durably indexed with chunk provenance.",), authority_scope="modify_internal", metadata={"interface": "cli", "workflow": "knowledge_ingest"})
        request = store.put_artifact(task.id, kind="knowledge_ingest_request", payload={"title": args.title or source.name, "source_path": resolved, "source_uri": args.source_uri or resolved, "content_sha256": source_content_sha256(text), "byte_size": source.stat().st_size, "chunk_chars": args.chunk_chars, "overlap_chars": args.overlap_chars})
        store.add_step(task.id, description="Chunk and index extracted text.", capability="knowledge.ingest_text", capability_version=runtime.capabilities.get("knowledge.ingest_text").profile.version, input_artifact_ids=(request.id,), metadata={"accept_all_criteria": True})
        _print_result(runtime.run_until_blocked(task.id)); return
    if args.command == "search":
        task = store.create_task(objective=f"Search Atlas knowledge for: {args.query}", success_criteria=("A source-grounded local knowledge search result is produced.",), authority_scope="read", metadata={"interface": "cli", "workflow": "knowledge_search"})
        request = store.put_artifact(task.id, kind="knowledge_search_request", payload={"query": args.query, "limit": args.limit})
        store.add_step(task.id, description="Retrieve matching knowledge chunks.", capability="knowledge.search", capability_version=runtime.capabilities.get("knowledge.search").profile.version, input_artifact_ids=(request.id,), metadata={"accept_all_criteria": True})
        _print_result(runtime.run_until_blocked(task.id)); print(TaskPresenter(store).build(task.id).render_markdown()); return
    if args.command == "tasks":
        for task in store.list_tasks(status=args.status): print(f"{task.id}\t{task.status}\t{task.objective}")
        return
    if args.command == "status":
        print(json.dumps(store.snapshot(args.task_id, include_artifact_payloads=args.payloads), ensure_ascii=False, indent=2, default=str)); return
    if args.command in {"approve", "deny"}:
        decision = "approved" if args.command == "approve" else "denied"
        approval = store.decide_approval(args.approval_id, status=decision, note=args.note)
        print(json.dumps({"approval_id": approval.id, "task_id": approval.task_id, "step_id": approval.step_id, "status": approval.status}, ensure_ascii=False, indent=2)); return
    if args.command == "recover":
        result = runtime.recover_interrupted(args.task_id)
        print(json.dumps({"task_id": result.task_id, "status": result.status, "recovered": result.recovered, "failed_closed": result.failed_closed}, ensure_ascii=False, indent=2)); return
    if args.command == "run":
        runtime.resume_blocked(args.task_id); _print_result(runtime.run_until_blocked(args.task_id)); return
    if args.command == "plan":
        if runtime.model_router is None: raise SystemExit("--providers is required for planning")
        planning = runtime.capabilities.get("planning.general")
        manifest = [item for item in runtime.capabilities.manifest() if item["id"] != planning.id]
        planner = TaskPlanner(store=store, model_router=runtime.model_router, planning_capability=planning, capability_manifest=manifest)
        task, plan = planner.plan_and_create(objective=args.objective, success_criteria=tuple(args.criterion), constraints=tuple(args.constraint), authority_scope=args.authority, metadata={"interface": "cli"})
        print(json.dumps({"task_id": task.id, "status": task.status, "planned_steps": len(plan.steps), "notes": list(plan.notes)}, ensure_ascii=False, indent=2))
        if args.run: _print_result(runtime.run_until_blocked(task.id))
        return
    if args.command == "morning":
        task = store.create_task(objective="Generate the TMM morning operational pack.", success_criteria=("The frozen Morning Workflow produces a verified non-empty pack for the requested operational day.",), authority_scope="read", metadata={"interface": "cli", "workflow": "morning_v1"})
        input_artifact = store.put_artifact(task.id, kind="morning_request", payload={"input": str(Path(args.input)), "config": str(Path(args.config)), "aliases": str(Path(args.aliases)) if args.aliases else None, "corrections": str(Path(args.corrections)) if args.corrections else None, "day": args.day})
        store.add_step(task.id, description="Generate and verify the morning pack.", capability="operations.morning_pack.generate", capability_version=runtime.capabilities.get("operations.morning_pack.generate").profile.version, input_artifact_ids=(input_artifact.id,), metadata={"accept_all_criteria": True})
        result = runtime.run_until_blocked(task.id); _print_result(result)
        packs = [artifact for artifact in store.list_artifacts(task.id) if artifact.kind == "morning_pack"]
        if packs: print(packs[-1].payload["markdown"])
        return


if __name__ == "__main__":
    main()
