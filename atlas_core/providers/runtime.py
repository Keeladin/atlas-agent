from __future__ import annotations

import os
from typing import Any
from atlas_core.secrets import CredentialStore
from .contracts import ModelRequest,ProviderSpec
from .http import AnthropicMessagesProvider,GeminiGenerateContentProvider,OpenAICompatibleChatProvider,OpenAIResponsesProvider
from .settings import ProviderSettings,ProviderSettingsStore

class ProviderRuntime:
    def __init__(self,settings:ProviderSettingsStore,secrets:CredentialStore)->None:self.settings=settings;self.secrets=secrets
    def public_state(self)->tuple[dict[str,Any],...]:return tuple(item.public() for item in self.settings.all())
    def active(self):
        enabled=[row for row in self.settings.all() if row.enabled]
        if not enabled:raise RuntimeError("no model provider enabled")
        last:Exception|None=None
        for row in sorted(enabled,key=lambda x:(x.priority,x.local),reverse=True):
            try:return self._build(row)
            except Exception as exc:last=exc
        raise RuntimeError(f"no configured model provider available: {last}")
    def generate(self,request:ModelRequest):
        enabled=[row for row in self.settings.all() if row.enabled]
        if not enabled:raise RuntimeError("no model provider enabled")
        errors=[]
        for row in sorted(enabled,key=lambda x:(x.priority,x.local),reverse=True):
            try:return self._build(row).generate(request)
            except Exception as exc:errors.append(f"{row.key}: {exc}")
        raise RuntimeError("all enabled model providers failed: " + " | ".join(errors))
    def verify(self,key:str)->dict[str,Any]:
        provider=self._build(self.settings.get(key));response=provider.generate(ModelRequest(capability_id="system.provider.verify",system="Reply with exactly OK.",input="OK",max_output_chars=32));return {"ok":bool(response.text.strip()),"provider":response.provider_key,"model":response.model,"text":response.text[:64]}
    def _build(self,row:ProviderSettings):
        env_name=None
        if row.credential_ref:
            secret=self.secrets.retrieve(row.credential_ref);value=str(secret.get("api_key") or "").strip()
            if not value:raise RuntimeError("provider credential has no api_key")
            env_name=f"ATLAS_PROVIDER_{''.join(ch if ch.isalnum() else '_' for ch in row.key).upper()}";os.environ[env_name]=value
        spec=ProviderSpec(key=row.key,model=row.model,provider_kind=row.kind,capabilities={},local=row.local,enabled=row.enabled,priority=row.priority,metadata=row.metadata)
        if row.kind=="openai_compatible":
            if not row.base_url:raise RuntimeError("openai-compatible provider requires base_url")
            return OpenAICompatibleChatProvider(spec,base_url=row.base_url,api_key_env=env_name)
        if row.kind=="openai":return OpenAIResponsesProvider(spec,api_key_env=env_name or "OPENAI_API_KEY",base_url=row.base_url or "https://api.openai.com")
        if row.kind=="anthropic":return AnthropicMessagesProvider(spec,api_key_env=env_name or "ANTHROPIC_API_KEY",base_url=row.base_url or "https://api.anthropic.com")
        if row.kind=="gemini":return GeminiGenerateContentProvider(spec,api_key_env=env_name or "GEMINI_API_KEY",base_url=row.base_url or "https://generativelanguage.googleapis.com")
        raise RuntimeError(f"unsupported provider kind: {row.kind}")
