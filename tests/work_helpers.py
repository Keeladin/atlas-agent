from __future__ import annotations


def confirm_pending(runtime, work_id):
    pending = tuple(runtime.list_pending_confirmations(work_id))
    for item in pending:
        runtime.confirm_payload(item.id)
    return pending


def run_with_confirmation(runtime, work_id):
    result = runtime.run(work_id)
    if not confirm_pending(runtime, work_id):
        return result
    return runtime.run(work_id)


def engine_run_with_confirmation(runtime, engine, contract, report):
    result = engine.run(contract, report)
    if not confirm_pending(runtime, contract.work_id):
        return result
    return engine.run(contract, report)
