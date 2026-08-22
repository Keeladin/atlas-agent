from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from atlas_core.work import WorkStore
from atlas_core.work.store_common import WORK_SCHEMA_VERSION


class WorkPhaseBSchemaTests(unittest.TestCase):
    def test_version_two_store_adds_phase_b_columns_and_history_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.db"
            with sqlite3.connect(path) as db:
                db.executescript(
                    """
                    CREATE TABLE atlas_schema_meta (component TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT);
                    INSERT INTO atlas_schema_meta(component,version) VALUES ('work',2);
                    CREATE TABLE work_criteria (
                        id TEXT PRIMARY KEY, work_id TEXT, ordinal INTEGER, text TEXT,
                        status TEXT, evidence_artifact_ids_json TEXT DEFAULT '[]', note TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE work_steps (
                        id TEXT PRIMARY KEY, work_id TEXT, ordinal INTEGER, description TEXT,
                        capability TEXT, capability_version TEXT, status TEXT,
                        dependencies_json TEXT, input_artifact_ids_json TEXT, metadata_json TEXT,
                        created_at TEXT, updated_at TEXT
                    );
                    CREATE TABLE work_claims (
                        id TEXT PRIMARY KEY, work_id TEXT, step_id TEXT, kind TEXT, subject TEXT,
                        value_json TEXT, evidence_artifact_ids_json TEXT, confidence REAL,
                        created_at TEXT
                    );
                    """
                )
            store = WorkStore(path)
            store.initialize()
            with sqlite3.connect(path) as db:
                criterion_columns = {row[1] for row in db.execute("PRAGMA table_info(work_criteria)")}
                step_columns = {row[1] for row in db.execute("PRAGMA table_info(work_steps)")}
                claim_columns = {row[1] for row in db.execute("PRAGMA table_info(work_claims)")}
                artifact_columns = {row[1] for row in db.execute("PRAGMA table_info(work_artifacts)")}
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                version = db.execute("SELECT version FROM atlas_schema_meta WHERE component='work'").fetchone()[0]
            self.assertEqual(version, WORK_SCHEMA_VERSION)
            self.assertTrue({"satisfaction_policy", "semantic_verification", "verification_artifact_id"} <= criterion_columns)
            self.assertIn("contract_capability_ordinal", step_columns)
            self.assertTrue({"execution_id", "context_manifest_id"} <= claim_columns)
            self.assertIn("provenance_category", artifact_columns)
            self.assertTrue({"work_claim_criteria", "work_criterion_verifications"} <= tables)


if __name__ == "__main__":
    unittest.main()
