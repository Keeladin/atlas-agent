from __future__ import annotations
from tests.capability_fixtures import make_registration, register_cap

import tempfile
import unittest
from pathlib import Path

from atlas_core.capabilities import (
    CapabilityOutcome,
    CapabilityRegistry,
    
    ExecutionBudget,
)
from atlas_core.runtime import TaskRuntime
from atlas_core.tasks import TaskStore
from atlas_core.verification import VerificationResult, VerifierRegistry


class NonIdempotentRetryTests(unittest.TestCase):
    def test_non_idempotent_side_effect_is_not_blindly_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "atlas.db")
            store.initialize()
            calls = {"n": 0}

            def handler(request):
                calls["n"] += 1
                return CapabilityOutcome(
                    "pass",
                    output={"sent": True},
                    receipt={"ok": True, "message_id": "m1"},
                )

            verifiers = VerifierRegistry()
            verifiers.register(
                "mail.verify",
                lambda spec, output, context: VerificationResult(
                    "rework",
                    "delivery state ambiguous",
                ),
            )
            capabilities = CapabilityRegistry()
            capabilities.register(
                make_registration(
                    id="mail.send",
                    description="send once",
                    executor_kind="tool",
                    required_authority="communicate",
                    side_effects=("external_email",),
                    idempotent=False,
                    verifier_id="mail.verify",
                    budget=ExecutionBudget(max_attempts=3),
                ),
                handler,
            )
            task = store.create_task(
                objective="Send one message",
                success_criteria=("Message delivery is verified",),
                authority_scope="communicate",
            )
            store.add_step(
                task.id,
                description="Send",
                capability="mail.send",
                metadata={"accept_all_criteria": True},
            )

            result = TaskRuntime(
                store=store,
                capabilities=capabilities,
                verifiers=verifiers,
            ).run_until_blocked(task.id)

            self.assertEqual(result.status, "failed")
            self.assertEqual(calls["n"], 1)
            self.assertEqual(len(store.list_executions(task.id)), 1)
            self.assertTrue(
                any(event.name == "retry.blocked" for event in store.list_events(task.id))
            )


if __name__ == "__main__":
    unittest.main()
