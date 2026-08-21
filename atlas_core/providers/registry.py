from __future__ import annotations

from .contracts import ModelProvider, ProviderSpec


class ProviderRegistryError(RuntimeError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        key = provider.spec.key
        if key in self._providers:
            raise ProviderRegistryError(f"Provider already registered: {key}")
        self._providers[key] = provider

    def get(self, key: str) -> ModelProvider:
        try:
            return self._providers[key]
        except KeyError as exc:
            raise ProviderRegistryError(f"Unknown provider: {key}") from exc

    def providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def specs(self) -> tuple[ProviderSpec, ...]:
        return tuple(provider.spec for provider in self.providers())
