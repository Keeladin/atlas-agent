from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from atlas_core.work import WorkStore
from atlas_core.work.store_common import WorkStoreError
from atlas_core.work.store_common import WORK_SCHEMA_VERSION


class WorkPhaseBSchemaTests(unittest.TestCase):
    def test_fresh_store_initializes_current_phase_b_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.db"
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

    def test_obsolete_schema_version_requires_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.db"
            with sqlite3.connect(path) as db:
                db.executescript(
                    """
                    CREATE TABLE atlas_schema_meta (
                        component TEXT PRIMARY KEY, version INTEGER NOT NULL,
                        updated_at TEXT
                    );
                    INSERT INTO atlas_schema_meta(component,version) VALUES ('work',4);
                    CREATE TABLE work (id TEXT PRIMARY KEY);
                    """
                )
            with self.assertRaisesRegex(WorkStoreError, "Unsupported Work schema version"):
                WorkStore(path).initialize()

    def test_unversioned_work_schema_requires_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE work (id TEXT PRIMARY KEY)")
            with self.assertRaisesRegex(WorkStoreError, "Unversioned Work schema"):
                WorkStore(path).initialize()


if __name__ == "__main__":
    unittest.main()
