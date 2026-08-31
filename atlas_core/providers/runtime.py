from __future__ import annotations

import logging
from typing import Any
from atlas_core.secrets import CredentialStore
from .contracts import ModelRequest,ProviderSpec
from .http import AnthropicMessagesProvider,GeminiGenerateContentProvider,OpenAICompatibleChatProvider,OpenAIResponsesProvider
from .settings import ProviderSettings,ProviderSettingsStore

logger=logging.getLogger(__name__)

class ProviderRuntime:
    def __init__(self,settings:ProviderSettingsStore,secrets:CredentialStore)->None:self.settings=settings;self.secrets=secrets
    def public_state(self)->tuple[dict[str,Any],...]:return tuple(item.public() for item in self.settings.all())
    def supports_content(self, kind: str)->tuple[bool,str]:
        """Report whether an enabled adapter can carry provider-neutral binary content."""
        wanted=str(kind or "").strip().lower()
        if wanted=="text":return True,"available"
        supported={"anthropic":{"image","document"}}
        enabled=[row for row in self.settings.all() if row.enabled]
        if any(wanted in supported.get(row.kind,set()) for row in enabled):return True,"available"
        return False,f"no enabled model provider adapter supports {wanted or 'requested'} content"
    def active(self):
        enabled=[row for row in self.settings.all() if row.enabled]
        if not enabled:raise RuntimeError("no model provider enabled")
        last:Exception|None=None
        for row in sorted(enabled,key=lambda x:(x.priority,x.local),reverse=True):
            try:return self._build(row)
            except Exception as exc:
                logger.warning("model provider %s could not be built; trying fallback",row.key,exc_info=True)
                last=exc
        raise RuntimeError(f"no configured model provider available: {last}")
    def generate(self,request:ModelRequest):
        enabled=[row for row in self.settings.all() if row.enabled]
        if not enabled:raise RuntimeError("no model provider enabled")
        errors=[]
        for row in sorted(enabled,key=lambda x:(x.priority,x.local),reverse=True):
            try:return self._build(row).generate(request)
            except Exception as exc:
                logger.warning("model provider %s failed; trying fallback",row.key,exc_info=True)
                errors.append(f"{row.key}: {exc}")
        raise RuntimeError("all enabled model providers failed: " + " | ".join(errors))
    def verify(self,key:str)->dict[str,Any]:
        provider=self._build(self.settings.get(key));response=provider.generate(ModelRequest(capability_id="system.provider.verify",system="Reply with exactly OK.",input="OK",max_output_chars=32));return {"ok":bool(response.text.strip()),"provider":response.provider_key,"model":response.model,"text":response.text[:64]}
    def _build(self,row:ProviderSettings):
        api_key=None
        if row.credential_ref:
            secret=self.secrets.retrieve(row.credential_ref);api_key=str(secret.get("api_key") or "").strip()
            if not api_key:raise RuntimeError("provider credential has no api_key")
        spec=ProviderSpec(key=row.key,model=row.model,provider_kind=row.kind,capabilities={},local=row.local,enabled=row.enabled,priority=row.priority,metadata=row.metadata)
        if row.kind=="openai_compatible":
            if not row.base_url:raise RuntimeError("openai-compatible provider requires base_url")
            return OpenAICompatibleChatProvider(spec,base_url=row.base_url,api_key=api_key)
        if row.kind=="openai":return OpenAIResponsesProvider(spec,api_key=api_key,base_url=row.base_url or "https://api.openai.com")
        if row.kind=="anthropic":
            workspace_id=str(row.metadata.get("workspace_id") or row.metadata.get("anthropic_workspace_id") or "").strip() or None
            return AnthropicMessagesProvider(spec,api_key=api_key,base_url=row.base_url or "https://api.anthropic.com",workspace_id=workspace_id)
        if row.kind=="gemini":return GeminiGenerateContentProvider(spec,api_key=api_key,base_url=row.base_url or "https://generativelanguage.googleapis.com")
        raise RuntimeError(f"unsupported provider kind: {row.kind}")
