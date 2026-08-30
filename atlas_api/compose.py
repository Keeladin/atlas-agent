from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import ArtifactRuntime, ArtifactStore
from atlas_core.cadence import CadenceRuntime, CadenceStore
from atlas_core.cadence.runtime import register_cadence_capabilities
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.chat import ChatRuntime, ChatStore
from atlas_core.evidence import EvidenceStore
from atlas_core.host import HostRuntime
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import GenerationStore
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore
from atlas_core.mcp import MCPRuntime, MCPServerStore
from atlas_core.memory import MemoryRuntime, MemoryStore
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.providers import ProviderRuntime, ProviderSettingsStore
from atlas_core.secrets import CredentialStore
from atlas_core.sources import SourceRootStore, SourceRuntime
from atlas_core.work import WorkRuntime, WorkStore
from atlas_core.work.runtime import register_work_capabilities


@dataclass
class AtlasRuntime:
    instance_root: Path
    identities: IdentityStore
    policy_store: PolicyStore
    policy: OwnerPolicy
    actions_store: ActionStore
    evidence: EvidenceStore
    capabilities_registry: CapabilityRegistry
    actions: ActionRuntime
    capabilities: CapabilityRuntime
    credentials: CredentialStore
    provider_settings: ProviderSettingsStore
    providers: ProviderRuntime
    mcp_store: MCPServerStore
    mcp: MCPRuntime
    source_roots: SourceRootStore
    sources: SourceRuntime
    host: HostRuntime
    knowledge_store: KnowledgeStore
    knowledge: KnowledgeRuntime
    passages: PassageStore
    generations: GenerationStore
    indexing: IndexingRuntime
    artifact_store: ArtifactStore
    artifacts: ArtifactRuntime
    memory_store: MemoryStore
    memory: MemoryRuntime
    work_store: WorkStore
    work: WorkRuntime
    cadence_store: CadenceStore
    cadence: CadenceRuntime
    chat_store: ChatStore
    chat: ChatRuntime

    def public_state(self) -> dict[str, Any]:
        owner = self.identities.current_owner()
        return {
            "version": "3.0.0",
            "owner": owner.as_dict(),
            "policy_revision": self.policy_store.revision(),
            "providers": list(self.providers.public_state()),
            "mcp_servers": list(self.mcp.public_state()),
            "source_roots": list(self.sources.public_state()),
            "connections": [item.as_dict() for item in self.identities.connections(owner_principal_id=owner.principal_id)],
            "service_bindings": [item.as_dict() for item in self.identities.service_bindings()],
            "pending_confirmations": [item.public() for item in self.actions_store.pending(principal_id=owner.principal_id)],
            "capabilities": [item.as_dict() for item in self.capabilities.snapshot(principal_id=owner.principal_id)],
        }

    def seed_policy(self) -> None:
        owner = self.identities.current_owner().principal_id
        seeds = [
            ("atlas/knowledge", "search", "YES"),
            ("atlas/knowledge", "retrieve", "YES"),
            ("atlas/knowledge", "promote", "YES"),
            ("atlas/knowledge", "delete", "CONFIRM"),
            ("atlas/knowledge/index", "index", "YES"),
            ("atlas/knowledge/index", "activate", "CONFIRM"),
            ("atlas/artifacts", "search", "YES"),
            ("atlas/artifacts", "inspect", "YES"),
            ("atlas/memory", "search", "YES"),
            ("atlas/memory", "remember", "YES"),
            ("atlas/memory", "update", "YES"),
            ("atlas/memory", "retract", "YES"),
            ("atlas/memory", "restore", "CONFIRM"),
            ("atlas/memory", "purge", "CONFIRM"),
            ("atlas/work", "create", "YES"),
            ("atlas/cadence", "create", "YES"),
            ("host/status", "inspect", "YES"),
            ("host/resources", "inspect", "YES"),
            ("host/storage", "inspect", "YES"),
            ("host/filesystem", "list", "YES"),
            ("host/filesystem", "stat", "YES"),
            ("host/filesystem", "read", "YES"),
            ("host/service", "status", "YES"),
            ("host/service", "logs", "YES"),
            ("host/service", "start", "CONFIRM"),
            ("host/service", "stop", "CONFIRM"),
            ("host/service", "restart", "CONFIRM"),
        ]
        sensitive_host = [Path("/etc/shadow"), Path("/etc/gshadow"), Path("/root"), self.instance_root / "secrets", self.instance_root / "companion-auth.env"]
        parts = self.instance_root.resolve().parts
        if len(parts) > 2 and parts[1] == "home":
            owner_home = Path("/home") / parts[2]
            sensitive_host.extend((owner_home / ".ssh", owner_home / ".gnupg"))
        for path in sensitive_host:
            seeds.append(("host/filesystem" + str(path.resolve()), "*", "NO"))
        for root in self.source_roots.all():
            scope = f"files/{root.provider_namespace}/{root.root_id}"
            seeds.extend((scope, op, decision) for op, decision in [
                ("list", "YES"), ("stat", "YES"), ("hash", "YES"), ("read", "YES"), ("extract_text", "YES"),
                ("copy", "CONFIRM"), ("move", "CONFIRM"), ("rename", "CONFIRM"),
                ("delete", "CONFIRM"), ("restore", "CONFIRM"),
            ])
            for child in (".ssh", ".gnupg", "secrets"):
                seeds.append((f"{scope}/{child}", "*", "NO"))
        for server in self.mcp_store.all():
            seeds.append((f"mcp/{server.server_id}", "invoke", "CONFIRM"))
        for scope, operation, decision in seeds:
            self.policy_store.seed_if_absent(
                principal_id=owner, scope=scope, operation=operation, decision=decision,
                reason="visible initial runtime policy",
            )


