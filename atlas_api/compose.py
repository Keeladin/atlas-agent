from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from atlas_core.actions import ActionRuntime, ActionStore
from atlas_core.artifacts import (
    ArtifactRuntime, ArtifactStore, ArtifactIntakeRuntime, ArtifactIntakeStore,
    ManagedIntakeRuntime, MANAGED_ROOT_ID, MANAGED_PROVIDER_NAMESPACE,
    OwnerUploadRuntime, UPLOAD_ROOT_ID, UPLOAD_PROVIDER_NAMESPACE,
)
from atlas_core.cadence import CadenceRuntime, CadenceStore
from atlas_core.cadence.runtime import register_cadence_capabilities
from atlas_core.capabilities import CapabilityRegistry, CapabilityRuntime
from atlas_core.chat import ChatRuntime, ChatStore, CORE_SIGNPOST_IDS
from atlas_core.database import WorkDatabase
from atlas_core.evidence import EvidenceStore
from atlas_core.host import HostRuntime
from atlas_core.identity import IdentityStore
from atlas_core.knowledge import KnowledgeRuntime, KnowledgeStore
from atlas_core.knowledge.generations import GenerationStore
from atlas_core.knowledge.indexing import IndexingRuntime
from atlas_core.knowledge.passages import PassageStore
from atlas_core.library import LibraryRuntime, LibraryStore
from atlas_core.mcp import MCPRuntime, MCPServerStore
from atlas_core.memory import MemoryRuntime, MemoryStore
from atlas_core.model_runtime import ModelInferenceRuntime
from atlas_core.policy import OwnerPolicy, PolicyStore
from atlas_core.providers import ProviderRuntime, ProviderSettingsStore
from atlas_core.representations import RepresentationRuntime
from atlas_core.retrieval import CapabilityRetriever, build_embedding_provider
from atlas_core.secrets import CredentialStore
from atlas_core.sources import SourceRootStore, SourceRuntime
from atlas_core.work import WorkRuntime, WorkStore
from atlas_core.work.runtime import register_work_capabilities
from atlas_core.web import WebProviderSettingsStore, WebRuntime
from atlas_providers.web_browser import PlaywrightBrowserProvider
from atlas_providers.web_configured import ConfiguredWebProvider


