from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from atlas_companion.cloud_providers import (
    CloudProviderError,
    MANAGEABLE_CLOUD,
    ProviderStateStore,
    overlay_providers,
    public_cloud_status,
    rebuild_router,
    xai_list_models,
)
from atlas_companion.conversations import ConversationStore
from atlas_companion.local_models import LocalModelError, LocalModelManager
from atlas_companion.credentials import CredentialStore
from atlas_companion.intent import preview_intent
from atlas_companion.telemetry import TelemetryCollector
from atlas_core.bootstrap import build_runtime
from atlas_core.context import ASSEMBLER_VERSION
from atlas_core.knowledge import (
    KnowledgeStore,
    is_knowledge_question,
    parse_ingest_objective,
    parse_search_objective,
    resolve_knowledge_source,
    source_content_sha256,
)
from atlas_core.planner import TaskPlanner
from atlas_core.presentation import TaskPresenter, task_failure_reason
from atlas_core.tasks import InvalidTransitionError, TaskStoreError, UnknownRecordError


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class CompanionService:
    """HTTP adapter; task state and execution stay in TaskRuntime."""

    def __init__(self, *, db_path: str | Path, provider_config: str | Path | None = None, companion_bind: str | None = None):
        self.db_path = Path(db_path)
        self.provider_config = Path(provider_config).expanduser() if provider_config else None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.pid = os.getpid()
        self._router_lock = threading.Lock()
        self.credentials = CredentialStore(self.db_path.parent)
        self.provider_state = ProviderStateStore(self.db_path.parent)
        self.runtime = build_runtime(db_path=db_path, provider_config=provider_config)
        if self.provider_config is not None:
            self._reconcile_active_provider()
            self._reload_router()
        self.local_models = LocalModelManager(
            state=self.provider_state,
            reload_router=self._reload_router if self.provider_config is not None else None,
        )
        self.knowledge = KnowledgeStore(db_path)
        self.knowledge.initialize()
        self.conversations = ConversationStore(db_path)
        self.conversations.initialize()
        self.telemetry = TelemetryCollector(
            db_path=db_path,
            repo_path=Path.cwd(),
            provider_url=self._provider_url(provider_config),
            companion_bind=companion_bind,
        )

    def tasks(self):
        rows = []
        for task in reversed(self.runtime.store.list_tasks()):
            row = asdict(task)
            row["workflow"] = (task.metadata or {}).get("workflow")
            if task.status == "failed":
                row["failure_reason"] = task_failure_reason(self.runtime.store, task.id)
            rows.append(row)
        return rows

    def health(self):
        tasks = self.runtime.store.list_tasks()
        active = sum(task.status in {"planned", "active", "waiting"} for task in tasks)
        running = sum(
            execution.status == "running"
            for task in tasks
            for execution in self.runtime.store.list_executions(task.id)
        )
        payload = self.telemetry.collect(
            active_tasks=active,
            running_executions=running,
            provider_configured=self.runtime.model_router is not None,
        )
        payload["atlas"]["knowledge_documents"] = len(self.knowledge.list_documents())
        payload["atlas"]["pending_approvals"] = len(self.approvals())
        payload["runtime"] = self._runtime_identity()
        return payload

    def _runtime_identity(self) -> dict[str, Any]:
        """Process and provider identity for the live companion. No secrets."""
        enabled: list[dict[str, Any]] = []
        disabled_keys: list[str] = []
        router = self.runtime.model_router
        if router is not None:
            for provider in router.registry.providers():
                spec = provider.spec
                if not spec.enabled:
                    disabled_keys.append(spec.key)
                    continue
                scores: dict[str, float] = {}
                for capability_id in sorted(spec.capabilities):
                    value = router.competence(provider, capability_id)
                    if value is not None:
                        scores[capability_id] = value
                enabled.append(
                    {
                        "key": spec.key,
                        "model": spec.model,
                        "kind": spec.provider_kind,
                        "local": spec.local,
                        "scores": scores,
                    }
                )
        config_path = None
        if self.provider_config is not None:
            try:
                config_path = str(self.provider_config.resolve())
            except OSError:
                config_path = str(self.provider_config)
        return {
            "assembler_version": ASSEMBLER_VERSION,
            "pid": self.pid,
            "started_at": self.started_at,
            "provider_config": config_path,
            "providers": enabled,
            "disabled_provider_keys": disabled_keys,
        }

    @staticmethod
    def _provider_url(provider_config):
        if not provider_config:
            return None
        try:
            providers = json.loads(Path(provider_config).read_text()).get("providers", {})
            return next(
                (
                    value.get("base_url")
                    for value in providers.values()
                    if value.get("enabled") and value.get("local")
                ),
                None,
            )
        except (OSError, json.JSONDecodeError):
            return None

    def detail(self, task_id):
        presentation = TaskPresenter(self.runtime.store).build(task_id)
        return {
            "snapshot": self.runtime.store.snapshot(task_id, include_artifact_payloads=True),
            "presentation": presentation.as_dict(),
            "markdown": presentation.render_markdown(),
        }

    def preview_task(self, body):
        intent = preview_intent(
            str(body.get("objective") or body.get("message") or ""),
            criteria=body.get("criteria"),
            authority=body.get("authority"),
        )
        return intent.as_dict()

    def create_and_run(self, body):
        objective = str(body.get("objective") or body.get("message") or "")
        intent = preview_intent(
            objective,
            criteria=body.get("criteria"),
            authority=body.get("authority"),
        )
        origin = str(body.get("origin") or "ask")
        conversation_id = str(body.get("conversation_id") or "").strip() or None
        query = parse_search_objective(intent.objective)
        if query:
            return self.search_task(
                {"query": query, "limit": 8, "origin": origin, "conversation_id": conversation_id}
            )
        ingest_label = parse_ingest_objective(intent.objective)
        if ingest_label:
            source = resolve_knowledge_source(ingest_label)
            if source is None:
                raise ValueError(f"Could not resolve knowledge source: {ingest_label}")
            return self.ingest(
                {
                    "source_path": str(source),
                    "title": source.name,
                    "origin": origin,
                    "conversation_id": conversation_id,
                }
            )
        if self.runtime.model_router is None:
            raise ValueError("Task creation requires a configured server-side provider registry.")
        planning = self.runtime.capabilities.get("planning.general")
        manifest = [item for item in self.runtime.capabilities.manifest() if item["id"] != planning.id]
        task, _ = TaskPlanner(
            store=self.runtime.store,
            model_router=self.runtime.model_router,
            planning_capability=planning,
            capability_manifest=manifest,
        ).plan_and_create(
            objective=intent.objective,
            success_criteria=intent.criteria,
            constraints=tuple(body.get("constraints", [])),
            authority_scope=intent.authority,
            metadata=self._task_metadata(
                body,
                origin=origin,
                inferred_criteria=intent.inferred_criteria,
                inferred_authority=intent.inferred_authority,
            ),
        )
        self.runtime.run_until_blocked(task.id)
        return self.detail(task.id)

    def ask(self, body):
        message = str(body.get("message") or body.get("objective") or "").strip()
        if not message:
            raise ValueError("message is required")
        conversation = self.conversations.get_or_create(body.get("conversation_id"))
        self.conversations.add_turn(conversation.id, role="user", content=message)
        try:
            detail = self.create_and_run(
                {**body, "objective": message, "origin": "ask", "conversation_id": conversation.id}
            )
        except Exception as exc:
            self.conversations.add_turn(
                conversation.id,
                role="atlas",
                content=str(exc),
                metadata={"error": True},
            )
            raise
        reply = _ask_reply(detail)
        task_id = (detail.get("presentation") or {}).get("task_id")
        self.conversations.add_turn(conversation.id, role="atlas", content=reply, task_id=task_id)
        return {
            "message": message,
            "reply": reply,
            "intent": self.preview_task(
                {"objective": message, "criteria": body.get("criteria"), "authority": body.get("authority")}
            ),
            "work": detail,
            "conversation_id": conversation.id,
            "conversation": self.conversation(conversation.id),
        }

    def list_conversations(self):
        return [self._conversation_public(item) for item in self.conversations.list()]

    def conversation(self, conversation_id: str):
        cid = str(conversation_id or "").strip()
        if cid in {"", "current"}:
            record = self.conversations.get_or_create(None)
        else:
            record = self.conversations.get(cid)
        return self._conversation_public(record, self.conversations.list_turns(record.id))

    def _conversation_public(self, record, turns=None):
        payload = record.as_dict()
        if turns is None:
            payload["turn_count"] = self.conversations.turn_count(record.id)
            return payload
        payload["turns"] = [self._turn_public(turn) for turn in turns]
        payload["turn_count"] = len(payload["turns"])
        return payload

    def _turn_public(self, turn):
        row = turn.as_dict()
        if turn.task_id:
            try:
                row["task_status"] = self.runtime.store.get_task(turn.task_id).status
            except UnknownRecordError:
                row["task_status"] = "deleted"
        else:
            row["task_status"] = None
        return row

    def _task_metadata(self, body: dict[str, Any], **extra: Any) -> dict[str, Any]:
        metadata = {"interface": "companion_pwa", **extra}
        origin = str(body.get("origin") or extra.get("origin") or "").strip()
        if origin:
            metadata["origin"] = origin
        conversation_id = str(body.get("conversation_id") or extra.get("conversation_id") or "").strip()
        if conversation_id:
            metadata["conversation_id"] = conversation_id
        return metadata

    def run(self, task_id):
        task = self.runtime.store.get_task(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            raise InvalidTransitionError(f"Task is already {task.status} and cannot be resumed.")
        self.runtime.resume_blocked(task_id)
        self.runtime.run_until_blocked(task_id)
        return self.detail(task_id)

    def decide(self, approval_id, decision, note=None):
        return self.detail(self.runtime.store.decide_approval(approval_id, status=decision, note=note).task_id)

    def cancel(self, task_id):
        task = self.runtime.store.get_task(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            raise InvalidTransitionError(f"Task is already {task.status} and cannot be cancelled.")
        self.runtime.recover_interrupted(task_id)
        task = self.runtime.store.set_task_status(task_id, "cancelled", force=True)
        self.runtime.store.create_checkpoint(task.id, reason="task cancelled from Companion PWA")
        return self.detail(task.id)

    def delete_task(self, task_id, *, confirm_id: str | None = None):
        task = self.runtime.store.get_task(task_id)
        if (confirm_id or "").strip() != task_id:
            raise ValueError("Deletion requires confirm_id to match the task id.")
        if task.status not in {"completed", "failed", "cancelled"}:
            self.runtime.recover_interrupted(task_id)
            current = self.runtime.store.get_task(task_id)
            if current.status not in {"completed", "failed", "cancelled"}:
                self.runtime.store.set_task_status(task_id, "cancelled", force=True)
        self.runtime.store.delete_task(task_id)
        return {"deleted": task_id}

    def _reload_router(self) -> None:
        if self.provider_config is None:
            return
        with self._router_lock:
            self.runtime.model_router = rebuild_router(
                self.provider_config,
                db_path=self.db_path,
                credentials=self.credentials,
                state=self.provider_state,
            )

    def _require_overlay(self) -> Path:
        if self.provider_config is None:
            raise ValueError("No provider overlay is configured.")
        return self.provider_config

    def _require_cloud_key(self, provider_key: str) -> str:
        if provider_key not in MANAGEABLE_CLOUD:
            raise ValueError(f"Cloud provider {provider_key!r} is not manageable yet.")
        return provider_key

    def _secret(self, provider_key: str) -> str:
        value = self.credentials.get(provider_key)
        if value:
            return value
        overlay = json.loads(self._require_overlay().read_text(encoding="utf-8"))
        env_name = ((overlay.get("providers") or {}).get(provider_key) or {}).get("api_key_env")
        env_value = os.environ.get(str(env_name)) if env_name else None
        if env_value:
            return env_value
        raise ValueError("No credential is configured for this provider.")

    def cloud_providers(self):
        path = self._require_overlay()
        return public_cloud_status(
            path,
            credentials=self.credentials,
            state=self.provider_state,
        )

    def _cloud_row(self, provider_key: str) -> dict[str, Any]:
        for row in self.cloud_providers():
            if row["key"] == provider_key:
                return row
        raise ValueError(f"Unknown cloud provider: {provider_key}")

    def save_cloud_credential(self, provider_key: str, api_key: str):
        key = self._require_cloud_key(provider_key)
        self.credentials.put(key, api_key)
        return self.verify_cloud_provider(key)

    def delete_cloud_credential(self, provider_key: str):
        key = self._require_cloud_key(provider_key)
        self.credentials.delete(key)
        self.provider_state.update(key, verified=False, verified_at=None, last_error=None)
        self._reload_router()
        return self._cloud_row(key)

    def verify_cloud_provider(self, provider_key: str):
        key = self._require_cloud_key(provider_key)
        secret = self._secret(key)
        try:
            models = xai_list_models(secret)
        except CloudProviderError:
            self.provider_state.update(
                key,
                verified=False,
                last_error="credential verification failed",
            )
            raise ValueError("Credential verification failed.")
        self.provider_state.update(
            key,
            verified=True,
            verified_at=datetime.now(timezone.utc).isoformat(),
            discovered_models=models,
            last_error=None,
        )
        self.credentials.apply_env(key, MANAGEABLE_CLOUD[key]["default_env"])
        return self._cloud_row(key)

    def refresh_cloud_models(self, provider_key: str):
        return self.verify_cloud_provider(provider_key)

    def select_cloud_model(self, provider_key: str, model: str):
        key = self._require_cloud_key(provider_key)
        selected = str(model or "").strip()
        if not selected:
            raise ValueError("model is required")
        row = self._cloud_row(key)
        models = list(row.get("discovered_models") or [])
        if selected not in models:
            row = self.refresh_cloud_models(key)
            models = list(row.get("discovered_models") or [])
        if selected not in models:
            raise ValueError("Selected model is not available for this credential.")
        self.provider_state.update(key, model=selected)
        self._reload_router()
        return self._cloud_row(key)

    def enable_cloud_provider(self, provider_key: str, enabled: bool):
        key = self._require_cloud_key(provider_key)
        if enabled:
            self._secret(key)
            self._set_exclusive_provider(key)
        else:
            self.provider_state.update(key, enabled=False)
            self._enable_fallback_local()
        self._reload_router()
        return self._cloud_row(key)

    def list_local_models(self):
        return self.local_models.catalog()

    def load_local_model(self, slot_id: str):
        result = self.local_models.load(slot_id)
        self._set_exclusive_provider(slot_id)
        self._reload_router()
        return result

    def unload_local_model(self, slot_id: str):
        return self.local_models.unload(slot_id)

    def activate_local_model(self, slot_id: str):
        result = self.local_models.activate(slot_id)
        self._set_exclusive_provider(slot_id)
        self._reload_router()
        return result

    def _set_exclusive_provider(self, provider_key: str) -> None:
        overlay = overlay_providers(self._require_overlay())
        if provider_key not in overlay:
            raise ValueError(f"Unknown provider: {provider_key}")
        for key in overlay:
            self.provider_state.update(key, enabled=(key == provider_key))

    def _enable_fallback_local(self) -> None:
        overlay = overlay_providers(self._require_overlay())
        stored = self.provider_state.load()["providers"]
        if any(
            bool((stored.get(key) or {}).get("enabled", item.get("enabled")))
            for key, item in overlay.items()
        ):
            return
        target = "local:resident" if "local:resident" in overlay else next(
            (key for key, item in overlay.items() if item.get("local")),
            None,
        )
        if target:
            self.provider_state.update(target, enabled=True)

    def _reconcile_active_provider(self) -> None:
        if self.provider_config is None:
            return
        overlay = overlay_providers(self.provider_config)
        stored = self.provider_state.load()["providers"]
        enabled_cloud = [
            key
            for key, item in overlay.items()
            if not item.get("local")
            and (
                bool(stored[key]["enabled"])
                if isinstance(stored.get(key), dict) and "enabled" in stored[key]
                else bool(item.get("enabled"))
            )
        ]
        if enabled_cloud:
            self._set_exclusive_provider(enabled_cloud[0])

    def approvals(self):
        rows = []
        for task in self.runtime.store.list_tasks():
            for approval in self.runtime.store.list_approvals(task.id, status="pending"):
                rows.append(
                    {
                        "id": approval.id,
                        "task_id": task.id,
                        "objective": task.objective,
                        "task_status": task.status,
                        "required_authority": approval.required_authority,
                        "requested_action": approval.requested_action,
                    }
                )
        return rows

    def documents(self):
        return [asdict(document) for document in self.knowledge.list_documents()]

    def search_knowledge(self, query: str, limit: int = 8):
        hits = self.knowledge.search(query, limit=limit)
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": hit.chunk.id,
                    "document_id": hit.chunk.document_id,
                    "title": hit.chunk.title,
                    "source_uri": hit.chunk.source_uri,
                    "ordinal": hit.chunk.ordinal,
                    "text": hit.chunk.text,
                    "sha256": hit.chunk.sha256,
                    "score": hit.score,
                }
                for hit in hits
            ],
        }

    def stat_source(self, source_path: str):
        path = Path(str(source_path or "").strip()).expanduser()
        try:
            path = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"Knowledge source path is missing: {path}") from exc
        if not path.is_file():
            raise ValueError(f"Knowledge source path is not a file: {path}")
        text = path.read_text(encoding="utf-8")
        return {
            "path": str(path),
            "title": path.name,
            "byte_size": path.stat().st_size,
            "content_sha256": source_content_sha256(text),
        }

    def ingest(self, body: dict[str, Any]):
        meta = self.stat_source(str(body.get("source_path") or ""))
        title = str(body.get("title") or meta["title"]).strip() or meta["title"]
        store = self.runtime.store
        task = store.create_task(
            objective=f"Index local knowledge source {title}",
            success_criteria=("The source is durably indexed with chunk provenance.",),
            authority_scope="modify_internal",
            metadata=self._task_metadata(body, workflow="knowledge_ingest"),
        )
        request = store.put_artifact(
            task.id,
            kind="knowledge_ingest_request",
            payload={
                "title": title,
                "source_path": meta["path"],
                "source_uri": meta["path"],
                "content_sha256": meta["content_sha256"],
                "byte_size": meta["byte_size"],
                "chunk_chars": int(body.get("chunk_chars") or 4000),
                "overlap_chars": int(body.get("overlap_chars") or 400),
            },
        )
        store.add_step(
            task.id,
            description="Chunk and index extracted text.",
            capability="knowledge.ingest_text",
            capability_version=self.runtime.capabilities.get("knowledge.ingest_text").profile.version,
            input_artifact_ids=(request.id,),
            metadata={"accept_all_criteria": True},
        )
        self.runtime.run_until_blocked(task.id)
        return self.detail(task.id)

    def search_task(self, body: dict[str, Any]):
        query = str(body.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        question = is_knowledge_question(query)
        criteria = ["A source-grounded local knowledge search result is produced."]
        if question:
            criteria.append("An evidence-grounded answer cites retrieved sources.")
        store = self.runtime.store
        task = store.create_task(
            objective=f"Search Atlas knowledge for: {query}",
            success_criteria=tuple(criteria),
            authority_scope="interpret" if question else "read",
            metadata=self._task_metadata(body, workflow="knowledge_search"),
        )
        request = store.put_artifact(
            task.id,
            kind="knowledge_search_request",
            payload={"query": query, "limit": int(body.get("limit") or 8)},
        )
        search = store.add_step(
            task.id,
            description="Retrieve matching knowledge chunks.",
            capability="knowledge.search",
            capability_version=self.runtime.capabilities.get("knowledge.search").profile.version,
            input_artifact_ids=(request.id,),
            metadata={"satisfies_criteria": [1]} if question else {"accept_all_criteria": True},
        )
        if question:
            store.add_step(
                task.id,
                description="Compose a source-grounded answer from retrieved chunks.",
                capability="knowledge.answer",
                capability_version=self.runtime.capabilities.get("knowledge.answer").profile.version,
                dependencies=(search.id,),
                metadata={"satisfies_criteria": [2]},
            )
        self.runtime.run_until_blocked(task.id)
        return self.detail(task.id)


def _ask_reply(detail: dict[str, Any]) -> str:
    presentation = detail.get("presentation") or {}
    status = presentation.get("status") or "unknown"
    if presentation.get("pending_approvals"):
        action = presentation["pending_approvals"][0].get("requested_action") or "approval"
        return f"Atlas paused for approval: {action}"
    if status == "failed":
        return presentation.get("failure_reason") or "The work failed."
    if status == "waiting":
        return "Atlas is waiting. Open Work for the current step or any approval."
    outputs = presentation.get("outputs") or []
    for kind in ("grounded_answer", "capability_result"):
        hit = next((item for item in outputs if item.get("kind") == kind and item.get("preview")), None)
        if hit:
            return str(hit["preview"])
    markdown = str(detail.get("markdown") or "").strip()
    if markdown:
        return markdown
    return f"Work is {status}."


class CompanionApp:
    def __init__(self, service, static_dir):
        self.service, self.static_dir = service, static_dir

    def __call__(self, environ, start_response):
        try:
            method, path = environ["REQUEST_METHOD"], urlparse(environ["PATH_INFO"]).path
            query = {key: values[-1] for key, values in parse_qs(environ.get("QUERY_STRING") or "").items()}
            if method == "GET" and path == "/api/tasks":
                return self._json(start_response, HTTPStatus.OK, self.service.tasks())
            if method == "GET" and path == "/api/health":
                return self._json(start_response, HTTPStatus.OK, self.service.health())
            if method == "GET" and path == "/api/models/cloud":
                return self._json(start_response, HTTPStatus.OK, self.service.cloud_providers())
            if method == "GET" and path == "/api/models/local":
                return self._json(start_response, HTTPStatus.OK, self.service.list_local_models())
            if method == "POST" and path == "/api/models/local/load":
                return self._json(start_response, HTTPStatus.OK, self.service.load_local_model(str(self._body(environ).get("id") or "")))
            if method == "POST" and path == "/api/models/local/unload":
                return self._json(start_response, HTTPStatus.OK, self.service.unload_local_model(str(self._body(environ).get("id") or "")))
            if method == "POST" and path == "/api/models/local/activate":
                return self._json(start_response, HTTPStatus.OK, self.service.activate_local_model(str(self._body(environ).get("id") or "")))
            if method in {"POST", "DELETE"} and path.startswith("/api/models/cloud/"):
                return self._json(start_response, HTTPStatus.OK, self._cloud_action(method, path, environ))
            if method == "GET" and path == "/api/approvals":
                return self._json(start_response, HTTPStatus.OK, self.service.approvals())
            if method == "GET" and path == "/api/conversations":
                return self._json(start_response, HTTPStatus.OK, self.service.list_conversations())
            if method == "GET" and path.startswith("/api/conversations/"):
                return self._json(start_response, HTTPStatus.OK, self.service.conversation(path.rsplit("/", 1)[-1]))
            if method == "GET" and path == "/api/knowledge/documents":
                return self._json(start_response, HTTPStatus.OK, self.service.documents())
            if method == "GET" and path == "/api/knowledge/search":
                return self._json(
                    start_response,
                    HTTPStatus.OK,
                    self.service.search_knowledge(query.get("q", ""), int(query.get("limit") or 8)),
                )
            if method == "GET" and path == "/api/knowledge/stat":
                return self._json(start_response, HTTPStatus.OK, self.service.stat_source(query.get("path", "")))
            if method == "POST" and path == "/api/tasks/preview":
                return self._json(start_response, HTTPStatus.OK, self.service.preview_task(self._body(environ)))
            if method == "POST" and path == "/api/ask":
                return self._json(start_response, HTTPStatus.CREATED, self.service.ask(self._body(environ)))
            if method == "GET" and path.startswith("/api/tasks/"):
                return self._json(start_response, HTTPStatus.OK, self.service.detail(path.rsplit("/", 1)[-1]))
            if method == "DELETE" and path.startswith("/api/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                body = self._body(environ)
                confirm = body.get("confirm_id") or query.get("confirm")
                return self._json(start_response, HTTPStatus.OK, self.service.delete_task(task_id, confirm_id=confirm))
            if method == "POST" and path == "/api/tasks":
                return self._json(start_response, HTTPStatus.CREATED, self.service.create_and_run(self._body(environ)))
            if method == "POST" and path == "/api/knowledge/ingest":
                return self._json(start_response, HTTPStatus.CREATED, self.service.ingest(self._body(environ)))
            if method == "POST" and path == "/api/knowledge/search":
                return self._json(start_response, HTTPStatus.CREATED, self.service.search_task(self._body(environ)))
            if method == "POST" and path.endswith("/run") and path.startswith("/api/tasks/"):
                return self._json(start_response, HTTPStatus.OK, self.service.run(path.split("/")[3]))
            if method == "POST" and path.endswith("/cancel") and path.startswith("/api/tasks/"):
                return self._json(start_response, HTTPStatus.OK, self.service.cancel(path.split("/")[3]))
            if method == "POST" and path.startswith("/api/approvals/"):
                bits = path.split("/")
                if len(bits) == 5 and bits[4] in {"approve", "deny"}:
                    return self._json(
                        start_response,
                        HTTPStatus.OK,
                        self.service.decide(bits[3], "approved" if bits[4] == "approve" else "denied", self._body(environ).get("note")),
                    )
            if method == "GET":
                return self._static(start_response, path)
            return self._json(start_response, HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (ValueError, TaskStoreError, UnknownRecordError, InvalidTransitionError, CloudProviderError, LocalModelError) as exc:
            return self._json(start_response, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _cloud_action(self, method: str, path: str, environ) -> dict[str, Any]:
        parts = path.strip("/").split("/")
        # api/models/cloud/<provider>/...
        if len(parts) < 5:
            raise ValueError("Unknown cloud models route.")
        provider_key = parts[3]
        action = parts[4]
        body = self._body(environ)
        if method == "POST" and action == "credentials":
            return self.service.save_cloud_credential(provider_key, str(body.get("api_key") or ""))
        if method == "DELETE" and action == "credentials":
            return self.service.delete_cloud_credential(provider_key)
        if method == "POST" and action == "verify":
            return self.service.verify_cloud_provider(provider_key)
        if method == "POST" and action == "models" and len(parts) > 5 and parts[5] == "refresh":
            return self.service.refresh_cloud_models(provider_key)
        if method == "POST" and action == "select":
            return self.service.select_cloud_model(provider_key, str(body.get("model") or ""))
        if method == "POST" and action == "enable":
            return self.service.enable_cloud_provider(provider_key, bool(body.get("enabled")))
        raise ValueError("Unknown cloud models route.")

    @staticmethod
    def _body(environ):
        return json.loads(environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0)) or b"{}")

    @staticmethod
    def _json(start_response, status, data):
        payload = json.dumps(data, ensure_ascii=False, default=str).encode()
        start_response(
            f"{status.value} {status.phrase}",
            [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(payload)))],
        )
        return [payload]

    def _static(self, start_response, path):
        name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        target = (self.static_dir / name).resolve()
        if self.static_dir.resolve() not in target.parents or not target.is_file():
            return self._json(start_response, HTTPStatus.NOT_FOUND, {"error": "not found"})
        mime = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".webmanifest": "application/manifest+json",
        }.get(target.suffix, "application/octet-stream")
        payload = target.read_bytes()
        start_response("200 OK", [("Content-Type", f"{mime}; charset=utf-8"), ("Content-Length", str(len(payload)))])
        return [payload]


def main():
    parser = argparse.ArgumentParser(description="Atlas Companion PWA (LAN-local TaskRuntime interface)")
    parser.add_argument("--db", default="instance/atlas.db")
    parser.add_argument("--providers")
    parser.add_argument("--host", default="127.0.0.1", help="Use a LAN address only on a trusted network.")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    app = CompanionApp(
        CompanionService(db_path=args.db, provider_config=args.providers, companion_bind=f"{args.host}:{args.port}"),
        Path(__file__).resolve().parent / "web",
    )
    with make_server(args.host, args.port, app, server_class=ThreadingWSGIServer, handler_class=WSGIRequestHandler) as server:
        print(f"Atlas Companion PWA listening on http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
