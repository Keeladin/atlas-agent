from __future__ import annotations

from atlas_core.memory import MemoryStore


def test_memory_hybrid_retrieval_preserves_strong_sparse_ranking(tmp_path):
    store = MemoryStore(tmp_path / "work.db"); store.initialize()
    owner = "principal_owner"
    strong = store.add(principal_id=owner, title="alpha alpha alpha", content="alpha alpha alpha beta")
    store.add(principal_id=owner, title="ordinary", content="alpha beta gamma delta epsilon zeta")

    results = store.search(owner, "alpha", limit=10)
    assert len(results) >= 2
    assert results[0]["item_id"] == strong["item_id"]
    assert results[0]["retrieval"]["ranks"]["sparse"] == 1
