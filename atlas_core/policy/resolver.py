from __future__ import annotations

from .models import PolicyResolution, PolicyRule, normalize_operation, normalize_scope
from .store import PolicyStore


def _scope_matches(rule_scope: str, concrete_scope: str) -> bool:
    return concrete_scope == rule_scope or concrete_scope.startswith(rule_scope + "/")


def _specificity(rule: PolicyRule, operation: str) -> tuple[int, int, int]:
    return (len(rule.scope.split("/")), 1 if rule.operation == operation else 0, rule.sequence)


class OwnerPolicy:
    """Sole discretionary authority resolver: NO / YES / CONFIRM."""

    def __init__(self, store: PolicyStore) -> None:
        self.store = store

    def resolve(self, *, principal_id: str, scope: str, operation: str) -> PolicyResolution:
        concrete_scope = normalize_scope(scope)
        concrete_operation = normalize_operation(operation)
        revision = self.store.revision()
        candidates = [
            rule for rule in self.store.latest_rules(principal_id)
            if _scope_matches(rule.scope, concrete_scope)
            and rule.operation in {concrete_operation, "*"}
        ]
        if not candidates:
            return PolicyResolution(
                principal_id=principal_id, scope=concrete_scope, operation=concrete_operation,
                decision="NO", revision=revision, defaulted=True,
            )
        chosen = max(candidates, key=lambda item: _specificity(item, concrete_operation))
        return PolicyResolution(
            principal_id=principal_id, scope=concrete_scope, operation=concrete_operation,
            decision=chosen.decision, revision=revision, matched_scope=chosen.scope,
            matched_operation=chosen.operation, event_id=chosen.event_id, defaulted=False,
        )
