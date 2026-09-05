from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .models import IntakeResult, Obligation
from .temporal import normalize_satisfiable_until

INTAKE_STATES = ("complete", "partial", "failed")


class ObligationStore:
    """Authoritative ledger for what Atlas owes the authenticated owner."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS obligations(
                obligation_id TEXT PRIMARY KEY,
                owner_principal_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                owner_turn_id TEXT NOT NULL,
                grounding_excerpt TEXT NOT NULL,
                text TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('state_change','communication')),
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','resolved','withdrawn','superseded')),
                resolution_kind TEXT,
                resolution_ref TEXT,
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
                satisfiable_until TEXT,
                lapsed_at TEXT,
                temporal_grounding_excerpt TEXT,
                temporal_anchor_at TEXT,
                temporal_anchor_timezone TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                supersedes TEXT,
                FOREIGN KEY(owner_turn_id) REFERENCES chat_turns(turn_id) ON DELETE RESTRICT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
                FOREIGN KEY(supersedes) REFERENCES obligations(obligation_id) ON DELETE RESTRICT,
                CHECK(
                    (status='open' AND resolution_kind IS NULL
                     AND resolution_ref IS NULL AND resolved_at IS NULL)
                    OR (status='resolved' AND resolution_kind IS NOT NULL
                        AND resolution_ref IS NOT NULL AND resolved_at IS NOT NULL)
                    OR status IN ('withdrawn','superseded')
                ),
                CHECK(lapsed_at IS NULL OR status='open')
            );
            CREATE INDEX IF NOT EXISTS obligations_owner_turn
                ON obligations(owner_turn_id,created_at);
            CREATE INDEX IF NOT EXISTS obligations_open_owner
                ON obligations(owner_principal_id,status,created_at);
            CREATE TABLE IF NOT EXISTS obligation_events(
                event_id TEXT PRIMARY KEY,
                obligation_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(obligation_id) REFERENCES obligations(obligation_id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS obligation_events_obligation
                ON obligation_events(obligation_id,created_at);
            CREATE TABLE IF NOT EXISTS serviceability_assessments(
                assessment_id TEXT PRIMARY KEY,
                obligation_id TEXT NOT NULL,
                registry_fingerprint TEXT NOT NULL,
                search_basis_json TEXT NOT NULL,
                owner_facing_turn_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(obligation_id) REFERENCES obligations(obligation_id) ON DELETE RESTRICT,
                FOREIGN KEY(owner_facing_turn_id) REFERENCES chat_turns(turn_id) ON DELETE RESTRICT
            );
            """)

    @staticmethod
    def _row(row: sqlite3.Row) -> Obligation:
        return Obligation(
            row["obligation_id"], row["owner_principal_id"], row["conversation_id"],
            row["owner_turn_id"], row["grounding_excerpt"], row["text"], row["kind"],
            row["status"], row["resolution_kind"], row["resolution_ref"],
            int(row["revision"]), row["satisfiable_until"], row["lapsed_at"],
            row["temporal_grounding_excerpt"], row["temporal_anchor_at"],
            row["temporal_anchor_timezone"], row["created_at"], row["resolved_at"],
            row["supersedes"],
        )

    def get(self, obligation_id: str) -> Obligation:
        with self._db() as db:
            row = db.execute("SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)).fetchone()
        if row is None:
            raise KeyError(obligation_id)
        return self._row(row)

    def for_turn(self, owner_turn_id: str) -> tuple[Obligation, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM obligations WHERE owner_turn_id=? ORDER BY created_at,rowid",
                (owner_turn_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def open_for_turn(self, owner_turn_id: str) -> tuple[Obligation, ...]:
        return tuple(item for item in self.for_turn(owner_turn_id) if item.status == "open")


    def list_open(self, limit: int = 1000) -> tuple[Obligation, ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM obligations WHERE status='open' ORDER BY created_at,rowid LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def commit_intake(
        self, owner_turn_id: str, candidates: Iterable[dict[str, Any]], *,
        attempts: int, provider: str | None, model: str | None,
        unmapped_spans: Iterable[str] = (),
    ) -> IntakeResult:
        rows = [dict(item) for item in candidates]
        unmapped_list = [str(item) for item in unmapped_spans if str(item)]
        with self._db() as db:
            turn = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (owner_turn_id,)).fetchone()
            if turn is None:
                raise KeyError(owner_turn_id)
            if turn["role"] != "user" or not turn["owner_principal_id"]:
                raise ValueError("obligation intake requires an authenticated owner turn")
            if turn["intake_status"] != "failed":
                raise ValueError(f"owner turn intake is not writable from {turn['intake_status']}")
            if db.execute(
                "SELECT 1 FROM obligations WHERE owner_turn_id=? LIMIT 1", (owner_turn_id,)
            ).fetchone():
                raise RuntimeError("incomplete intake cannot already own obligation rows")

            obligation_ids: list[str] = []
            for item in rows:
                excerpt = str(item.get("grounding_excerpt") or "")
                text = str(item.get("text") or "").strip()
                kind = str(item.get("kind") or "")
                if not excerpt or excerpt not in turn["content"]:
                    raise ValueError(
                        "obligation grounding excerpt is not a substring of the owner turn"
                    )
                if not text:
                    raise ValueError("obligation text is required")
                if kind not in {"state_change", "communication"}:
                    raise ValueError("obligation kind must be state_change or communication")
                temporal_excerpt = item.get("temporal_grounding_excerpt")
                satisfiable_until = anchor_at = anchor_timezone = None
                if temporal_excerpt is not None:
                    if not isinstance(temporal_excerpt, str) or not temporal_excerpt or temporal_excerpt not in turn["content"]:
                        raise ValueError("temporal grounding excerpt is not a substring of the owner turn")
                    satisfiable_until, anchor_at, anchor_timezone = normalize_satisfiable_until(
                        temporal_excerpt, anchor_at=turn["created_at"]
                    )
                    if satisfiable_until is None and temporal_excerpt not in unmapped_list:
                        unmapped_list.append(temporal_excerpt)
                oid = f"obligation_{uuid4().hex}"
                db.execute(
                    """INSERT INTO obligations(
                           obligation_id,owner_principal_id,conversation_id,owner_turn_id,
                           grounding_excerpt,text,kind,satisfiable_until,
                           temporal_grounding_excerpt,temporal_anchor_at,temporal_anchor_timezone
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, turn["owner_principal_id"], turn["conversation_id"], owner_turn_id,
                        excerpt, text, kind, satisfiable_until,
                        temporal_excerpt, anchor_at, anchor_timezone,
                    ),
                )
                obligation_ids.append(oid)

            unmapped = tuple(dict.fromkeys(unmapped_list))
            if unmapped and rows:
                status, error_code = "partial", "intake_coverage_partial"
            elif unmapped:
                status, error_code = "failed", "intake_coverage_unmapped"
            else:
                status, error_code = "complete", None
            db.execute(
                """UPDATE chat_turns
                   SET intake_status=?,intake_attempts=?,intake_provider=?,intake_model=?,
                       intake_error_code=?,intake_unmapped_spans_json=?
                   WHERE turn_id=?""",
                (
                    status, int(attempts), provider, model, error_code,
                    json.dumps(unmapped), owner_turn_id,
                ),
            )
        return IntakeResult(
            owner_turn_id, status, int(attempts), tuple(obligation_ids), unmapped, error_code
        )

    def fail_intake(
        self, owner_turn_id: str, *, attempts: int, provider: str | None,
        model: str | None, error_code: str,
    ) -> IntakeResult:
        with self._db() as db:
            turn = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (owner_turn_id,)).fetchone()
            if turn is None:
                raise KeyError(owner_turn_id)
            if turn["intake_status"] != "failed":
                raise ValueError(f"owner turn intake is not writable from {turn['intake_status']}")
            if db.execute(
                "SELECT 1 FROM obligations WHERE owner_turn_id=? LIMIT 1", (owner_turn_id,)
            ).fetchone():
                raise RuntimeError("failed intake cannot coexist with committed obligations")
            db.execute(
                """UPDATE chat_turns
                   SET intake_status='failed',intake_attempts=?,intake_provider=?,intake_model=?,
                       intake_error_code=?,intake_unmapped_spans_json='[]'
                   WHERE turn_id=?""",
                (int(attempts), provider, model, str(error_code), owner_turn_id),
            )
        return IntakeResult(owner_turn_id, "failed", int(attempts), (), (), str(error_code))

    def intake_attention(self, limit: int = 200) -> tuple[dict[str, Any], ...]:
        """Return owner turns whose fail-closed intake did not complete."""
        with self._db() as db:
            rows = db.execute(
                """SELECT turn_id,conversation_id,owner_principal_id,content,intake_status,
                          intake_error_code,intake_unmapped_spans_json,created_at
                   FROM chat_turns
                   WHERE role='user' AND intake_status!='complete'
                   ORDER BY created_at,rowid LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["unmapped_spans"] = json.loads(
                item.pop("intake_unmapped_spans_json") or "[]"
            )
            result.append(item)
        return tuple(result)

    def grounding_violations(self) -> tuple[str, ...]:
        with self._db() as db:
            rows = db.execute(
                """SELECT o.obligation_id
                   FROM obligations o
                   JOIN chat_turns t ON t.turn_id=o.owner_turn_id
                   WHERE t.role!='user'
                      OR t.owner_principal_id!=o.owner_principal_id
                      OR instr(t.content,o.grounding_excerpt)=0
                      OR (o.temporal_grounding_excerpt IS NOT NULL
                          AND instr(t.content,o.temporal_grounding_excerpt)=0)"""
            ).fetchall()
        return tuple(row["obligation_id"] for row in rows)

    def begin_attempt(self, owner_turn_id: str) -> int:
        """Record an intake attempt while keeping the owner turn fail-closed."""
        with self._db() as db:
            row = db.execute(
                "SELECT intake_status,intake_attempts FROM chat_turns WHERE turn_id=?",
                (owner_turn_id,),
            ).fetchone()
            if row is None:
                raise KeyError(owner_turn_id)
            if row["intake_status"] != "failed":
                raise ValueError(f"owner turn intake is not writable from {row['intake_status']}")
            if db.execute(
                "SELECT 1 FROM obligations WHERE owner_turn_id=? LIMIT 1", (owner_turn_id,)
            ).fetchone():
                raise RuntimeError("failed intake cannot already own obligation rows")
            attempts = int(row["intake_attempts"] or 0) + 1
            changed = db.execute(
                """UPDATE chat_turns
                   SET intake_attempts=?,intake_error_code='intake_not_completed'
                   WHERE turn_id=? AND intake_status='failed'""",
                (attempts, owner_turn_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("owner turn intake attempt changed concurrently")
        return attempts

    def revisions(self, obligation_ids: Iterable[str]) -> dict[str, tuple[int, str]]:
        ids = tuple(dict.fromkeys(str(item) for item in obligation_ids if str(item)))
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._db() as db:
            rows = db.execute(
                f"SELECT obligation_id,revision,status FROM obligations WHERE obligation_id IN ({marks})",
                ids,
            ).fetchall()
        return {
            row["obligation_id"]: (int(row["revision"]), row["status"])
            for row in rows
        }

    def resolve(
        self, obligation_id: str, *, base_revision: int,
        resolution_kind: str, resolution_ref: str,
    ) -> Obligation:
        resolution_kind = str(resolution_kind or "").strip()
        resolution_ref = str(resolution_ref or "").strip()
        if not resolution_kind or not resolution_ref:
            raise ValueError("resolution kind and reference are required")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(obligation_id)
            if row["status"] != "open" or int(row["revision"]) != int(base_revision):
                raise ValueError("obligation revision changed or is not open")
            if row["kind"] == "communication" and resolution_kind == "fulfilled":
                raise ValueError("fulfilled communication must be persisted atomically with its verified Chat turn")
            if row["kind"] == "state_change" and resolution_kind == "fulfilled":
                if not resolution_ref.startswith("evidence:"):
                    raise ValueError("fulfilled state change requires execution/observation evidence authority")
            if resolution_kind == "declined_policy" and not resolution_ref.startswith("action:"):
                raise ValueError("policy decline requires an Action occurrence reference")
            db.execute(
                """UPDATE obligations
                   SET status='resolved',resolution_kind=?,resolution_ref=?,resolved_at=CURRENT_TIMESTAMP,
                       lapsed_at=NULL,revision=revision+1
                   WHERE obligation_id=? AND status='open' AND revision=?""",
                (resolution_kind, resolution_ref, obligation_id, int(base_revision)),
            )
        return self.get(obligation_id)

    def observe_lapses(self, now_iso: str) -> tuple[str, ...]:
        changed: list[str] = []
        with self._db() as db:
            rows = db.execute(
                """SELECT obligation_id,satisfiable_until FROM obligations
                   WHERE status='open' AND satisfiable_until IS NOT NULL AND lapsed_at IS NULL
                     AND datetime(satisfiable_until) <= datetime(?)""",
                (now_iso,),
            ).fetchall()
            for row in rows:
                oid = row["obligation_id"]
                stamped = db.execute(
                    """UPDATE obligations
                       SET lapsed_at=?,revision=revision+1
                       WHERE obligation_id=? AND status='open' AND lapsed_at IS NULL""",
                    (now_iso, oid),
                ).rowcount
                if not stamped:
                    continue
                db.execute(
                    "INSERT INTO obligation_events(event_id,obligation_id,kind,payload_json) VALUES (?,?,?,?)",
                    (
                        f"obligation_event_{uuid4().hex}", oid, "lapse_observed",
                        json.dumps({"observed_at": now_iso, "satisfiable_until": row["satisfiable_until"]},
                                   sort_keys=True, separators=(",", ":")),
                    ),
                )
                changed.append(oid)
        return tuple(changed)

    def events(self, obligation_id: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM obligation_events WHERE obligation_id=? ORDER BY created_at,rowid",
                (obligation_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return tuple(result)

    def persist_communication_report(
        self, conversation_id: str, content: str, *,
        obligation_revisions: dict[str, int], metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not obligation_revisions:
            raise ValueError("communication report requires at least one obligation")
        report_meta = dict(metadata or {})
        direct = report_meta.get("communication_delivery") if isinstance(report_meta.get("communication_delivery"), dict) else None
        background = report_meta.get("obligation_report") if isinstance(report_meta.get("obligation_report"), dict) else None
        direct_ids = set(str(item) for item in (direct or {}).get("fulfilled_obligation_ids", []) if str(item))
        report_ids = set(str(item) for item in (background or {}).get("obligation_ids", []) if str(item))
        verified_direct = bool(direct and direct.get("grounded") is True and set(obligation_revisions).issubset(direct_ids))
        verification = (background or {}).get("verification") if isinstance((background or {}).get("verification"), dict) else None
        verified_background = bool(verification and verification.get("grounded") is True and set(obligation_revisions).issubset(report_ids))
        if not (verified_direct or verified_background):
            raise ValueError("communication resolution requires verified persisted-report metadata")
        turn_id = f"turn_{uuid4().hex}"
        encoded = json.dumps(report_meta, sort_keys=True, separators=(",", ":"), default=str)
        with self._db() as db:
            for obligation_id, revision in obligation_revisions.items():
                row = db.execute(
                    "SELECT conversation_id,kind,status,revision FROM obligations WHERE obligation_id=?",
                    (obligation_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(obligation_id)
                if row["conversation_id"] != conversation_id or row["kind"] != "communication":
                    raise ValueError("communication report obligation scope changed")
                if row["status"] != "open" or int(row["revision"]) != int(revision):
                    raise ValueError("communication report snapshot is stale")
            db.execute(
                "INSERT INTO chat_turns(turn_id,conversation_id,role,content,metadata_json) VALUES (?,?,?,?,?)",
                (turn_id, conversation_id, "assistant", content, encoded),
            )
            for obligation_id, revision in obligation_revisions.items():
                changed = db.execute(
                    """UPDATE obligations
                       SET status='resolved',resolution_kind='fulfilled',resolution_ref=?,
                           resolved_at=CURRENT_TIMESTAMP,lapsed_at=NULL,revision=revision+1
                       WHERE obligation_id=? AND kind='communication'
                         AND status='open' AND revision=?""",
                    (f"chat_turn:{turn_id}", obligation_id, int(revision)),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("communication obligation changed during report persistence")
            db.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE conversation_id=?",
                (conversation_id,),
            )
            row = db.execute("SELECT * FROM chat_turns WHERE turn_id=?", (turn_id,)).fetchone()
        return {
            "turn_id": row["turn_id"], "conversation_id": row["conversation_id"],
            "role": row["role"], "content": row["content"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def withdraw(self, obligation_id: str, *, actor_turn_id: str, grounding_excerpt: str) -> Obligation:
        with self._db() as db:
            obligation = db.execute("SELECT rowid,* FROM obligations WHERE obligation_id=?", (obligation_id,)).fetchone()
            actor = db.execute("SELECT rowid,* FROM chat_turns WHERE turn_id=?", (actor_turn_id,)).fetchone()
            source = db.execute("SELECT rowid FROM chat_turns WHERE turn_id=?", (obligation["owner_turn_id"],)).fetchone() if obligation else None
            if obligation is None:
                raise KeyError(obligation_id)
            if actor is None or actor["role"] != "user":
                raise ValueError("withdrawal requires an authenticated owner turn")
            if actor["owner_principal_id"] != obligation["owner_principal_id"]:
                raise ValueError("withdrawal owner does not match obligation owner")
            if not grounding_excerpt or grounding_excerpt not in actor["content"]:
                raise ValueError("withdrawal grounding must be verbatim owner language")
            if source is None or int(actor["rowid"]) <= int(source["rowid"]):
                raise ValueError("withdrawal must come from a later owner turn")
            changed = db.execute(
                """UPDATE obligations
                   SET status='withdrawn',resolution_kind='withdrawn',resolution_ref=?,
                       resolved_at=CURRENT_TIMESTAMP,lapsed_at=NULL,revision=revision+1
                   WHERE obligation_id=? AND status='open'""",
                (f"chat_turn:{actor_turn_id}", obligation_id),
            ).rowcount
            if changed != 1:
                raise ValueError("only an open obligation can be withdrawn")
            db.execute(
                "INSERT INTO obligation_events(event_id,obligation_id,kind,payload_json) VALUES (?,?,?,?)",
                (
                    f"obligation_event_{uuid4().hex}", obligation_id, "withdrawn_by_owner",
                    json.dumps({"actor_turn_id": actor_turn_id, "grounding_excerpt": grounding_excerpt},
                               sort_keys=True, separators=(",", ":")),
                ),
            )
        return self.get(obligation_id)

    def supersede(self, older_obligation_id: str, newer_obligation_id: str) -> tuple[Obligation, Obligation]:
        with self._db() as db:
            old = db.execute("SELECT * FROM obligations WHERE obligation_id=?", (older_obligation_id,)).fetchone()
            new = db.execute("SELECT * FROM obligations WHERE obligation_id=?", (newer_obligation_id,)).fetchone()
            if old is None or new is None:
                raise KeyError(older_obligation_id if old is None else newer_obligation_id)
            if old["owner_principal_id"] != new["owner_principal_id"]:
                raise ValueError("supersession owner mismatch")
            if old["status"] != "open" or new["status"] != "open":
                raise ValueError("supersession requires open obligations")
            old_turn = db.execute(
                "SELECT rowid FROM chat_turns WHERE turn_id=?", (old["owner_turn_id"],)
            ).fetchone()
            new_turn = db.execute(
                "SELECT rowid FROM chat_turns WHERE turn_id=?", (new["owner_turn_id"],)
            ).fetchone()
            if old_turn is None or new_turn is None or int(new_turn["rowid"]) <= int(old_turn["rowid"]):
                raise ValueError("replacement obligation must come from a later owner turn")
            db.execute(
                "UPDATE obligations SET supersedes=? WHERE obligation_id=?",
                (older_obligation_id, newer_obligation_id),
            )
            changed = db.execute(
                """UPDATE obligations
                   SET status='superseded',resolution_kind='superseded',resolution_ref=?,
                       resolved_at=CURRENT_TIMESTAMP,lapsed_at=NULL,revision=revision+1
                   WHERE obligation_id=? AND status='open'""",
                (f"obligation:{newer_obligation_id}", older_obligation_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("supersession lost its open-state guard")
        return self.get(older_obligation_id), self.get(newer_obligation_id)

    def resolve_unserviceable(
        self, obligation_id: str, *, base_revision: int,
        registry_fingerprint: str, search_basis: dict[str, Any], owner_facing_turn_id: str,
    ) -> Obligation:
        assessment_id = f"serviceability_{uuid4().hex}"
        with self._db() as db:
            obligation = db.execute(
                "SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)
            ).fetchone()
            turn = db.execute(
                "SELECT role,conversation_id FROM chat_turns WHERE turn_id=?", (owner_facing_turn_id,)
            ).fetchone()
            if obligation is None:
                raise KeyError(obligation_id)
            if turn is None or turn["role"] != "assistant" or turn["conversation_id"] != obligation["conversation_id"]:
                raise ValueError("unserviceable resolution requires a persisted owner-facing explanation")
            if obligation["status"] != "open" or int(obligation["revision"]) != int(base_revision):
                raise ValueError("obligation revision changed or is not open")
            db.execute(
                """INSERT INTO serviceability_assessments(
                       assessment_id,obligation_id,registry_fingerprint,search_basis_json,owner_facing_turn_id
                   ) VALUES (?,?,?,?,?)""",
                (assessment_id, obligation_id, registry_fingerprint,
                 json.dumps(search_basis, sort_keys=True, separators=(",", ":"), default=str),
                 owner_facing_turn_id),
            )
            changed = db.execute(
                """UPDATE obligations
                   SET status='resolved',resolution_kind='unserviceable',resolution_ref=?,
                       resolved_at=CURRENT_TIMESTAMP,lapsed_at=NULL,revision=revision+1
                   WHERE obligation_id=? AND status='open' AND revision=?""",
                (
                    f"serviceability_assessment:{assessment_id}|chat_turn:{owner_facing_turn_id}",
                    obligation_id, int(base_revision),
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("unserviceable resolution changed concurrently")
        return self.get(obligation_id)

    def stale_unserviceable(self, current_registry_fingerprint: str) -> tuple[dict[str, Any], ...]:
        with self._db() as db:
            rows = db.execute(
                """SELECT o.obligation_id,o.text,a.assessment_id,a.registry_fingerprint,a.created_at
                   FROM obligations o
                   JOIN serviceability_assessments a ON a.obligation_id=o.obligation_id
                   WHERE o.status='resolved' AND o.resolution_kind='unserviceable'
                     AND a.registry_fingerprint!=?
                   ORDER BY a.created_at,a.rowid""",
                (current_registry_fingerprint,),
            ).fetchall()
        return tuple(dict(row) for row in rows)