def build_runtime(instance_root: str | Path) -> AtlasRuntime:
    root = Path(instance_root)
    root.mkdir(parents=True, exist_ok=True)
    identity_db = root / "atlas-identity.db"
    work_db = root / "atlas-work.db"
    chat_db = root / "atlas-chat.db"
    cadence_db = root / "atlas-cadence.db"

    identities = IdentityStore(identity_db); identities.initialize()
    policy_store = PolicyStore(identity_db); policy_store.initialize(); policy = OwnerPolicy(policy_store)
    actions_store = ActionStore(work_db); actions_store.initialize()
    evidence = EvidenceStore(work_db); evidence.initialize()
    registry = CapabilityRegistry()
    credentials = CredentialStore(root); credentials.initialize()

    provider_settings = ProviderSettingsStore(identity_db); provider_settings.initialize(); provider_settings.seed_local()
    providers = ProviderRuntime(provider_settings, credentials)
    mcp_store = MCPServerStore(identity_db); mcp_store.initialize()
    source_roots = SourceRootStore(identity_db); source_roots.initialize()

    # ActionRuntime resolves the executor at execution time so pending CONFIRM
    # occurrences survive capability refreshes and process restarts.
    actions = ActionRuntime(policy=policy, store=actions_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)

    mcp = MCPRuntime(mcp_store, credentials, registry); mcp.refresh_all()
    artifact_store = ArtifactStore(work_db); artifact_store.initialize()
    artifacts = ArtifactRuntime(artifact_store, registry)
    sources = SourceRuntime(source_roots, registry, artifact_store)
    host = HostRuntime(registry, actions_store)
    knowledge_store = KnowledgeStore(work_db); knowledge_store.initialize()
    passages = PassageStore(work_db); passages.initialize()
    generations = GenerationStore(work_db); generations.initialize()
    indexing = IndexingRuntime(passages, generations, artifact_store, sources)
    knowledge = KnowledgeRuntime(knowledge_store, registry, indexing)
    chat_store = ChatStore(chat_db); chat_store.initialize()
    memory_store = MemoryStore(work_db); memory_store.initialize(); memory = MemoryRuntime(
        memory_store, registry, actions_store, grounding_validator=chat_store.owner_grounding_matches,
    )
    work_store = WorkStore(work_db); work_store.initialize(); work = WorkRuntime(work_store, capabilities, actions_store)
    cadence_store = CadenceStore(cadence_db); cadence_store.initialize(); cadence = CadenceRuntime(cadence_store, work)
    register_work_capabilities(registry, work)
    register_cadence_capabilities(registry, cadence)
    chat = ChatRuntime(chat_store, providers, registry, capabilities, knowledge, memory_store, identities,
                       source_roots=source_roots, artifacts=artifact_store)

    runtime = AtlasRuntime(
        root, identities, policy_store, policy, actions_store, evidence, registry,
        actions, capabilities, credentials, provider_settings, providers,
        mcp_store, mcp, source_roots, sources, host, knowledge_store, knowledge,
        passages, generations, indexing, artifact_store, artifacts, memory_store, memory,
        work_store, work, cadence_store, cadence, chat_store, chat,
    )
    runtime.seed_policy()
    host.reconcile_self_restart()
    return runtime
