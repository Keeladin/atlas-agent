from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from atlas_core.database import WORK_DATABASE_PARTICIPANTS, WorkDatabase, open_work_db, verify_work_connection

ROOT = Path(__file__).resolve().parents[1]


def _module_path(module: str) -> Path:
    return ROOT / (module.replace(".", "/") + ".py")


def _direct_connect_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    module_aliases: set[str] = set()
    connect_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    module_aliases.add(alias.asname or "sqlite3")
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            for alias in node.names:
                if alias.name == "connect":
                    connect_aliases.add(alias.asname or "connect")
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            if isinstance(func.value, ast.Name) and func.value.id in module_aliases:
                lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in connect_aliases:
            lines.append(node.lineno)
    return lines


def test_work_database_participants_cannot_construct_sqlite_connections():
    for qualified in sorted(WORK_DATABASE_PARTICIPANTS):
        module, _name = qualified.rsplit(".", 1)
        path = _module_path(module)
        assert path.exists(), qualified
        assert _direct_connect_calls(path) == [], f"{qualified} bypasses WorkDatabase"


def test_work_database_factory_is_the_connection_constructor():
    path = ROOT / "atlas_core/database/work.py"
    calls = _direct_connect_calls(path)
    assert len(calls) == 1


def test_declared_participants_are_composed_with_work_database():
    tree = ast.parse((ROOT / "atlas_api/compose.py").read_text())
    calls: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, []).append(node)
    for qualified in sorted(WORK_DATABASE_PARTICIPANTS):
        _module, class_name = qualified.rsplit(".", 1)
        matching = calls.get(class_name, [])
        assert matching, f"{qualified} is declared but not composed"
        assert any(
            call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "work_database"
            for call in matching
        ), f"{qualified} is not composed with WorkDatabase"


def test_open_work_db_enforces_connection_invariants(tmp_path):
    conn = open_work_db(tmp_path / "work.db")
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("SELECT vec_version()").fetchone()[0]
    finally:
        conn.close()


def test_verify_work_connection_fails_closed_without_foreign_keys(tmp_path):
    conn = sqlite3.connect(tmp_path / "plain.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        with pytest.raises(RuntimeError, match="foreign-key enforcement"):
            verify_work_connection(conn)
    finally:
        conn.close()


def test_work_database_initialize_rejects_existing_fk_violations(tmp_path):
    path = tmp_path / "work.db"
    raw = sqlite3.connect(path)
    try:
        raw.executescript("""
        PRAGMA foreign_keys=OFF;
        CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id));
        INSERT INTO child(id,parent_id) VALUES(1,999);
        """)
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(RuntimeError, match="foreign-key violations"):
        WorkDatabase(path).initialize()