@dataclass
class AtlasRuntime:
    instance_root: Path
    identities: IdentityStore
    policy_store: PolicyStore
    policy: OwnerPolicy
    work_database: WorkDatabase
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
    web_provider_settings: WebProviderSettingsStore
    web_providers: ConfiguredWebProvider
    web: WebRuntime
    host: HostRuntime
    knowledge_store: KnowledgeStore
    knowledge: KnowledgeRuntime
    library_store: LibraryStore
    library: LibraryRuntime
    passages: PassageStore
    generations: GenerationStore
    indexing: IndexingRuntime
    artifact_store: ArtifactStore
    artifacts: ArtifactRuntime
    managed_intake: ManagedIntakeRuntime
    artifact_intake_store: ArtifactIntakeStore
    artifact_intake: ArtifactIntakeRuntime
    uploads: OwnerUploadRuntime
    model_inference: ModelInferenceRuntime
    representations: RepresentationRuntime
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
            "version": "3.5.0",
            "owner": owner.as_dict(),
            "policy_revision": self.policy_store.revision(),
            "providers": list(self.providers.public_state()),
            "web_providers": list(self.web_providers.public_state()),
            "mcp_servers": list(self.mcp.public_state()),
            "source_roots": [row for row in self.sources.public_state() if row.get("provider_namespace") != MANAGED_PROVIDER_NAMESPACE],
            "connections": [item.as_dict() for item in self.identities.connections(owner_principal_id=owner.principal_id)],
            "service_bindings": [item.as_dict() for item in self.identities.service_bindings()],
            "capabilities": [item.as_dict() for item in self.capabilities.snapshot(principal_id=owner.principal_id)],
        }

    def seed_policy(self) -> None:
        """Seed coarse principal authority domains only.

        Capability registration and provider discovery never grant authority. Fine-grained
        execution constraints remain in capability contracts and deterministic resolvers.
        """
        owner = self.identities.current_owner().principal_id
        seeds = [
            ("atlas", "*", "YES"),
            ("files", "*", "YES"),
            ("web", "*", "YES"),
            ("host/status", "*", "YES"),
            ("host/resources", "*", "YES"),
            ("host/storage", "*", "YES"),
            ("host/filesystem", "*", "YES"),
            ("host/service", "*", "YES"),
            ("host/package", "*", "YES"),
        ]
        for scope, operation, decision in seeds:
            self.policy_store.seed_if_absent(
                principal_id=owner, scope=scope, operation=operation, decision=decision,
                reason="v3.5 coarse principal authority",
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
    work_database = WorkDatabase(work_db); work_database.initialize()
    actions_store = ActionStore(work_database); actions_store.initialize()
    evidence = EvidenceStore(work_database); evidence.initialize()
    registry = CapabilityRegistry()
    credentials = CredentialStore(root); credentials.initialize()

    provider_settings = ProviderSettingsStore(identity_db); provider_settings.initialize(); provider_settings.seed_local()
    providers = ProviderRuntime(provider_settings, credentials)
    mcp_store = MCPServerStore(identity_db); mcp_store.initialize()
    web_provider_settings = WebProviderSettingsStore(identity_db); web_provider_settings.initialize()
    source_roots = SourceRootStore(identity_db); source_roots.initialize()
    managed_root = root / "managed-intake"
    managed_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_roots.put(
        root_id=MANAGED_ROOT_ID, host_path=str(managed_root), display_name="Atlas managed intake",
        provider_namespace=MANAGED_PROVIDER_NAMESPACE, quarantine_relative_path=None, enabled=True,
    )
    upload_root = root / "owner-uploads"
    upload_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_roots.put(root_id=UPLOAD_ROOT_ID, host_path=str(upload_root), display_name="Owner uploads",
                     provider_namespace=UPLOAD_PROVIDER_NAMESPACE, quarantine_relative_path=None, enabled=True)
    library_root = root / "library-clean"
    library_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_roots.put(
        root_id="atlas-library-clean", host_path=str(library_root), display_name="Atlas clean library",
        provider_namespace="atlas-library", quarantine_relative_path=".atlas-quarantine", enabled=True,
    )

    embedding_provider = build_embedding_provider(cache_dir=root / "models" / "embeddings")

    # Executors are resolved at execution time so the registry remains runtime truth.
    actions = ActionRuntime(policy=policy, store=actions_store, evidence=evidence, executor_resolver=registry.executor)
    capabilities = CapabilityRuntime(registry, actions, policy)

    mcp = MCPRuntime(mcp_store, credentials, registry); mcp.refresh_all()
    artifact_store = ArtifactStore(work_database); artifact_store.initialize()
    sources = SourceRuntime(source_roots, registry, artifact_store)
    uploads = OwnerUploadRuntime(staging_root=root / "upload-staging", upload_root=upload_root, sources=sources, registry=registry)
    web_providers = ConfiguredWebProvider(web_provider_settings, credentials)
    web_browser = PlaywrightBrowserProvider()
    web = WebRuntime(registry, web_providers, managed_root / "web", browser=web_browser)
    artifacts = ArtifactRuntime(artifact_store, registry, sources)
    managed_intake = ManagedIntakeRuntime(artifact_store, sources, registry)
    sensitive_host = [Path("/etc/shadow"), Path("/etc/gshadow"), Path("/root"), root / "secrets", root / "companion-auth.env"]
    parts = root.resolve().parts
    if len(parts) > 2 and parts[1] == "home":
        owner_home = Path("/home") / parts[2]
        sensitive_host.extend((owner_home / ".ssh", owner_home / ".gnupg"))
    host = HostRuntime(
        registry, actions_store, protected_paths=tuple(sensitive_host),
        self_service_unit=os.environ.get("ATLAS_SERVICE_UNIT") or None,
    )
    knowledge_store = KnowledgeStore(work_database); knowledge_store.initialize()
    passages = PassageStore(work_database); passages.initialize()
    generations = GenerationStore(work_database); generations.initialize()
    indexing = IndexingRuntime(passages, generations, artifact_store, sources)
    knowledge = KnowledgeRuntime(knowledge_store, registry, indexing)
    library_store = LibraryStore(work_database); library_store.initialize()
    library = LibraryRuntime(library_store, sources, registry)
    model_inference = ModelInferenceRuntime(providers, registry)
    representations = RepresentationRuntime(artifact_store, sources, registry, model_provider=providers)
    chat_store = ChatStore(chat_db); chat_store.initialize()
    memory_store = MemoryStore(work_database, embedding_provider); memory_store.initialize(); memory = MemoryRuntime(
        memory_store, registry, actions_store, grounding_validator=chat_store.owner_grounding_matches,
    )
    def cancel_work_cleanup(item) -> None:
        if item.metadata.get("workflow_intent") == "knowledge.ingest":
            indexing.abandon_for_work(item.work_id)
    work_store = WorkStore(work_database); work_store.initialize(); work = WorkRuntime(
        work_store, capabilities, actions_store, cancel_hook=cancel_work_cleanup,
    )
    artifact_intake_store = ArtifactIntakeStore(work_database); artifact_intake_store.initialize()
    artifact_intake = ArtifactIntakeRuntime(
        artifact_intake_store, artifact_store, providers, work, registry, capabilities,
        representations=representations, managed_intake=managed_intake, indexing=indexing,
    )
    cadence_store = CadenceStore(cadence_db); cadence_store.initialize(); cadence = CadenceRuntime(cadence_store, work, artifact_intake)
    register_work_capabilities(registry, work)
    register_cadence_capabilities(registry, cadence)
    chat = ChatRuntime(chat_store, providers, registry, capabilities, knowledge, memory_store, identities,
                       source_roots=source_roots, artifacts=artifact_store, work_store=work_store,
                       capability_retriever=CapabilityRetriever(embedding_provider, core_signposts=CORE_SIGNPOST_IDS))
    work.set_completion_hook(chat.record_work_completion)

    runtime = AtlasRuntime(
        root, identities, policy_store, policy, work_database, actions_store, evidence, registry,
        actions, capabilities, credentials, provider_settings, providers,
        mcp_store, mcp, source_roots, sources, web_provider_settings, web_providers, web, host, knowledge_store, knowledge, library_store, library,
        passages, generations, indexing, artifact_store, artifacts, managed_intake, artifact_intake_store, artifact_intake, uploads, model_inference, representations, memory_store, memory,
        work_store, work, cadence_store, cadence, chat_store, chat,
    )
    runtime.seed_policy()

    # Recovery order is load-bearing: preserve abandoned Action evidence, resolve any
    # self-restart against the successor invocation, then reconcile and resume only
    # Work that was actually touched by this recovery pass.
    actions_store.recover_executing()
    host.reconcile_self_restart()
    recovery = work.recover_incomplete()
    work.resume_recovered(recovery.get("touched_work_ids", []))
    work.reconcile_orchestration_actions()
    return runtime
