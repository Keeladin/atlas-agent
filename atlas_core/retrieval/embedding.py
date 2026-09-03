from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from .models import EmbeddingSpec


class EmbeddingProvider(Protocol):
    @property
    def spec(self) -> EmbeddingSpec: ...
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedProvider:
    """Lazy local ONNX embeddings with immutable model-snapshot identity."""

    def __init__(self, *, cache_dir: str | Path, model_name: str = "BAAI/bge-small-en-v1.5",
                 threads: int = 2, local_files_only: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self.threads = threads
        self.local_files_only = local_files_only
        self._model = None
        self._spec: EmbeddingSpec | None = None

    def _load(self):
        if self._model is not None:
            return self._model
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        import fastembed
        from fastembed import TextEmbedding
        model = TextEmbedding(
            model_name=self.model_name, cache_dir=str(self.cache_dir),
            threads=self.threads, local_files_only=self.local_files_only,
        )
        inner = getattr(model, "model", None)
        model_dir = Path(getattr(inner, "_model_dir", ""))
        revision = model_dir.name if model_dir.name else "unknown"
        dimensions = int(model.embedding_size)
        self._model = model
        self._spec = EmbeddingSpec(
            provider="fastembed", model=self.model_name, model_revision=revision,
            package_version=str(getattr(fastembed, "__version__", "unknown")),
            dimensions=dimensions, normalization="l2",
        )
        return model

    @property
    def spec(self) -> EmbeddingSpec:
        self._load()
        assert self._spec is not None
        return self._spec

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        return [list(map(float, row)) for row in model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return list(map(float, next(iter(model.query_embed([text])))))


class HashEmbeddingProvider:
    """Deterministic non-semantic provider for unit tests and offline diagnostics only."""

    def __init__(self, dimensions: int = 384) -> None:
        self._spec = EmbeddingSpec(
            provider="hash-test", model="blake2-token-hash", model_revision="1",
            package_version="1", dimensions=dimensions, normalization="l2",
            representation_version="test-only@1",
        )

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def _embed(self, text: str) -> list[float]:
        out = [0.0] * self._spec.dimensions
        for token in str(text).casefold().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % len(out)
            sign = 1.0 if digest[4] & 1 else -1.0
            out[index] += sign
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_embedding_provider(*, cache_dir: str | Path) -> EmbeddingProvider:
    kind = os.getenv("ATLAS_EMBEDDING_PROVIDER", "fastembed").strip().casefold()
    if kind == "hash":
        return HashEmbeddingProvider()
    if kind != "fastembed":
        raise RuntimeError(f"unsupported Atlas embedding provider: {kind}")
    offline = os.getenv("ATLAS_EMBEDDING_LOCAL_ONLY", "0").strip().casefold() in {"1", "true", "yes", "on"}
    return FastEmbedProvider(cache_dir=cache_dir, local_files_only=offline)
