from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MemoryRecord:
    id: int
    kind: str
    content: str
    symbol: str = ""
    strategy: str = ""
    importance: float = 0.5
    confidence: float = 0.5
    source: str = "bot"
    metadata: dict[str, Any] | None = None
    created_at: str = ""


class MemoryStore:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.enabled:
            self._init_db()

    def add(
        self,
        kind: str,
        content: str,
        *,
        symbol: str = "",
        strategy: str = "",
        importance: float = 0.5,
        confidence: float = 0.5,
        source: str = "bot",
        metadata: dict[str, Any] | None = None,
        evidence_links: list[str] | None = None,
        expires_at: str | None = None,
        related_run_id: int | None = None,
    ) -> int | None:
        if not self.enabled or not content.strip():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(metadata or {})
        if evidence_links:
            payload["evidence_links"] = evidence_links
        if expires_at:
            payload["expires_at"] = expires_at
        if related_run_id is not None:
            payload["related_run_id"] = int(related_run_id)
        metadata_json = json.dumps(payload, ensure_ascii=False, default=str)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memories
                  (kind, symbol, strategy, content, importance, confidence, source, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    symbol.upper(),
                    strategy,
                    content.strip(),
                    float(importance),
                    float(confidence),
                    source,
                    metadata_json,
                    now,
                    now,
                ),
            )
            rowid = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO memory_fts(rowid, kind, symbol, strategy, content) VALUES (?, ?, ?, ?, ?)",
                (rowid, kind, symbol.upper(), strategy, content.strip()),
            )
            return rowid

    def search(self, query: str, *, symbol: str = "", limit: int = 8) -> list[MemoryRecord]:
        if not self.enabled or not query.strip():
            return []
        symbol = symbol.upper()
        with self._connect() as conn:
            try:
                sql = """
                    SELECT m.*
                    FROM memory_fts f
                    JOIN memories m ON m.id = f.rowid
                    WHERE memory_fts MATCH ?
                """
                params: list[Any] = [fts_query(query)]
                if symbol:
                    sql += " AND m.symbol = ?"
                    params.append(symbol)
                sql += " ORDER BY m.importance DESC, m.created_at DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                if symbol:
                    rows = conn.execute(
                        """
                        SELECT * FROM memories
                        WHERE symbol = ? AND content LIKE ?
                        ORDER BY importance DESC, created_at DESC LIMIT ?
                        """,
                        (symbol, like, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM memories
                        WHERE content LIKE ?
                        ORDER BY importance DESC, created_at DESC LIMIT ?
                        """,
                        (like, limit),
                    ).fetchall()
        return [record_from_row(row) for row in rows]

    def recent(self, *, symbol: str = "", kind: str = "", strategy: str = "", limit: int = 8) -> list[MemoryRecord]:
        if not self.enabled:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if strategy:
            clauses.append("strategy = ?")
            params.append(strategy)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [record_from_row(row) for row in rows]

    def context(self, query: str, symbols: list[str], limit: int = 8) -> str:
        if not self.enabled:
            return ""
        records: list[MemoryRecord] = []
        seen: set[int] = set()
        for symbol in symbols:
            for record in self.search(query, symbol=symbol, limit=max(2, limit // 2)):
                if record.id not in seen:
                    records.append(record)
                    seen.add(record.id)
        if len(records) < limit:
            for record in self.search(query, limit=limit - len(records)):
                if record.id not in seen:
                    records.append(record)
                    seen.add(record.id)
        records = sorted(records, key=lambda r: (r.importance, r.created_at), reverse=True)[:limit]
        return "\n".join(format_memory(record) for record in records)

    def count(self) -> int:
        if not self.enabled:
            return 0
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,
                  symbol TEXT NOT NULL DEFAULT '',
                  strategy TEXT NOT NULL DEFAULT '',
                  content TEXT NOT NULL,
                  importance REAL NOT NULL DEFAULT 0.5,
                  confidence REAL NOT NULL DEFAULT 0.5,
                  source TEXT NOT NULL DEFAULT 'bot',
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(kind, symbol, strategy, content)
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_symbol ON memories(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")


class OpenSourceMemoryBridge:
    """Optional bridge for open-source memory libraries such as mem0.

    It is deliberately best-effort. The bot's canonical memory remains local
    SQLite so quota, embedding, or package issues cannot break monitoring.
    """

    def __init__(self, enabled: bool = False, backend: str = "mem0"):
        self.enabled = enabled
        self.backend = backend
        self.available = False
        self.detail = "disabled"
        self._memory = None
        if enabled:
            self._init_backend()

    def add(self, content: str, *, user_id: str = "portfolio-bot", metadata: dict[str, Any] | None = None) -> None:
        if not self.available or not self._memory:
            return
        try:
            self._memory.add(content, user_id=user_id, metadata=metadata or {})
        except Exception as exc:
            self.available = False
            self.detail = f"backend add failed: {exc}"

    def search(self, query: str, *, user_id: str = "portfolio-bot", limit: int = 5) -> list[str]:
        if not self.available or not self._memory:
            return []
        try:
            result = self._memory.search(query=query, user_id=user_id, limit=limit)
        except Exception as exc:
            self.available = False
            self.detail = f"backend search failed: {exc}"
            return []
        return normalize_mem0_results(result)

    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        return f"{self.backend}: {'available' if self.available else 'unavailable'} ({self.detail})"

    def _init_backend(self) -> None:
        if self.backend != "mem0":
            self.detail = f"unsupported backend: {self.backend}"
            return
        try:
            from mem0 import Memory  # type: ignore

            self._memory = Memory.from_config({"vector_store": {"provider": "memory"}})
            self.available = True
            self.detail = "loaded"
        except Exception as exc:
            self.available = False
            self.detail = str(exc)


def memory_path(data_dir: Path, sqlite_path: str) -> Path:
    path = Path(sqlite_path)
    if path.is_absolute():
        return path
    return data_dir / path


def record_from_row(row: sqlite3.Row) -> MemoryRecord:
    metadata = json.loads(row["metadata_json"] or "{}")
    return MemoryRecord(
        id=int(row["id"]),
        kind=str(row["kind"]),
        symbol=str(row["symbol"]),
        strategy=str(row["strategy"]),
        content=str(row["content"]),
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        source=str(row["source"]),
        metadata=metadata,
        created_at=str(row["created_at"]),
    )


def format_memory(record: MemoryRecord) -> str:
    prefix = f"[{record.kind}]"
    if record.symbol:
        prefix += f" {record.symbol}"
    if record.strategy:
        prefix += f" {record.strategy}"
    return f"- {prefix}: {record.content}"


def fts_query(query: str) -> str:
    terms = [term.strip('"').strip("'") for term in query.replace(":", " ").split() if term.strip()]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms[:12])


def normalize_mem0_results(result: Any) -> list[str]:
    if isinstance(result, dict):
        result = result.get("results", result.get("memories", []))
    if not isinstance(result, list):
        return []
    values: list[str] = []
    for item in result:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values.append(str(item.get("memory") or item.get("text") or item.get("content") or item))
        else:
            values.append(str(item))
    return values
