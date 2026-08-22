from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas_core.advanced.brief import TaskBrief
from atlas_core.integrations import register_morning_workflow
from atlas_core.knowledge import KnowledgeStore, register_knowledge_capabilities
from atlas_core.presentation import WorkPresenter
from atlas_core.verification import VerifierRegistry
from atlas_core.work import DeploymentInventory, build_work_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atlas 2.0 Work runtime")
    parser.add_argument("--db", default="atlas-agent.db", help="SQLite Work database")
    sub = parser.add_subparsers(dest="command", required=True)

    morning = sub.add_parser("morning", help="Run the existing Morning Workflow through WorkRuntime")
    morning.add_argument("input")
    morning.add_argument("--config", required=True)
    morning.add_argument("--aliases")
    morning.add_argument("--corrections")
    morning.add_argument("--day")

    run = sub.add_parser("run", help="Run or resume accepted work")
    run.add_argument("work_id")
    recover = sub.add_parser("recover", help="Resolve executions left running by an interrupted process")
    recover.add_argument("work_id")
    approve = sub.add_parser("approve", help="Approve one pending authority gate")
    approve.add_argument("approval_id")
    approve.add_argument("--note")
    deny = sub.add_parser("deny", help="Deny one pending authority gate")
    deny.add_argument("approval_id")
    deny.add_argument("--note")
    result = sub.add_parser("result", help="Render durable work truth as a user-facing report")
    result.add_argument("work_id")
    index_text = sub.add_parser(
        "index-text",
        help="Request controlled UTF-8 ingestion from a configured local source root",
    )
    index_text.add_argument("relative_path")
    index_text.add_argument("--provider-namespace", required=True)
    index_text.add_argument("--root-id", required=True)
    index_text.add_argument("--configuration-revision", required=True)
    index_text.add_argument("--title")
    index_text.add_argument("--chunk-chars", type=int, default=4000)
    index_text.add_argument("--overlap-chars", type=int, default=400)
    search = sub.add_parser("search", help="Search Atlas local full-text knowledge")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    cancel = sub.add_parser("cancel", help="Cancel a non-terminal work item")
    cancel.add_argument("work_id")
    status = sub.add_parser("status", help="Show one work snapshot")
    status.add_argument("work_id")
    status.add_argument("--payloads", action="store_true")
    work = sub.add_parser("work", help="List durable work items")
    work.add_argument("--status")
    return parser


def _print_result(result) -> None:
    print(
        json.dumps(
            {
                "work_id": result.work_id,
                "status": result.status,
                "cycles": result.cycles,
                "executions": result.executions,
                "reason": result.reason,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _work_runtime(
    db_path,
    *,
    morning: bool = True,
    knowledge: bool = True,
    local_source_registry=None,
    local_source_kernel=None,
):
    verifiers = VerifierRegistry()
    inventory = DeploymentInventory()
    runtime = build_work_runtime(
        db_path=db_path,
        profiles=inventory,
        verifiers=verifiers,
        local_source_registry=local_source_registry,
        local_source_kernel=local_source_kernel,
    )
    if morning:
        register_morning_workflow(inventory, verifiers, store=runtime.store)
    if knowledge:
        knowledge_store = KnowledgeStore(db_path)
        knowledge_store.initialize()
        register_knowledge_capabilities(
            inventory,
            verifiers,
            store=runtime.store,
            knowledge_store=knowledge_store,
        )
    return runtime


def main() -> None:
    args = _parser().parse_args()
    if args.command == "index-text":
        runtime = _work_runtime(args.db)
        if runtime._profiles.get("files.read") is None:
            raise SystemExit(
                "index-text requires a deployment-configured local source registry; "
                "no local source roots are configured"
            )
        relative_path = args.relative_path
        title = args.title or relative_path.rsplit("/", 1)[-1]
        work_id = runtime.accept(
            TaskBrief(
                objective=f"Index local knowledge source {relative_path}",
                capabilities=("files.read", "knowledge.ingest_text"),
                required_authority="modify_internal",
                expected_effect="The source is durably indexed with chunk provenance.",
            ),
            "modify_internal",
            inputs={
                "files.read": {
                    "provider_namespace": args.provider_namespace,
                    "root_id": args.root_id,
                    "configuration_revision": args.configuration_revision,
                    "relative_path": relative_path,
                },
                "knowledge.ingest_text": {
                    "title": title,
                    "chunk_chars": args.chunk_chars,
                    "overlap_chars": args.overlap_chars,
                }
            },
        )
        _print_result(runtime.run(work_id))
        return
    if args.command == "search":
        runtime = _work_runtime(args.db)
        work_id = runtime.accept(
            TaskBrief(
                objective=f"Search Atlas knowledge for: {args.query}",
                capabilities=("knowledge.search",),
                required_authority="read",
                expected_effect="A source-grounded local knowledge search result is produced.",
            ),
            "read",
            inputs={"knowledge.search": {"query": args.query, "limit": args.limit}},
        )
        _print_result(runtime.run(work_id))
        print(WorkPresenter(runtime.store).build(work_id).render_markdown())
        return
    if args.command == "morning":
        runtime = _work_runtime(args.db)
        work_id = runtime.accept(
            TaskBrief(
                objective="Generate the TMM morning operational pack.",
                capabilities=("operations.morning_pack.generate",),
                required_authority="read",
                expected_effect=(
                    "The frozen Morning Workflow produces a verified non-empty "
                    "pack for the requested operational day."
                ),
            ),
            "read",
            inputs={
                "operations.morning_pack.generate": {
                    "input": str(Path(args.input)),
                    "config": str(Path(args.config)),
                    "aliases": str(Path(args.aliases)) if args.aliases else None,
                    "corrections": str(Path(args.corrections)) if args.corrections else None,
                    "day": args.day,
                }
            },
        )
        result = runtime.run(work_id)
        _print_result(result)
        packs = [
            artifact
            for artifact in runtime.store.list_artifacts(work_id)
            if artifact.kind == "morning_pack"
        ]
        if packs:
            print(packs[-1].payload["markdown"])
        return

    runtime = _work_runtime(args.db)
    store = runtime.store
    if args.command == "result":
        print(WorkPresenter(store).build(args.work_id).render_markdown())
        return
    if args.command == "cancel":
        item = store.set_work_status(args.work_id, "cancelled")
        store.create_checkpoint(item.id, reason="work cancelled from CLI")
        print(f"{item.id}\t{item.status}")
        return
    if args.command == "work":
        for item in store.list_work(status=args.status):
            print(f"{item.id}\t{item.status}\t{item.objective}")
        return
    if args.command == "status":
        print(
            json.dumps(
                store.snapshot(args.work_id, include_artifact_payloads=args.payloads),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    if args.command == "approve":
        approval = runtime.approve(args.approval_id, note=args.note)
        print(
            json.dumps(
                {
                    "approval_id": approval.id,
                    "work_id": approval.work_id,
                    "step_id": approval.step_id,
                    "status": approval.status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "deny":
        approval = runtime.deny(args.approval_id, note=args.note)
        print(
            json.dumps(
                {
                    "approval_id": approval.id,
                    "work_id": approval.work_id,
                    "step_id": approval.step_id,
                    "status": approval.status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "recover":
        result = runtime.recover(args.work_id)
        print(
            json.dumps(
                {
                    "work_id": result.work_id,
                    "status": result.status,
                    "recovered": result.recovered,
                    "failed_closed": result.failed_closed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "run":
        runtime.resume(args.work_id)
        _print_result(runtime.run(args.work_id))
        return


if __name__ == "__main__":
    main()
