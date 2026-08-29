from __future__ import annotations
import re, sqlite3
from contextlib import contextmanager
from pathlib import Path
from .models import MCPServer

_IDENTIFIER=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

class MCPServerStore:
    def __init__(self,path:str|Path)->None:self.path=Path(path)
    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:yield db
        finally:db.close()
    def initialize(self)->None:
        with self._db() as db:db.execute("""CREATE TABLE IF NOT EXISTS mcp_servers(server_id TEXT PRIMARY KEY,display_name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('mcp','n8n')),transport TEXT NOT NULL CHECK(transport IN ('streamable-http')),url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,credential_ref TEXT,timeout_sec REAL NOT NULL DEFAULT 30,read_timeout_sec REAL NOT NULL DEFAULT 300,last_error TEXT,last_discovered_at TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    def put(self,*,server_id:str,display_name:str,kind:str="mcp",transport:str="streamable-http",url:str,enabled:bool=True,credential_ref:str|None=None,timeout_sec:float=30,read_timeout_sec:float=300)->MCPServer:
        if not _IDENTIFIER.fullmatch(server_id):raise ValueError("MCP server id must be a safe identifier")
        if not display_name.strip() or not url.strip():raise ValueError("MCP server name/url required")
        if kind not in {"mcp","n8n"} or transport!="streamable-http":raise ValueError("unsupported MCP server kind or transport")
        with self._db() as db:db.execute("""INSERT INTO mcp_servers(server_id,display_name,kind,transport,url,enabled,credential_ref,timeout_sec,read_timeout_sec) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(server_id) DO UPDATE SET display_name=excluded.display_name,kind=excluded.kind,transport=excluded.transport,url=excluded.url,enabled=excluded.enabled,credential_ref=excluded.credential_ref,timeout_sec=excluded.timeout_sec,read_timeout_sec=excluded.read_timeout_sec,updated_at=CURRENT_TIMESTAMP""",(server_id,display_name,kind,transport,url,1 if enabled else 0,credential_ref,float(timeout_sec),float(read_timeout_sec)))
        return self.get(server_id)
    def get(self,server_id:str)->MCPServer:
        with self._db() as db:row=db.execute("SELECT * FROM mcp_servers WHERE server_id=?",(server_id,)).fetchone()
        if row is None:raise KeyError(server_id)
        return _server(row)
    def all(self)->tuple[MCPServer,...]:
        with self._db() as db:rows=db.execute("SELECT * FROM mcp_servers ORDER BY kind DESC,display_name").fetchall()
        return tuple(_server(r) for r in rows)
    def set_discovery(self,server_id:str,*,error:str|None)->None:
        with self._db() as db:db.execute("UPDATE mcp_servers SET last_error=?,last_discovered_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE server_id=?",(error,server_id))
    def delete(self,server_id:str)->None:
        with self._db() as db:db.execute("DELETE FROM mcp_servers WHERE server_id=?",(server_id,))

def _server(row:sqlite3.Row)->MCPServer:return MCPServer(row["server_id"],row["display_name"],row["kind"],row["transport"],row["url"],bool(row["enabled"]),row["credential_ref"],float(row["timeout_sec"]),float(row["read_timeout_sec"]),row["last_error"],row["last_discovered_at"],row["updated_at"])
