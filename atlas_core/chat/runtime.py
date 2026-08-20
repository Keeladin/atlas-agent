from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas_core.providers import (
    ModelProvider,
    ModelRequest,
    ProviderRegistry,
    load_provider_registry,
)

from .awareness import CapabilityAwareness, explain_manifest
from .conversations import ConversationStore, ConversationView
from .prompts import build_model_input, build_system_prompt


DEFAULT_CHAT_DB = Path("instance/atlas-chat.db")
_REPLY_LABEL = "conversation.reply"
_MAX_OUTPUT_CHARS = 8_192


class ChatError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatReply:
    conversation_id: str
    reply: str
    conversation: ConversationView


class ChatRuntime:
    """Conversation runtime. Does not own Work, tools, or execution."""

    def __init__(
        self,
        *,
        conversations: ConversationStore,
        provider: ModelProvider,
        awareness: tuple[CapabilityAwareness, ...],
    ) -> None:
        self._conversations = conversations
        self._provider = provider
        self._awareness = awareness
        self._system = build_system_prompt(awareness)

    def respond(self, message: str, conversation_id: str | None = None) -> ChatReply:
        text = (message or "").strip()
        if not text:
            raise ValueError("message is required")
        record = self._conversations.get_or_create(conversation_id)
        self._conversations.add_turn(record.id, role="user", content=text)
        turns = self._conversations.list_turns(record.id)
        response = self._provider.generate(
            ModelRequest(
                capability_id=_REPLY_LABEL,
                system=self._system,
                input=build_model_input(turns),
                max_output_chars=_MAX_OUTPUT_CHARS,
            )
        )
        reply = (response.text or "").strip()
        if not reply:
            raise ChatError("Chat provider returned an empty reply.")
        self._conversations.add_turn(record.id, role="atlas", content=reply)
        return ChatReply(
            conversation_id=record.id,
            reply=reply,
            conversation=self._conversations.view(record.id),
        )

    def conversation(self, conversation_id: str) -> ConversationView:
        cid = (conversation_id or "").strip()
        if cid in {"", "current"}:
            cid = self._conversations.get_or_create(None).id
        return self._conversations.view(cid)

    def list_conversations(self) -> tuple[ConversationView, ...]:
        return tuple(
            self._conversations.view(item.id, include_turns=False)
            for item in self._conversations.list()
        )


def build_chat_runtime(
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    provider_config: str | Path | None = None,
    provider: ModelProvider | None = None,
) -> ChatRuntime:
    """Only composition root for ChatRuntime."""

    chosen = provider
    if chosen is None:
        if provider_config is None:
            raise ChatError("ChatRuntime requires a model provider.")
        chosen = _enabled_provider(load_provider_registry(provider_config))
    store = ConversationStore(db_path)
    store.initialize()
    return ChatRuntime(
        conversations=store,
        provider=chosen,
        awareness=explain_manifest(),
    )


def _enabled_provider(registry: ProviderRegistry) -> ModelProvider:
    enabled = [item for item in registry.providers() if item.spec.enabled]
    if not enabled:
        raise ChatError("ChatRuntime requires an enabled model provider.")
    return max(enabled, key=lambda item: (item.spec.priority, -item.spec.latency_rank))
