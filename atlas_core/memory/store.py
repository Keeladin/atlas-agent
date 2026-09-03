from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_core.database import WorkDatabase, as_work_database
from atlas_core.retrieval import EmbeddingProvider, RankedCandidate, build_embedding_provider, reciprocal_rank_fusion

from .models import MemoryItem

_WS = re.compile(r"\s+")


def normalize_memory_text(value: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", str(value))).strip().casefold()


def memory_content_hash(value: str) -> str:
    return hashlib.sha256(normalize_memory_text(value).encode("utf-8")).hexdigest()


def _vector_blob(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)

class MemoryStore:
    """Greenfield Memory V2. Canonical rows are truth; search representations are derived."""

    def __init__(self, database: WorkDatabase | str | Path, embedder: EmbeddingProvider | None = None) -> None:
        self.database = as_work_database(database)
        self.path = self.database.path
        self.embedder = embedder or build_embedding_provider(cache_dir=self.path.parent / "models" / "embeddings")

    @contextmanager
    def _db(self, db: sqlite3.Connection | None = None):
        with self.database.connection(db) as conn:
            yield conn

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_v2_items(
                memory_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL UNIQUE,
                principal_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                grounding_excerpt TEXT,
                source_ref TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','superseded','retracted')),
                supersedes_pk INTEGER REFERENCES memory_v2_items(memory_pk) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                retracted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS memory_v2_principal_state
                ON memory_v2_items(principal_id,state,updated_at);
            CREATE INDEX IF NOT EXISTS memory_v2_supersedes
                ON memory_v2_items(principal_id,supersedes_pk);
            CREATE UNIQUE INDEX IF NOT EXISTS memory_v2_active_hash
                ON memory_v2_items(principal_id,content_hash) WHERE state='active';

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_v2_fts USING fts5(
                title,content,content='memory_v2_items',content_rowid='memory_pk',tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memory_v2_ai AFTER INSERT ON memory_v2_items BEGIN
                INSERT INTO memory_v2_fts(rowid,title,content) VALUES(new.memory_pk,new.title,new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_v2_ad AFTER DELETE ON memory_v2_items BEGIN
                INSERT INTO memory_v2_fts(memory_v2_fts,rowid,title,content)
                    VALUES('delete',old.memory_pk,old.title,old.content);
            END;

            CREATE TABLE IF NOT EXISTS memory_v2_index_generations(
                generation_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                package_version TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                normalization TEXT NOT NULL,
                representation_version TEXT NOT NULL,
                vector_table TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('candidate','active','retired')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                verified_at TEXT,
                activated_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS memory_v2_one_active_generation
                ON memory_v2_index_generations(state) WHERE state='active';
            CREATE UNIQUE INDEX IF NOT EXISTS memory_v2_one_candidate_generation
                ON memory_v2_index_generations(state) WHERE state='candidate';

            CREATE TABLE IF NOT EXISTS memory_v2_representations(
                generation_id TEXT NOT NULL REFERENCES memory_v2_index_generations(generation_id) ON DELETE CASCADE,
                memory_pk INTEGER NOT NULL REFERENCES memory_v2_items(memory_pk) ON DELETE CASCADE,
                input_hash TEXT NOT NULL,
                PRIMARY KEY(generation_id,memory_pk)
            );
            """)
            self._install_duplicate_guards(db)
            self._repair_fts_if_needed(db)
            if db.execute("SELECT 1 FROM memory_v2_items LIMIT 1").fetchone() is not None:
                self._ensure_active_generation(db)

    @staticmethod
    def _install_duplicate_guards(db: sqlite3.Connection) -> None:
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS memory_v2_duplicate_active_insert
        BEFORE INSERT ON memory_v2_items
        WHEN NEW.state='active' AND EXISTS(
            SELECT 1 FROM memory_v2_items
            WHERE principal_id=NEW.principal_id AND state='active' AND content_hash=NEW.content_hash
        )
        BEGIN SELECT RAISE(ABORT,'duplicate active memory'); END;
        CREATE TRIGGER IF NOT EXISTS memory_v2_duplicate_active_update
        BEFORE UPDATE OF state,content_hash,principal_id ON memory_v2_items
        WHEN NEW.state='active' AND EXISTS(
            SELECT 1 FROM memory_v2_items
            WHERE principal_id=NEW.principal_id AND state='active'
              AND content_hash=NEW.content_hash AND memory_pk!=OLD.memory_pk
        )
        BEGIN SELECT RAISE(ABORT,'duplicate active memory'); END;
        """)

    @staticmethod
    def _repair_fts_if_needed(db: sqlite3.Connection) -> None:
        canonical = int(db.execute("SELECT COUNT(*) FROM memory_v2_items").fetchone()[0])
        indexed = int(db.execute("SELECT COUNT(*) FROM memory_v2_fts").fetchone()[0])
        if canonical != indexed:
            db.execute("INSERT INTO memory_v2_fts(memory_v2_fts) VALUES('rebuild')")

    def _generation_matches(self, row: sqlite3.Row | None) -> bool:
        if row is None:
            return False
        spec = self.embedder.spec
        return (
            row["provider"], row["model"], row["model_revision"], row["package_version"],
            int(row["dimensions"]), row["normalization"], row["representation_version"],
        ) == spec.identity

    @staticmethod
    def _active_generation(db: sqlite3.Connection) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM memory_v2_index_generations WHERE state='active' LIMIT 1"
        ).fetchone()
    def _ensure_active_generation(self, db: sqlite3.Connection) -> sqlite3.Row:
        active = self._active_generation(db)
        if self._generation_matches(active):
            return active
        return self._build_activate_generation(db, previous=active)

    def _build_activate_generation(self, db: sqlite3.Connection,
                                   previous: sqlite3.Row | None) -> sqlite3.Row:
        spec = self.embedder.spec
        generation_id = f"memory_index_{uuid4().hex}"
        suffix = hashlib.sha256(generation_id.encode("utf-8")).hexdigest()[:16]
        table = f"memory_v2_vec_{suffix}"
        rows = db.execute(
            "SELECT memory_pk,principal_id,title,content FROM memory_v2_items WHERE state='active' ORDER BY memory_pk"
        ).fetchall()
        texts = [self._representation_text(row["title"], row["content"]) for row in rows]
        vectors = self.embedder.embed_documents(texts) if texts else []
        db.execute(
            """INSERT INTO memory_v2_index_generations(
               generation_id,provider,model,model_revision,package_version,dimensions,
               normalization,representation_version,vector_table,state)
               VALUES (?,?,?,?,?,?,?,?,?,'candidate')""",
            (generation_id, spec.provider, spec.model, spec.model_revision, spec.package_version,
             spec.dimensions, spec.normalization, spec.representation_version, table),
        )
        db.execute(
            f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{spec.dimensions}] distance_metric=cosine, principal_id text)"
        )
        for row, text, vector in zip(rows, texts, vectors, strict=True):
            db.execute(
                f"INSERT INTO {table}(rowid,embedding,principal_id) VALUES (?,?,?)",
                (row["memory_pk"], _vector_blob(vector), row["principal_id"]),
            )
            db.execute(
                "INSERT INTO memory_v2_representations(generation_id,memory_pk,input_hash) VALUES (?,?,?)",
                (generation_id, row["memory_pk"], memory_content_hash(text)),
            )
        vector_count = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        rep_count = int(db.execute(
            "SELECT COUNT(*) FROM memory_v2_representations WHERE generation_id=?", (generation_id,)
        ).fetchone()[0])
        if vector_count != len(rows) or rep_count != len(rows):
            raise RuntimeError("Memory V2 candidate index verification failed")
        db.execute(
            "UPDATE memory_v2_index_generations SET verified_at=CURRENT_TIMESTAMP WHERE generation_id=?",
            (generation_id,),
        )
        if previous is not None:
            db.execute(
                "UPDATE memory_v2_index_generations SET state='retired' WHERE generation_id=?",
                (previous["generation_id"],),
            )
        db.execute(
            "UPDATE memory_v2_index_generations SET state='active',activated_at=CURRENT_TIMESTAMP WHERE generation_id=?",
            (generation_id,),
        )
        return db.execute(
            "SELECT * FROM memory_v2_index_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()

    @staticmethod
    def _representation_text(title: str, content: str) -> str:
        return f"{title.strip()}\n\n{content.strip()}".strip()

    @staticmethod
    def _select_item_sql(where: str) -> str:
        return f"""SELECT m.*,
            (SELECT p.item_id FROM memory_v2_items p WHERE p.memory_pk=m.supersedes_pk) AS supersedes
            FROM memory_v2_items m WHERE {where}"""

    @staticmethod
    def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
        item = MemoryItem(
            item_id=row["item_id"], principal_id=row["principal_id"], title=row["title"],
            content=row["content"], grounding_excerpt=row["grounding_excerpt"], source_ref=row["source_ref"],
            metadata=json.loads(row["metadata_json"] or "{}"), state=row["state"],
            supersedes=row["supersedes"], created_at=row["created_at"], updated_at=row["updated_at"],
            retracted_at=row["retracted_at"],
        )
        return item.as_dict()

    def _index_active_row(self, db: sqlite3.Connection, generation: sqlite3.Row, *,
                          memory_pk: int, principal_id: str, title: str, content: str,
                          vector: list[float]) -> None:
        table = generation["vector_table"]
        db.execute(
            f"INSERT INTO {table}(rowid,embedding,principal_id) VALUES (?,?,?)",
            (memory_pk, _vector_blob(vector), principal_id),
        )
        text = self._representation_text(title, content)
        db.execute(
            "INSERT INTO memory_v2_representations(generation_id,memory_pk,input_hash) VALUES (?,?,?)",
            (generation["generation_id"], memory_pk, memory_content_hash(text)),
        )

    @staticmethod
    def _unindex_pk(db: sqlite3.Connection, memory_pk: int) -> None:
        generations = db.execute(
            "SELECT generation_id,vector_table FROM memory_v2_index_generations"
        ).fetchall()
        for generation in generations:
            db.execute(f"DELETE FROM {generation['vector_table']} WHERE rowid=?", (memory_pk,))
        db.execute("DELETE FROM memory_v2_representations WHERE memory_pk=?", (memory_pk,))

    def add(self, *, principal_id: str, title: str = "Memory", content: str,
            grounding_excerpt: str | None = None, source_ref: str | None = None,
            metadata: dict[str, Any] | None = None, supersedes: str | None = None,
            db: sqlite3.Connection | None = None) -> dict[str, Any]:
        title = title or "Memory"
        with self._db(db) as conn:
            generation = self._ensure_active_generation(conn)
            vector = self.embedder.embed_documents([self._representation_text(title, content)])[0]
            prior_pk = None
            if supersedes:
                prior = conn.execute(
                    "SELECT memory_pk,state FROM memory_v2_items WHERE item_id=? AND principal_id=?",
                    (supersedes, principal_id),
                ).fetchone()
                if prior is None:
                    raise KeyError(supersedes)
                if prior["state"] == "superseded":
                    raise ValueError("cannot supersede an already superseded memory")
                prior_pk = int(prior["memory_pk"])
                self._unindex_pk(conn, prior_pk)
                conn.execute(
                    "UPDATE memory_v2_items SET state='superseded',updated_at=CURRENT_TIMESTAMP WHERE memory_pk=?",
                    (prior_pk,),
                )
            item_id = f"memory_{uuid4().hex}"
            cursor = conn.execute(
                """INSERT INTO memory_v2_items(
                   item_id,principal_id,title,content,content_hash,grounding_excerpt,
                   source_ref,metadata_json,supersedes_pk)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (item_id, principal_id, title, content, memory_content_hash(content), grounding_excerpt,
                 source_ref, json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                 prior_pk),
            )
            memory_pk = int(cursor.lastrowid)
            self._index_active_row(
                conn, generation, memory_pk=memory_pk, principal_id=principal_id,
                title=title, content=content, vector=vector,
            )
            row = conn.execute(self._select_item_sql("m.memory_pk=?"), (memory_pk,)).fetchone()
        return self._decode_item(row)

    def update(self, *, principal_id: str, item_id: str, content: str, title: str | None = None,
               grounding_excerpt: str | None = None, source_ref: str | None = None,
               metadata: dict[str, Any] | None = None, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            prior = conn.execute(
                self._select_item_sql("m.item_id=? AND m.principal_id=?"), (item_id, principal_id)
            ).fetchone()
            if prior is None:
                raise KeyError(item_id)
            if prior["state"] == "superseded":
                raise ValueError("cannot update a superseded memory")
            return self.add(
                principal_id=principal_id, title=title or prior["title"], content=content,
                grounding_excerpt=grounding_excerpt, source_ref=source_ref,
                metadata=metadata if metadata is not None else json.loads(prior["metadata_json"] or "{}"),
                supersedes=item_id, db=conn,
            )

    def get(self, principal_id: str, item_id: str, *,
            db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute(
                self._select_item_sql("m.item_id=? AND m.principal_id=?"), (item_id, principal_id)
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._decode_item(row)

    def recent(self, principal_id: str, *, limit: int = 100,
               include_history: bool = False) -> tuple[dict[str, Any], ...]:
        where = "m.principal_id=?" if include_history else "m.principal_id=? AND m.state!='superseded'"
        sql = self._select_item_sql(where) + " ORDER BY m.updated_at DESC,m.memory_pk DESC LIMIT ?"
        with self._db() as db:
            rows = db.execute(sql, (principal_id, max(1, min(int(limit), 500)))).fetchall()
        return tuple(self._decode_item(row) for row in rows)

    def search(self, principal_id: str, query: str, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
        q = " ".join(part for part in str(query).strip().split() if part)
        if not q:
            return ()
        with self._db() as db:
            if db.execute(
                "SELECT 1 FROM memory_v2_items WHERE principal_id=? AND state='active' LIMIT 1", (principal_id,)
            ).fetchone() is None:
                return ()
            generation = self._ensure_active_generation(db)
            sparse = self._sparse_candidates(db, principal_id, q, limit=max(limit * 4, 24))
            dense = self._dense_candidates(db, generation, principal_id, q, limit=max(limit * 4, 24))
            fused = reciprocal_rank_fusion([sparse, dense], weights={"sparse": 1.1, "dense": 1.0})
            ids = [row.item_id for row in fused[: max(1, min(int(limit), 50))]]
            return tuple(self.get(principal_id, item_id, db=db) | {"retrieval": next(
                {"score": row.score, "ranks": row.ranks} for row in fused if row.item_id == item_id
            )} for item_id in ids)

    def _sparse_candidates(self, db: sqlite3.Connection, principal_id: str, query: str,
                           *, limit: int) -> list[RankedCandidate]:
        safe = " OR ".join('"' + part.replace('"', '') + '"' for part in query.split()[:12])
        try:
            rows = db.execute(
                """SELECT m.item_id,bm25(memory_v2_fts) AS score
                   FROM memory_v2_fts
                   JOIN memory_v2_items m ON m.memory_pk=memory_v2_fts.rowid
                   WHERE m.principal_id=? AND m.state='active' AND memory_v2_fts MATCH ?
                   ORDER BY bm25(memory_v2_fts) ASC,m.updated_at DESC LIMIT ?""",
                (principal_id, safe, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        exact = db.execute(
            "SELECT item_id FROM memory_v2_items WHERE principal_id=? AND state='active' AND item_id=?",
            (principal_id, query.strip()),
        ).fetchone()
        ordered = []
        if exact is not None:
            ordered.append((exact["item_id"], None))
        ordered.extend((row["item_id"], float(row["score"])) for row in rows if row["item_id"] != (exact["item_id"] if exact else None))
        return [
            RankedCandidate(item_id=item_id, rank=rank, source="sparse", raw_score=score)
            for rank, (item_id, score) in enumerate(ordered, 1)
        ]

    def _dense_candidates(self, db: sqlite3.Connection, generation: sqlite3.Row,
                          principal_id: str, query: str, *, limit: int) -> list[RankedCandidate]:
        vector = self.embedder.embed_query(query)
        table = generation["vector_table"]
        rows = db.execute(
            f"""SELECT v.rowid AS memory_pk,v.distance,m.item_id
                FROM {table} v
                JOIN memory_v2_items m ON m.memory_pk=v.rowid
                WHERE v.embedding MATCH ? AND v.k=? AND v.principal_id=? AND m.state='active'
                ORDER BY v.distance,m.item_id""",
            (_vector_blob(vector), limit, principal_id),
        ).fetchall()
        return [
            RankedCandidate(item_id=row["item_id"], rank=rank, source="dense", raw_score=float(row["distance"]))
            for rank, row in enumerate(rows, 1)
        ]

    def retract(self, principal_id: str, item_id: str, *,
                db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute(
                "SELECT memory_pk,state FROM memory_v2_items WHERE item_id=? AND principal_id=?",
                (item_id, principal_id),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["state"] == "retracted":
                return self.get(principal_id, item_id, db=conn)
            if row["state"] != "active":
                raise ValueError("only an active memory can be retracted")
            self._unindex_pk(conn, int(row["memory_pk"]))
            conn.execute(
                """UPDATE memory_v2_items SET state='retracted',retracted_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE memory_pk=?""",
                (row["memory_pk"],),
            )
            return self.get(principal_id, item_id, db=conn)

    def restore(self, principal_id: str, item_id: str, *,
                db: sqlite3.Connection | None = None) -> dict[str, Any]:
        with self._db(db) as conn:
            row = conn.execute(
                "SELECT * FROM memory_v2_items WHERE item_id=? AND principal_id=?",
                (item_id, principal_id),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["state"] == "active":
                return self.get(principal_id, item_id, db=conn)
            if row["state"] != "retracted":
                raise ValueError("only a retracted memory can be restored")
            generation = self._ensure_active_generation(conn)
            vector = self.embedder.embed_documents([
                self._representation_text(row["title"], row["content"])
            ])[0]
            conn.execute(
                """UPDATE memory_v2_items SET state='active',retracted_at=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE memory_pk=?""",
                (row["memory_pk"],),
            )
            self._index_active_row(
                conn, generation, memory_pk=int(row["memory_pk"]), principal_id=principal_id,
                title=row["title"], content=row["content"], vector=vector,
            )
            return self.get(principal_id, item_id, db=conn)

    def chain_for(self, principal_id: str, item_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            return self.chain(principal_id, item_id, db=db)

    def chain(self, principal_id: str, item_id: str, *,
              db: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
        start = db.execute(
            "SELECT memory_pk FROM memory_v2_items WHERE item_id=? AND principal_id=?",
            (item_id, principal_id),
        ).fetchone()
        if start is None:
            raise KeyError(item_id)
        queue = [int(start["memory_pk"])]
        visited: set[int] = set()
        items: list[dict[str, Any]] = []
        while queue:
            memory_pk = queue.pop(0)
            if memory_pk in visited:
                continue
            visited.add(memory_pk)
            row = db.execute(self._select_item_sql("m.memory_pk=? AND m.principal_id=?"),
                             (memory_pk, principal_id)).fetchone()
            if row is None:
                continue
            items.append(self._decode_item(row) | {"memory_pk": memory_pk})
            if row["supersedes_pk"] is not None:
                queue.append(int(row["supersedes_pk"]))
            children = db.execute(
                "SELECT memory_pk FROM memory_v2_items WHERE principal_id=? AND supersedes_pk=?",
                (principal_id, memory_pk),
            ).fetchall()
            queue.extend(int(child["memory_pk"]) for child in children)
        return tuple(items)

    def purge_chain(self, principal_id: str, item_id: str, *,
                    db: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
        chain = self.chain(principal_id, item_id, db=db)
        pks = sorted({int(item["memory_pk"]) for item in chain})
        if not pks:
            return ()
        placeholders = ",".join("?" for _ in pks)
        for generation in db.execute(
            "SELECT vector_table FROM memory_v2_index_generations"
        ).fetchall():
            db.execute(
                f"DELETE FROM {generation['vector_table']} WHERE rowid IN ({placeholders})", pks
            )
        db.execute(
            f"DELETE FROM memory_v2_representations WHERE memory_pk IN ({placeholders})", pks
        )
        db.execute(
            f"DELETE FROM memory_v2_items WHERE principal_id=? AND memory_pk IN ({placeholders})",
            (principal_id, *pks),
        )
        return chain

    def index_status(self) -> dict[str, Any]:
        with self._db() as db:
            generation = self._active_generation(db)
            if generation is None:
                return {"active_generation": None, "canonical": 0, "vectors": 0, "representations": 0}
            vectors = int(db.execute(
                f"SELECT COUNT(*) FROM {generation['vector_table']}"
            ).fetchone()[0])
            representations = int(db.execute(
                "SELECT COUNT(*) FROM memory_v2_representations WHERE generation_id=?",
                (generation["generation_id"],),
            ).fetchone()[0])
            canonical = int(db.execute(
                "SELECT COUNT(*) FROM memory_v2_items WHERE state='active'"
            ).fetchone()[0])
            return {
                "active_generation": dict(generation), "canonical": canonical,
                "vectors": vectors, "representations": representations,
            }

    def discard_retired_generations(self) -> int:
        with self._db() as db:
            rows = db.execute(
                "SELECT generation_id,vector_table FROM memory_v2_index_generations WHERE state='retired'"
            ).fetchall()
            for row in rows:
                db.execute(f"DROP TABLE {row['vector_table']}")
                db.execute("DELETE FROM memory_v2_index_generations WHERE generation_id=?", (row["generation_id"],))
            return len(rows)
