"""Local SQLite Memory — private local SQLite/FTS5 memory provider.

This provider is intentionally dependency-light for Raspberry Pi deployments:
SQLite + FTS5 only, no cloud service, no embedding/vector dependencies.  It
supports three phases for a local-first memory workflow:

1. Local memory core: turns, durable memories, namespaces, CRUD/search tools.
2. Better recall: FTS5-ranked context injection and explicit context/search tools.
3. Review workflow: heuristic proposal extraction from sessions and tools/CLI to
   review, approve, reject, or promote proposed durable memories.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from agent.memory_provider import MemoryProvider
except ModuleNotFoundError:  # Allow standalone tests/package imports outside Hermes.
    class MemoryProvider:  # type: ignore[no-redef]
        pass

try:
    from tools.registry import tool_error
except ModuleNotFoundError:  # Allow standalone tests/package imports outside Hermes.
    def tool_error(message: str, **kwargs) -> str:  # type: ignore[no-redef]
        return json.dumps({"success": False, "error": message, **kwargs}, ensure_ascii=False)

logger = logging.getLogger(__name__)

__version__ = "0.1.2"

# Namespaces are intentionally user-defined; values are sanitized by _safe_namespace.
_ALLOWED_TYPES = {"fact", "preference", "decision", "project", "infrastructure", "handoff", "identity", "other"}
_DEFAULT_NAMESPACE = "default"
_MAX_CONTEXT_RESULTS = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(text: Any, limit: int = 25000) -> str:
    text = "" if text is None else str(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _safe_namespace(namespace: Any) -> str:
    ns = _clean_text(namespace or _DEFAULT_NAMESPACE, 64).lower().replace("-", "_")
    ns = re.sub(r"[^a-z0-9_.-]+", "_", ns)[:64].strip("_.-")
    return ns or _DEFAULT_NAMESPACE


def _safe_type(memory_type: Any) -> str:
    mt = _clean_text(memory_type or "fact", 64).lower().replace("-", "_")
    return mt if mt in _ALLOWED_TYPES else "other"


def _json_result(**kwargs) -> str:
    return json.dumps({"success": True, **kwargs}, ensure_ascii=False)


def _fts_query(query: str) -> str:
    """Build a forgiving FTS5 query from user text.

    Exact phrase matching is too brittle for recall ("private memory" should
    match "private local memory"). Use OR'd prefix tokens and keep syntax
    conservative so arbitrary user text cannot break MATCH parsing.
    """
    tokens = re.findall(r"[\w]+", query.lower())
    tokens = [t for t in tokens if len(t) > 1][:12]
    if not tokens:
        return '"' + query.replace('"', ' ') + '"'
    return " OR ".join(f"{t}*" for t in tokens)


def _dedupe_words(text: str, limit: int = 18) -> str:
    """Return a compact deterministic phrase for local/offline dreaming."""
    stop = {
        "about", "after", "also", "assistant", "because", "before", "context", "could",
        "bridge", "cycle", "debug", "default_profile_smoke_ok", "default", "from", "have",
        "include", "local", "memory", "message", "profile_final_smoke_ok", "profile_smoke_ok",
        "smoke", "test",
        "only", "private", "should", "that", "their", "there", "these",
        "this", "turn", "user", "wants", "with", "would", "your", "you", "the", "and",
        "for", "into", "is", "it", "to", "of", "a", "in", "on", "be", "as", "or",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9_'-]+", text.lower())
    kept: list[str] = []
    for word in words:
        word = word.strip("_'-")
        if len(word) < 3 or word in stop or word in kept:
            continue
        kept.append(word)
        if len(kept) >= limit:
            break
    return " ".join(kept)


def _dream_conclusion_text(messages: list[sqlite3.Row]) -> str:
    """Build a deterministic conclusion from recent messages without cloud/LLM calls."""
    combined = " ".join(_clean_text(m["content"], 700) for m in messages)
    phrase = _dedupe_words(combined)
    sample = _clean_text(messages[-1]["content"] if messages else combined, 220)
    if phrase:
        return f"Dreamed pattern from recent messages: {phrase}. Evidence: {sample}"
    return f"Dreamed pattern from recent messages: {sample}"

def _noise_regex() -> re.Pattern[str]:
    """Patterns that should not become durable dream conclusions."""
    return re.compile(
        r"(reply exactly|verification|health check|context[_-]?ok|bridge[_-]?ok|"
        r"test[_ -]?message|debug ok|\w*smoke\w*|default_profile_smoke_ok|"
        r"profile_smoke_ok|profile_final_smoke_ok|set up dreaming|"
        r"daily[_ -]?all[_ -]?bot[_ -]?dreaming|start at 3 am|end at 6 am|"
        r"3 am every day|called tool\(s\)|previous turn was interrupted|"
        r"conversation history contains|last tool result|temporary browser|noVNC|"
        r"host=127\.0\.0\.1|ssh -L|vnc\.html)",
        re.I,
    )


def _parse_ts(value: Any) -> float:
    text = _clean_text(value, 80)
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class _Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL DEFAULT 'default',
                    memory_type TEXT NOT NULL DEFAULT 'fact',
                    content TEXT NOT NULL,
                    source TEXT,
                    source_session TEXT,
                    confidence REAL NOT NULL DEFAULT 0.70,
                    importance REAL NOT NULL DEFAULT 0.60,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    id UNINDEXED,
                    namespace UNINDEXED,
                    memory_type UNINDEXED,
                    content,
                    source,
                    tokenize = 'porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT,
                    user_content TEXT,
                    assistant_content TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
                    id UNINDEXED,
                    namespace UNINDEXED,
                    session_id UNINDEXED,
                    content,
                    tokenize = 'porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS review_queue (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL DEFAULT 'default',
                    proposed_type TEXT NOT NULL DEFAULT 'fact',
                    content TEXT NOT NULL,
                    evidence TEXT,
                    source_session TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    confidence REAL NOT NULL DEFAULT 0.50,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS peers (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    handle TEXT,
                    role TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(namespace, handle)
                );
                CREATE TABLE IF NOT EXISTS memory_sessions (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    session_id TEXT,
                    peer_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    id UNINDEXED,
                    namespace UNINDEXED,
                    session_id UNINDEXED,
                    peer_id UNINDEXED,
                    role UNINDEXED,
                    content,
                    tokenize = 'porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS conclusions (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    session_id TEXT,
                    peer_id TEXT,
                    scope TEXT NOT NULL DEFAULT 'workspace',
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.70,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS conclusions_fts USING fts5(
                    id UNINDEXED,
                    namespace UNINDEXED,
                    session_id UNINDEXED,
                    peer_id UNINDEXED,
                    scope UNINDEXED,
                    content,
                    tokenize = 'porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS representations (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    peer_id TEXT,
                    kind TEXT NOT NULL DEFAULT 'peer_context',
                    content TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(namespace, peer_id, kind)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_ns_status ON memories(namespace, status);
                CREATE INDEX IF NOT EXISTS idx_review_ns_status ON review_queue(namespace, status);
                CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
                CREATE INDEX IF NOT EXISTS idx_workspaces_ns ON workspaces(namespace);
                CREATE INDEX IF NOT EXISTS idx_peers_ns ON peers(namespace);
                CREATE INDEX IF NOT EXISTS idx_memory_sessions_ns ON memory_sessions(namespace);
                CREATE INDEX IF NOT EXISTS idx_messages_ns_session ON messages(namespace, session_id);
                CREATE INDEX IF NOT EXISTS idx_conclusions_ns_scope ON conclusions(namespace, scope, status);
                CREATE INDEX IF NOT EXISTS idx_representations_ns_peer ON representations(namespace, peer_id);
                """
            )
            ts = _now()
            conn.execute(
                "INSERT OR IGNORE INTO workspaces(id, namespace, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
                (f"ws_{_DEFAULT_NAMESPACE}", _DEFAULT_NAMESPACE, ts, ts, json.dumps({"source": "default_namespace"}, ensure_ascii=False)),
            )
    def add_memory(self, content: str, namespace: str = _DEFAULT_NAMESPACE, memory_type: str = "fact",
                   source: str = "tool", source_session: str = "", confidence: float = 0.7,
                   importance: float = 0.6, metadata: Optional[dict] = None) -> dict:
        content = _clean_text(content, 5000)
        if not content:
            raise ValueError("content is required")
        namespace = _safe_namespace(namespace)
        memory_type = _safe_type(memory_type)
        mid = f"dlm_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO memories(id, namespace, memory_type, content, source, source_session,
                   confidence, importance, status, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (mid, namespace, memory_type, content, source, source_session, float(confidence),
                 float(importance), ts, ts, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO memories_fts(id, namespace, memory_type, content, source) VALUES (?, ?, ?, ?, ?)",
                (mid, namespace, memory_type, content, source or ""),
            )
        return self.get_memory(mid) or {"id": mid, "content": content}

    def get_memory(self, memory_id: str) -> Optional[dict]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def update_memory(self, memory_id: str, *, content: Optional[str] = None, status: Optional[str] = None,
                      importance: Optional[float] = None, confidence: Optional[float] = None) -> dict:
        current = self.get_memory(memory_id)
        if not current:
            raise ValueError(f"memory not found: {memory_id}")
        updates = []
        params: list[Any] = []
        if content is not None:
            content = _clean_text(content, 5000)
            if not content:
                raise ValueError("content cannot be empty")
            updates.append("content = ?")
            params.append(content)
        if status is not None:
            if status not in {"active", "archived", "deleted"}:
                raise ValueError("status must be active, archived, or deleted")
            updates.append("status = ?")
            params.append(status)
        if importance is not None:
            updates.append("importance = ?")
            params.append(float(importance))
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(float(confidence))
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(memory_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)
            conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
            refreshed = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if refreshed and refreshed["status"] == "active":
                conn.execute(
                    "INSERT INTO memories_fts(id, namespace, memory_type, content, source) VALUES (?, ?, ?, ?, ?)",
                    (refreshed["id"], refreshed["namespace"], refreshed["memory_type"], refreshed["content"], refreshed["source"] or ""),
                )
        return self.get_memory(memory_id) or {}

    def search_memories(self, query: str, namespace: str = _DEFAULT_NAMESPACE, limit: int = 8,
                        include_archived: bool = False) -> List[dict]:
        query = _clean_text(query, 500)
        namespace = _safe_namespace(namespace)
        limit = max(1, min(int(limit or 8), 25))
        if not query:
            sql = "SELECT * FROM memories WHERE namespace = ? AND status = 'active' ORDER BY importance DESC, updated_at DESC LIMIT ?"
            args: tuple[Any, ...] = (namespace, limit)
            if include_archived:
                sql = "SELECT * FROM memories WHERE namespace = ? AND status != 'deleted' ORDER BY importance DESC, updated_at DESC LIMIT ?"
            with self._lock, self._connect() as conn:
                rows = conn.execute(sql, args).fetchall()
            return [dict(r) for r in rows]
        # FTS query: token-prefix OR matching, fallback to LIKE on syntax errors.
        fts_query = _fts_query(query)
        status_filter = "m.status != 'deleted'" if include_archived else "m.status = 'active'"
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(
                    f"""SELECT m.*, bm25(memories_fts) AS score
                        FROM memories_fts JOIN memories m ON m.id = memories_fts.id
                        WHERE memories_fts MATCH ? AND m.namespace = ? AND {status_filter}
                        ORDER BY score ASC, m.importance DESC LIMIT ?""",
                    (fts_query, namespace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    f"""SELECT *, 0 AS score FROM memories
                        WHERE namespace = ? AND {status_filter} AND content LIKE ?
                        ORDER BY importance DESC, updated_at DESC LIMIT ?""",
                    (namespace, f"%{query}%", limit),
                ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                conn.executemany("UPDATE memories SET last_used_at = ? WHERE id = ?", [(_now(), i) for i in ids])
        return [dict(r) for r in rows]

    def add_turn(self, user_content: str, assistant_content: str, namespace: str, session_id: str, metadata: Optional[dict] = None) -> dict:
        tid = f"turn_{uuid.uuid4().hex[:12]}"
        user_content = _clean_text(user_content)
        assistant_content = _clean_text(assistant_content)
        ts = _now()
        namespace = _safe_namespace(namespace)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO turns(id, namespace, session_id, user_content, assistant_content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tid, namespace, session_id, user_content, assistant_content, ts, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO turns_fts(id, namespace, session_id, content) VALUES (?, ?, ?, ?)",
                (tid, namespace, session_id, f"User: {user_content}\nAssistant: {assistant_content}"),
            )
        return {"id": tid, "created_at": ts}

    def search_turns(self, query: str, namespace: str = _DEFAULT_NAMESPACE, limit: int = 6) -> List[dict]:
        query = _clean_text(query, 500)
        namespace = _safe_namespace(namespace)
        limit = max(1, min(int(limit or 6), 20))
        if not query:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM turns WHERE namespace = ? ORDER BY created_at DESC LIMIT ?", (namespace, limit)
                ).fetchall()
            return [dict(r) for r in rows]
        fts_query = _fts_query(query)
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(
                    """SELECT t.*, bm25(turns_fts) AS score
                       FROM turns_fts JOIN turns t ON t.id = turns_fts.id
                       WHERE turns_fts MATCH ? AND t.namespace = ?
                       ORDER BY score ASC LIMIT ?""",
                    (fts_query, namespace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """SELECT *, 0 AS score FROM turns
                       WHERE namespace = ? AND (user_content LIKE ? OR assistant_content LIKE ?)
                       ORDER BY created_at DESC LIMIT ?""",
                    (namespace, f"%{query}%", f"%{query}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def upsert_peer(self, handle: str, namespace: str = _DEFAULT_NAMESPACE, role: str = "user", metadata: Optional[dict] = None) -> dict:
        handle = _clean_text(handle or "default", 120)
        if not handle:
            raise ValueError("peer handle is required")
        namespace = _safe_namespace(namespace)
        role = _clean_text(role or "user", 64)
        ts = _now()
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT * FROM peers WHERE namespace = ? AND handle = ?", (namespace, handle)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE peers SET role = ?, updated_at = ?, metadata = ? WHERE id = ?",
                    (role, ts, json.dumps(metadata or {}, ensure_ascii=False), existing["id"]),
                )
                row = conn.execute("SELECT * FROM peers WHERE id = ?", (existing["id"],)).fetchone()
            else:
                pid = f"peer_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO peers(id, namespace, handle, role, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, namespace, handle, role, ts, ts, json.dumps(metadata or {}, ensure_ascii=False)),
                )
                row = conn.execute("SELECT * FROM peers WHERE id = ?", (pid,)).fetchone()
        return dict(row)

    def upsert_session(self, session_id: str, namespace: str = _DEFAULT_NAMESPACE, title: str = "", metadata: Optional[dict] = None) -> dict:
        session_id = _clean_text(session_id or f"session_{uuid.uuid4().hex[:12]}", 160)
        namespace = _safe_namespace(namespace)
        title = _clean_text(title, 240)
        ts = _now()
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT * FROM memory_sessions WHERE id = ? AND namespace = ?", (session_id, namespace)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE memory_sessions SET title = COALESCE(NULLIF(?, ''), title), updated_at = ?, metadata = ? WHERE id = ? AND namespace = ?",
                    (title, ts, json.dumps(metadata or {}, ensure_ascii=False), session_id, namespace),
                )
            else:
                conn.execute(
                    "INSERT INTO memory_sessions(id, namespace, title, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, namespace, title, ts, ts, json.dumps(metadata or {}, ensure_ascii=False)),
                )
            row = conn.execute("SELECT * FROM memory_sessions WHERE id = ? AND namespace = ?", (session_id, namespace)).fetchone()
        return dict(row)

    def add_message(self, content: str, namespace: str = _DEFAULT_NAMESPACE, session_id: str = "",
                    peer_id: str = "", role: str = "user", metadata: Optional[dict] = None) -> dict:
        content = _clean_text(content)
        if not content:
            raise ValueError("message content is required")
        namespace = _safe_namespace(namespace)
        role = _clean_text(role or "user", 64)
        session_id = _clean_text(session_id or "", 160)
        peer_id = _clean_text(peer_id or "", 120)
        mid = f"msg_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(id, namespace, session_id, peer_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, namespace, session_id, peer_id, role, content, ts, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO messages_fts(id, namespace, session_id, peer_id, role, content) VALUES (?, ?, ?, ?, ?, ?)",
                (mid, namespace, session_id, peer_id, role, content),
            )
        return {"id": mid, "namespace": namespace, "session_id": session_id, "peer_id": peer_id, "role": role, "content": content, "created_at": ts}

    def search_messages(self, query: str, namespace: str = _DEFAULT_NAMESPACE, limit: int = 8) -> List[dict]:
        query = _clean_text(query, 500)
        namespace = _safe_namespace(namespace)
        limit = max(1, min(int(limit or 8), 25))
        if not query:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE namespace = ? ORDER BY created_at DESC LIMIT ?", (namespace, limit)
                ).fetchall()
            return [dict(r) for r in rows]
        fts_query = _fts_query(query)
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(
                    """SELECT m.*, bm25(messages_fts) AS score
                       FROM messages_fts JOIN messages m ON m.id = messages_fts.id
                       WHERE messages_fts MATCH ? AND m.namespace = ?
                       ORDER BY score ASC LIMIT ?""",
                    (fts_query, namespace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """SELECT *, 0 AS score FROM messages
                       WHERE namespace = ? AND content LIKE ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (namespace, f"%{query}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def add_conclusion(self, content: str, namespace: str = _DEFAULT_NAMESPACE, session_id: str = "",
                       peer_id: str = "", scope: str = "workspace", confidence: float = 0.7,
                       metadata: Optional[dict] = None) -> dict:
        content = _clean_text(content, 4000)
        if not content:
            raise ValueError("conclusion content is required")
        namespace = _safe_namespace(namespace)
        scope = _clean_text(scope or "workspace", 64)
        if scope not in {"workspace", "peer", "session", "message"}:
            scope = "workspace"
        cid = f"con_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO conclusions(id, namespace, session_id, peer_id, scope, content, confidence, status, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (cid, namespace, _clean_text(session_id, 160), _clean_text(peer_id, 120), scope, content,
                 float(confidence), ts, ts, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO conclusions_fts(id, namespace, session_id, peer_id, scope, content) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, namespace, _clean_text(session_id, 160), _clean_text(peer_id, 120), scope, content),
            )
            row = conn.execute("SELECT * FROM conclusions WHERE id = ?", (cid,)).fetchone()
        return dict(row)

    def update_conclusion_status(self, conclusion_id: str, status: str = "archived") -> dict:
        if status not in {"active", "archived", "deleted"}:
            raise ValueError("status must be active, archived, or deleted")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM conclusions WHERE id = ?", (conclusion_id,)).fetchone()
            if not row:
                raise ValueError(f"conclusion not found: {conclusion_id}")
            conn.execute("UPDATE conclusions SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), conclusion_id))
            conn.execute("DELETE FROM conclusions_fts WHERE id = ?", (conclusion_id,))
            if status == "active":
                refreshed = conn.execute("SELECT * FROM conclusions WHERE id = ?", (conclusion_id,)).fetchone()
                conn.execute(
                    "INSERT INTO conclusions_fts(id, namespace, session_id, peer_id, scope, content) VALUES (?, ?, ?, ?, ?, ?)",
                    (refreshed["id"], refreshed["namespace"], refreshed["session_id"] or "", refreshed["peer_id"] or "", refreshed["scope"], refreshed["content"]),
                )
            updated = conn.execute("SELECT * FROM conclusions WHERE id = ?", (conclusion_id,)).fetchone()
        return dict(updated)

    def search_conclusions(self, query: str, namespace: str = _DEFAULT_NAMESPACE, limit: int = 8) -> List[dict]:
        query = _clean_text(query, 500)
        namespace = _safe_namespace(namespace)
        limit = max(1, min(int(limit or 8), 25))
        fts_query = _fts_query(query)
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(
                    """SELECT c.*, bm25(conclusions_fts) AS score
                       FROM conclusions_fts JOIN conclusions c ON c.id = conclusions_fts.id
                       WHERE conclusions_fts MATCH ? AND c.namespace = ? AND c.status = 'active'
                       ORDER BY score ASC, c.confidence DESC LIMIT ?""",
                    (fts_query, namespace, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """SELECT *, 0 AS score FROM conclusions
                       WHERE namespace = ? AND status = 'active' AND content LIKE ?
                       ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
                    (namespace, f"%{query}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def build_representation(self, namespace: str = _DEFAULT_NAMESPACE, peer_id: str = "", kind: str = "peer_context", limit: int = 12) -> dict:
        namespace = _safe_namespace(namespace)
        peer_id = _clean_text(peer_id or "", 120)
        kind = _clean_text(kind or "peer_context", 64)
        with self._lock, self._connect() as conn:
            if peer_id:
                rows = conn.execute(
                    """SELECT content, confidence, created_at FROM conclusions
                       WHERE namespace = ? AND status = 'active' AND (peer_id = ? OR scope = 'workspace')
                       ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
                    (namespace, peer_id, max(1, min(int(limit or 12), 25))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT content, confidence, created_at FROM conclusions
                       WHERE namespace = ? AND status = 'active'
                       ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
                    (namespace, max(1, min(int(limit or 12), 25))),
                ).fetchall()
        content = "\n".join(f"- {r['content']}" for r in rows) or "No active conclusions yet."
        rid = f"repr_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM representations WHERE namespace = ? AND peer_id = ? AND kind = ?",
                (namespace, peer_id, kind),
            ).fetchone()
            if existing:
                rid = existing["id"]
                conn.execute(
                    "UPDATE representations SET content = ?, source_count = ?, updated_at = ? WHERE id = ?",
                    (content, len(rows), ts, rid),
                )
            else:
                conn.execute(
                    "INSERT INTO representations(id, namespace, peer_id, kind, content, source_count, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, namespace, peer_id, kind, content, len(rows), ts, ts, json.dumps({"source": "build_representation"}, ensure_ascii=False)),
                )
            row = conn.execute("SELECT * FROM representations WHERE id = ?", (rid,)).fetchone()
        return dict(row)

    def graph_context(self, query: str, namespace: str = _DEFAULT_NAMESPACE, limit: int = 8) -> dict:
        ns = _safe_namespace(namespace)
        return {
            "namespace": ns,
            "memories": self.search_memories(query, namespace=ns, limit=limit),
            "conclusions": self.search_conclusions(query, namespace=ns, limit=limit),
            "messages": self.search_messages(query, namespace=ns, limit=min(5, limit)),
            "turns": self.search_turns(query, namespace=ns, limit=min(5, limit)),
        }

    def dream_cycle(self, namespace: str = _DEFAULT_NAMESPACE, peer_id: str = "", limit: int = 24) -> dict:
        """Consolidate recent raw messages into conclusions and representations.

        This is intentionally local/deterministic: no cloud calls, no hidden model
        dependency, and no cross-namespace reads. It is a safe "dream" pass that
        turns recent evidence into reviewable graph-style conclusions, then
        refreshes the peer/workspace representation card.
        """
        namespace = _safe_namespace(namespace)
        peer_id = _clean_text(peer_id or "", 120)
        limit = max(1, min(int(limit or 24), 100))
        with self._lock, self._connect() as conn:
            if peer_id:
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE namespace = ? AND peer_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (namespace, peer_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE namespace = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (namespace, limit),
                ).fetchall()
        rows = list(reversed(rows))
        # Dreaming should consolidate real conversational evidence, not smoke tests,
        # exact-reply verifications, or bridge health probes.
        noise = _noise_regex()
        rows = [r for r in rows if not noise.search(r["content"] or "")]
        # Do not dream from a single one-off request, or from unrelated messages
        # pulled across sessions. Prefer the most recent session/topic cluster with
        # at least two non-noise messages.
        grouped: dict[str, list[sqlite3.Row]] = {}
        order: list[str] = []
        for row in rows:
            key = row["session_id"] or "__workspace__"
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(row)
        dream_rows: list[sqlite3.Row] = []
        for key in reversed(order):
            if len(grouped[key]) >= 2:
                dream_rows = grouped[key]
                break
        created: list[dict] = []
        if dream_rows:
            conclusion_text = _dream_conclusion_text(dream_rows)
            # Avoid creating the exact same dream conclusion repeatedly.
            with self._lock, self._connect() as conn:
                existing = conn.execute(
                    """SELECT id FROM conclusions
                       WHERE namespace = ? AND status != 'deleted' AND content = ? LIMIT 1""",
                    (namespace, conclusion_text),
                ).fetchone()
            if not existing:
                session_id = dream_rows[-1]["session_id"] or ""
                created.append(self.add_conclusion(
                    conclusion_text, namespace=namespace, session_id=session_id, peer_id=peer_id,
                    scope="peer" if peer_id else "workspace", confidence=0.62,
                    metadata={
                        "source": "dream_cycle",
                        "message_count": len(dream_rows),
                        "message_ids": [r["id"] for r in dream_rows[-10:]],
                    },
                ))
        representation = self.build_representation(
            namespace=namespace, peer_id=peer_id, kind="dream_context" if peer_id else "workspace_dream", limit=16
        )
        return {
            "namespace": namespace,
            "peer_id": peer_id,
            "inspected_messages": len(rows),
            "dreamed_messages": len(dream_rows),
            "created_conclusions": len(created),
            "conclusions": created,
            "representation": representation,
        }

    def cleanup_noise(self, namespace: str = _DEFAULT_NAMESPACE) -> dict:
        """Archive/reject obvious smoke/tool fragments before dreaming."""
        namespace = _safe_namespace(namespace)
        noise = _noise_regex()
        ts = _now()
        rejected: list[str] = []
        archived: list[str] = []
        deleted_representations: list[str] = []
        with self._lock, self._connect() as conn:
            for row in conn.execute(
                "SELECT * FROM review_queue WHERE namespace = ? AND status = 'pending'", (namespace,)
            ).fetchall():
                content = row["content"] or ""
                proposed_type = row["proposed_type"] or ""
                if noise.search(content) or (proposed_type == "infrastructure" and len(content) < 80):
                    try:
                        meta = json.loads(row["metadata"] or "{}")
                    except Exception:
                        meta = {}
                    meta["cleanup_reason"] = "auto dream cleanup rejected obvious smoke/tool/task fragment"
                    conn.execute(
                        "UPDATE review_queue SET status='rejected', reviewed_at=?, metadata=? WHERE id=?",
                        (ts, json.dumps(meta, ensure_ascii=False), row["id"]),
                    )
                    rejected.append(row["id"])
            for row in conn.execute(
                "SELECT * FROM conclusions WHERE namespace = ? AND status = 'active'", (namespace,)
            ).fetchall():
                if noise.search(row["content"] or ""):
                    try:
                        meta = json.loads(row["metadata"] or "{}")
                    except Exception:
                        meta = {}
                    meta["cleanup_reason"] = "auto dream cleanup archived noisy conclusion"
                    conn.execute(
                        "UPDATE conclusions SET status='archived', updated_at=?, metadata=? WHERE id=?",
                        (ts, json.dumps(meta, ensure_ascii=False), row["id"]),
                    )
                    conn.execute("DELETE FROM conclusions_fts WHERE id=?", (row["id"],))
                    archived.append(row["id"])
            for row in conn.execute(
                "SELECT * FROM representations WHERE namespace = ?", (namespace,)
            ).fetchall():
                if noise.search(row["content"] or ""):
                    conn.execute("DELETE FROM representations WHERE id=?", (row["id"],))
                    deleted_representations.append(row["id"])
        return {
            "namespace": namespace,
            "rejected": len(rejected),
            "archived": len(archived),
            "deleted_representations": len(deleted_representations),
        }

    def auto_dream_state(self, namespace: str = _DEFAULT_NAMESPACE) -> dict:
        namespace = _safe_namespace(namespace)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT metadata FROM workspaces WHERE namespace=?", (namespace,)).fetchone()
        try:
            return json.loads((row["metadata"] if row else "") or "{}")
        except Exception:
            return {}

    def record_auto_dream(self, namespace: str = _DEFAULT_NAMESPACE, result: Optional[dict] = None) -> None:
        namespace = _safe_namespace(namespace)
        ts = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT id, metadata FROM workspaces WHERE namespace=?", (namespace,)).fetchone()
            try:
                meta = json.loads((row["metadata"] if row else "") or "{}")
            except Exception:
                meta = {}
            meta["last_auto_dream_at"] = ts
            meta["last_auto_dream"] = result or {}
            if row:
                conn.execute("UPDATE workspaces SET updated_at=?, metadata=? WHERE namespace=?", (ts, json.dumps(meta, ensure_ascii=False), namespace))
            else:
                conn.execute(
                    "INSERT INTO workspaces(id, namespace, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
                    (f"ws_{namespace}", namespace, ts, ts, json.dumps(meta, ensure_ascii=False)),
                )

    def self_maintain(self, namespace: str = _DEFAULT_NAMESPACE, assistant_handle: str = "default", limit: int = 48) -> dict:
        """Run the cron-style maintenance locally for this provider's own namespace."""
        namespace = _safe_namespace(namespace)
        assistant_handle = _clean_text(assistant_handle or "default", 120) or "default"
        cleanup = self.cleanup_noise(namespace)
        assistant_peer = self.upsert_peer(assistant_handle, namespace, role="assistant", metadata={"source": "self_maintain"})
        workspace_dream = self.dream_cycle(namespace=namespace, limit=limit)
        peer_dream = self.dream_cycle(namespace=namespace, peer_id=assistant_peer["id"], limit=limit)
        result = {
            "namespace": namespace,
            "assistant_peer_id": assistant_peer["id"],
            "cleanup": cleanup,
            "workspace_dream": workspace_dream,
            "peer_dream": peer_dream,
        }
        self.record_auto_dream(namespace, result)
        return result

    def add_proposal(self, content: str, namespace: str, proposed_type: str, evidence: str,
                     source_session: str = "", confidence: float = 0.5, metadata: Optional[dict] = None) -> dict:
        content = _clean_text(content, 2000)
        if not content:
            raise ValueError("proposal content is required")
        namespace = _safe_namespace(namespace)
        proposed_type = _safe_type(proposed_type)
        # Avoid flooding duplicate pending proposals.
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM review_queue WHERE namespace = ? AND status = 'pending' AND lower(content) = lower(?) LIMIT 1",
                (namespace, content),
            ).fetchone()
            if existing:
                return dict(existing)
            pid = f"prop_{uuid.uuid4().hex[:12]}"
            ts = _now()
            conn.execute(
                """INSERT INTO review_queue(id, namespace, proposed_type, content, evidence, source_session,
                   status, confidence, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (pid, namespace, proposed_type, content, _clean_text(evidence, 4000), source_session, float(confidence), ts,
                 json.dumps(metadata or {}, ensure_ascii=False)),
            )
            row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (pid,)).fetchone()
        return dict(row)

    def list_proposals(self, namespace: str = _DEFAULT_NAMESPACE, status: str = "pending", limit: int = 20) -> List[dict]:
        namespace = _safe_namespace(namespace)
        limit = max(1, min(int(limit or 20), 100))
        if status not in {"pending", "approved", "rejected", "all"}:
            status = "pending"
        with self._lock, self._connect() as conn:
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM review_queue WHERE namespace = ? ORDER BY created_at DESC LIMIT ?", (namespace, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review_queue WHERE namespace = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                    (namespace, status, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def review_proposal(self, proposal_id: str, action: str) -> dict:
        action = (action or "").lower()
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (proposal_id,)).fetchone()
            if not row:
                raise ValueError(f"proposal not found: {proposal_id}")
            status = "approved" if action == "approve" else "rejected"
            conn.execute("UPDATE review_queue SET status = ?, reviewed_at = ? WHERE id = ?", (status, _now(), proposal_id))
        result = dict(row)
        if action == "approve":
            mem = self.add_memory(
                result["content"], namespace=result["namespace"], memory_type=result["proposed_type"],
                source="review_queue", source_session=result.get("source_session") or "",
                confidence=float(result.get("confidence") or 0.6), importance=0.65,
                metadata={"proposal_id": proposal_id},
            )
            conclusion = self.add_conclusion(
                result["content"], namespace=result["namespace"], session_id=result.get("source_session") or "",
                scope="workspace", confidence=float(result.get("confidence") or 0.6),
                metadata={"proposal_id": proposal_id, "source": "review_queue"},
            )
            result["memory"] = mem
            result["conclusion"] = conclusion
        result["status"] = status
        return result

    def counts(self) -> dict:
        with self._lock, self._connect() as conn:
            memories = conn.execute("SELECT namespace, status, count(*) c FROM memories GROUP BY namespace, status").fetchall()
            proposals = conn.execute("SELECT namespace, status, count(*) c FROM review_queue GROUP BY namespace, status").fetchall()
            turns = conn.execute("SELECT namespace, count(*) c FROM turns GROUP BY namespace").fetchall()
            workspaces = conn.execute("SELECT namespace, count(*) c FROM workspaces GROUP BY namespace").fetchall()
            peers = conn.execute("SELECT namespace, count(*) c FROM peers GROUP BY namespace").fetchall()
            sessions = conn.execute("SELECT namespace, count(*) c FROM memory_sessions GROUP BY namespace").fetchall()
            messages = conn.execute("SELECT namespace, count(*) c FROM messages GROUP BY namespace").fetchall()
            conclusions = conn.execute("SELECT namespace, status, count(*) c FROM conclusions GROUP BY namespace, status").fetchall()
            representations = conn.execute("SELECT namespace, kind, count(*) c FROM representations GROUP BY namespace, kind").fetchall()
        return {
            "db_path": str(self.db_path),
            "memories": [dict(r) for r in memories],
            "proposals": [dict(r) for r in proposals],
            "turns": [dict(r) for r in turns],
            "graph": {
                "workspaces": [dict(r) for r in workspaces],
                "peers": [dict(r) for r in peers],
                "sessions": [dict(r) for r in sessions],
                "messages": [dict(r) for r in messages],
                "conclusions": [dict(r) for r in conclusions],
                "representations": [dict(r) for r in representations],
            },
        }


def _extract_proposals_from_messages(messages: Iterable[Dict[str, Any]], namespace: str, session_id: str) -> List[dict]:
    """Heuristic, review-first extraction.  It proposes; it does not persist facts."""
    proposals: List[dict] = []
    seen: set[str] = set()
    patterns = [
        ("preference", re.compile(r"\b(?:i|we)\s+(?:prefer|like|want|need)\s+(.{8,180})", re.I)),
        ("decision", re.compile(r"\b(?:we decided|decision:|decided to)\s+(.{8,180})", re.I)),
        ("fact", re.compile(r"\b(?:remember(?: this)?|please remember)[:\s]+(.{8,220})", re.I)),
        ("infrastructure", re.compile(r"\b(?:host|service|path|port|ip|profile|cron job)\b.{0,180}", re.I)),
    ]
    for msg in messages:
        role = msg.get("role") or ""
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(msg.get("content") or "", 4000)
        if not content:
            continue
        for proposed_type, pat in patterns:
            for m in pat.finditer(content):
                snippet = m.group(1) if m.groups() else m.group(0)
                snippet = _clean_text(snippet, 280).rstrip(" .")
                if len(snippet) < 12:
                    continue
                key = snippet.lower()
                if key in seen:
                    continue
                seen.add(key)
                proposals.append({
                    "content": snippet,
                    "namespace": namespace,
                    "proposed_type": proposed_type,
                    "evidence": f"{role}: {content[:500]}",
                    "source_session": session_id,
                    "confidence": 0.55 if proposed_type != "infrastructure" else 0.45,
                })
    return proposals[:25]


LOCAL_MEMORY_STORE_SCHEMA = {
    "name": "local_memory_store",
    "description": "Store an explicit durable memory in private local Local memory. Use only for stable facts/preferences/decisions the user would expect remembered.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Durable memory content."},
            "namespace": {"type": "string", "description": "Isolation namespace; default default."},
            "memory_type": {"type": "string", "enum": sorted(_ALLOWED_TYPES), "description": "Memory category."},
            "importance": {"type": "number", "description": "0-1 importance; default 0.6."},
            "confidence": {"type": "number", "description": "0-1 confidence; default 0.7."},
        },
        "required": ["content"],
    },
}

LOCAL_MEMORY_SEARCH_SCHEMA = {
    "name": "local_memory_search",
    "description": "Search private local long-term memories and optionally recent turn excerpts in a namespace.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "namespace": {"type": "string", "description": "Namespace; default default."},
            "limit": {"type": "integer", "description": "Max results, default 8."},
            "include_turns": {"type": "boolean", "description": "Also search synced conversation turns."},
        },
        "required": ["query"],
    },
}

LOCAL_MEMORY_CONTEXT_SCHEMA = {
    "name": "local_memory_context",
    "description": "Return a concise context block for the current task from private local memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Current user request/task."},
            "namespace": {"type": "string", "description": "Namespace; default default."},
            "limit": {"type": "integer", "description": "Max memories, default 8."},
        },
        "required": ["query"],
    },
}

LOCAL_MEMORY_REVIEW_SCHEMA = {
    "name": "local_memory_review",
    "description": "Review proposed memories: list pending proposals, add a proposal, approve, or reject. Approved proposals become durable memories.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "propose", "approve", "reject"]},
            "proposal_id": {"type": "string", "description": "Proposal ID for approve/reject."},
            "content": {"type": "string", "description": "Proposal content for propose."},
            "namespace": {"type": "string", "description": "Namespace; default default."},
            "memory_type": {"type": "string", "enum": sorted(_ALLOWED_TYPES), "description": "Proposal category."},
            "limit": {"type": "integer", "description": "List limit; default 20."},
        },
        "required": ["action"],
    },
}

LOCAL_MEMORY_FORGET_SCHEMA = {
    "name": "local_memory_forget",
    "description": "Archive/delete a specific private local memory by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory ID."},
            "mode": {"type": "string", "enum": ["archive", "delete"], "description": "Archive by default; delete removes from active search."},
        },
        "required": ["memory_id"],
    },
}

LOCAL_MEMORY_HONCHO_SCHEMA = {
    "name": "local_memory_graph",
    "description": "Local Graph-aligned primitives over SQLite: upsert peers/sessions, add messages/conclusions, build representations, or return combined context.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["upsert_peer", "upsert_session", "add_message", "add_conclusion", "build_representation", "dream", "cleanup", "self_maintain", "context"]},
            "namespace": {"type": "string", "description": "Workspace namespace; default default."},
            "peer_handle": {"type": "string", "description": "Stable peer handle, e.g. user, assistant, workspace."},
            "peer_id": {"type": "string", "description": "Peer ID for messages/conclusions/representations."},
            "role": {"type": "string", "description": "Peer/message role."},
            "session_id": {"type": "string", "description": "Session identifier."},
            "title": {"type": "string", "description": "Session title."},
            "content": {"type": "string", "description": "Message or conclusion content."},
            "scope": {"type": "string", "enum": ["workspace", "peer", "session", "message"], "description": "Conclusion scope."},
            "confidence": {"type": "number", "description": "Conclusion confidence 0-1."},
            "kind": {"type": "string", "description": "Representation kind, default peer_context."},
            "query": {"type": "string", "description": "Context/search query."},
            "limit": {"type": "integer", "description": "Max results."},
        },
        "required": ["action"],
    },
}


LOCAL_MEMORY_STATUS_SCHEMA = {
    "name": "local_memory_status",
    "description": "Show private local Local memory database path and counts by namespace/status.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


class LocalSQLiteMemoryProvider(MemoryProvider):
    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._store: Optional[_Store] = None
        self._session_id = ""
        self._namespace = _safe_namespace(self._config.get("namespace", _DEFAULT_NAMESPACE))
        self._context_limit = int(self._config.get("context_limit", _MAX_CONTEXT_RESULTS))
        self._sync_turns = str(self._config.get("sync_turns", "true")).lower() not in {"0", "false", "no"}
        self._auto_propose = str(self._config.get("auto_propose", "true")).lower() not in {"0", "false", "no"}
        self._auto_dream = str(self._config.get("auto_dream", "true")).lower() not in {"0", "false", "no"}
        self._auto_dream_interval_seconds = int(self._config.get("auto_dream_interval_seconds", 86400))
        self._auto_dream_limit = int(self._config.get("auto_dream_limit", 48))
        self._assistant_handle = _clean_text(self._config.get("assistant_handle", "default"), 120) or "default"
        self._initialized = False

    @property
    def name(self) -> str:
        return "local_sqlite_memory"

    def is_available(self) -> bool:
        return True

    def get_config_schema(self):
        try:
            from hermes_constants import display_hermes_home  # type: ignore[import-not-found]
            home_display = display_hermes_home()
        except ModuleNotFoundError:
            home_display = "~/.hermes"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": f"{home_display}/local-sqlite-memory/memory.sqlite3"},
            {"key": "namespace", "description": "Default namespace", "default": "default"},
            {"key": "context_limit", "description": "Memories injected before each turn", "default": "8"},
            {"key": "sync_turns", "description": "Store turn excerpts locally", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_propose", "description": "Create review proposals at session end", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_dream", "description": "Run deterministic cleanup/dream maintenance opportunistically at session end", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_dream_interval_seconds", "description": "Minimum seconds between automatic dream maintenance runs", "default": "86400"},
            {"key": "auto_dream_limit", "description": "Recent graph messages inspected by automatic dream maintenance", "default": "48"},
            {"key": "assistant_handle", "description": "Assistant peer handle used for automatic peer dreaming", "default": "default"},
        ]

    def save_config(self, values, hermes_home):
        config_path = Path(hermes_home) / "local_sqlite_memory.json"
        config_path.write_text(json.dumps(values or {}, indent=2), encoding="utf-8")
        # Keep plugin-specific config native, not under plugins.*; activation is still memory.provider.

    def _load_config_file(self, hermes_home: str) -> dict:
        path = Path(hermes_home) / "local_sqlite_memory.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("Failed reading local_sqlite_memory config %s: %s", path, e)
            return {}

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home")
        if not hermes_home:
            from hermes_constants import get_hermes_home
            hermes_home = str(get_hermes_home())
        file_config = self._load_config_file(str(hermes_home))
        merged = {**file_config, **self._config}
        self._namespace = _safe_namespace(merged.get("namespace", _DEFAULT_NAMESPACE))
        self._context_limit = max(1, min(int(merged.get("context_limit", _MAX_CONTEXT_RESULTS)), 20))
        self._sync_turns = str(merged.get("sync_turns", "true")).lower() not in {"0", "false", "no"}
        self._auto_propose = str(merged.get("auto_propose", "true")).lower() not in {"0", "false", "no"}
        self._auto_dream = str(merged.get("auto_dream", "true")).lower() not in {"0", "false", "no"}
        self._auto_dream_interval_seconds = max(0, int(merged.get("auto_dream_interval_seconds", 86400)))
        self._auto_dream_limit = max(1, min(int(merged.get("auto_dream_limit", 48)), 100))
        self._assistant_handle = _clean_text(merged.get("assistant_handle", "default"), 120) or "default"
        db_path = str(merged.get("db_path") or "$HERMES_HOME/local-sqlite-memory/memory.sqlite3")
        db_path = db_path.replace("$HERMES_HOME", str(hermes_home)).replace("${HERMES_HOME}", str(hermes_home))
        self._store = _Store(Path(db_path))
        self._session_id = session_id or ""
        self._initialized = True
        logger.info("Local SQLite memory initialized at %s", db_path)

    def system_prompt_block(self) -> str:
        return (
            "Local SQLite Memory is active: private local SQLite memory with namespaces "
            "default and user-defined workspaces. Use local_memory_search/context before "
            "answering questions that depend on past context. Do not mix namespaces unless explicitly requested."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._store:
            return ""
        memories = self._store.search_memories(query, namespace=self._namespace, limit=self._context_limit)
        if not memories:
            return ""
        lines = ["Relevant Local local memories:"]
        for m in memories[: self._context_limit]:
            lines.append(f"- [{m['id']}; {m['memory_type']}; {m['namespace']}] {m['content']}")
        return "\n".join(lines)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._store or not self._sync_turns:
            return
        sid = session_id or self._session_id
        try:
            self._store.add_turn(user_content, assistant_content, self._namespace, sid, {"provider": self.name})
            self._store.upsert_session(sid, self._namespace, metadata={"provider": self.name, "source": "sync_turn"})
            user_peer = self._store.upsert_peer("user", self._namespace, role="user", metadata={"source": "sync_turn"})
            assistant_peer = self._store.upsert_peer(self._assistant_handle, self._namespace, role="assistant", metadata={"source": "sync_turn"})
            if _clean_text(user_content):
                self._store.add_message(user_content, self._namespace, sid, user_peer["id"], "user", {"source": "sync_turn"})
            if _clean_text(assistant_content):
                self._store.add_message(assistant_content, self._namespace, sid, assistant_peer["id"], "assistant", {"source": "sync_turn"})
        except Exception as e:
            logger.warning("Local SQLite memory sync_turn failed: %s", e)

    def _maybe_auto_dream(self, reason: str = "session_end") -> None:
        if not self._store or not self._auto_dream:
            return
        try:
            state = self._store.auto_dream_state(self._namespace)
            last = _parse_ts(state.get("last_auto_dream_at"))
            now_ts = datetime.now(timezone.utc).timestamp()
            if self._auto_dream_interval_seconds and last and (now_ts - last) < self._auto_dream_interval_seconds:
                return
            result = self._store.self_maintain(
                self._namespace, assistant_handle=self._assistant_handle, limit=self._auto_dream_limit
            )
            result["reason"] = reason
            self._store.record_auto_dream(self._namespace, result)
            logger.info(
                "Local SQLite memory auto dream completed namespace=%s workspace_created=%s peer_created=%s",
                self._namespace,
                result.get("workspace_dream", {}).get("created_conclusions"),
                result.get("peer_dream", {}).get("created_conclusions"),
            )
        except Exception as e:
            logger.debug("Local SQLite memory auto dream skipped/failed: %s", e)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._store:
            return
        if self._auto_propose and messages:
            for prop in _extract_proposals_from_messages(messages, self._namespace, self._session_id):
                try:
                    self._store.add_proposal(**prop)
                except Exception as e:
                    logger.debug("Failed adding Local memory proposal: %s", e)
        self._maybe_auto_dream("session_end")

    def on_memory_write(self, action, target, content, metadata=None):
        if not self._store or action not in {"add", "replace"}:
            return
        namespace = self._namespace
        if target == "user":
            memory_type = "preference"
        else:
            memory_type = "fact"
        try:
            self._store.add_memory(
                content, namespace=namespace, memory_type=memory_type,
                source=f"builtin_memory:{target}:{action}", source_session=self._session_id,
                confidence=0.8, importance=0.75, metadata=metadata or {},
            )
        except Exception as e:
            logger.debug("Failed mirroring built-in memory write to Local local: %s", e)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **kwargs) -> None:
        self._session_id = new_session_id or ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            LOCAL_MEMORY_SEARCH_SCHEMA,
            LOCAL_MEMORY_CONTEXT_SCHEMA,
            LOCAL_MEMORY_STORE_SCHEMA,
            LOCAL_MEMORY_REVIEW_SCHEMA,
            LOCAL_MEMORY_FORGET_SCHEMA,
            LOCAL_MEMORY_HONCHO_SCHEMA,
            LOCAL_MEMORY_STATUS_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._store:
            return tool_error("Local SQLite memory is not initialized")
        try:
            if tool_name == "local_memory_store":
                mem = self._store.add_memory(
                    args.get("content", ""), namespace=args.get("namespace", self._namespace),
                    memory_type=args.get("memory_type", "fact"), source="tool",
                    source_session=self._session_id, confidence=float(args.get("confidence", 0.7)),
                    importance=float(args.get("importance", 0.6)),
                )
                return _json_result(memory=mem)
            if tool_name == "local_memory_search":
                ns = args.get("namespace", self._namespace)
                query = args.get("query", "")
                memories = self._store.search_memories(query, namespace=ns, limit=int(args.get("limit", 8)))
                turns = self._store.search_turns(query, namespace=ns, limit=5) if args.get("include_turns") else []
                return _json_result(memories=memories, turns=turns)
            if tool_name == "local_memory_context":
                ns = args.get("namespace", self._namespace)
                query = args.get("query", "")
                memories = self._store.search_memories(query, namespace=ns, limit=int(args.get("limit", self._context_limit)))
                context = "\n".join(f"- [{m['memory_type']}] {m['content']}" for m in memories)
                return _json_result(context=context, memories=memories)
            if tool_name == "local_memory_review":
                action = (args.get("action") or "list").lower()
                ns = args.get("namespace", self._namespace)
                if action == "list":
                    return _json_result(proposals=self._store.list_proposals(ns, limit=int(args.get("limit", 20))))
                if action == "propose":
                    prop = self._store.add_proposal(
                        args.get("content", ""), namespace=ns,
                        proposed_type=args.get("memory_type", "fact"), evidence="manual proposal via tool",
                        source_session=self._session_id, confidence=0.6,
                    )
                    return _json_result(proposal=prop)
                if action in {"approve", "reject"}:
                    return _json_result(proposal=self._store.review_proposal(args.get("proposal_id", ""), action))
                return tool_error("unknown review action")
            if tool_name == "local_memory_forget":
                status = "deleted" if args.get("mode") == "delete" else "archived"
                mem = self._store.update_memory(args.get("memory_id", ""), status=status)
                return _json_result(memory=mem)
            if tool_name == "local_memory_graph":
                action = (args.get("action") or "context").lower()
                ns = args.get("namespace", self._namespace)
                if action == "upsert_peer":
                    peer = self._store.upsert_peer(
                        args.get("peer_handle", args.get("peer_id", "default")), namespace=ns,
                        role=args.get("role", "user"), metadata={"source": "tool"},
                    )
                    return _json_result(peer=peer)
                if action == "upsert_session":
                    session = self._store.upsert_session(
                        args.get("session_id", self._session_id), namespace=ns,
                        title=args.get("title", ""), metadata={"source": "tool"},
                    )
                    return _json_result(session=session)
                if action == "add_message":
                    message = self._store.add_message(
                        args.get("content", ""), namespace=ns,
                        session_id=args.get("session_id", self._session_id), peer_id=args.get("peer_id", ""),
                        role=args.get("role", "user"), metadata={"source": "tool"},
                    )
                    return _json_result(message=message)
                if action == "add_conclusion":
                    conclusion = self._store.add_conclusion(
                        args.get("content", ""), namespace=ns,
                        session_id=args.get("session_id", self._session_id), peer_id=args.get("peer_id", ""),
                        scope=args.get("scope", "workspace"), confidence=float(args.get("confidence", 0.7)),
                        metadata={"source": "tool"},
                    )
                    return _json_result(conclusion=conclusion)
                if action == "build_representation":
                    representation = self._store.build_representation(
                        namespace=ns, peer_id=args.get("peer_id", ""), kind=args.get("kind", "peer_context"),
                        limit=int(args.get("limit", 12)),
                    )
                    return _json_result(representation=representation)
                if action == "dream":
                    dream = self._store.dream_cycle(
                        namespace=ns, peer_id=args.get("peer_id", ""), limit=int(args.get("limit", 24))
                    )
                    return _json_result(dream=dream)
                if action == "cleanup":
                    return _json_result(cleanup=self._store.cleanup_noise(ns))
                if action == "self_maintain":
                    return _json_result(maintenance=self._store.self_maintain(
                        ns, assistant_handle=args.get("peer_handle", self._assistant_handle), limit=int(args.get("limit", self._auto_dream_limit))
                    ))
                if action == "context":
                    return _json_result(context=self._store.graph_context(
                        args.get("query", args.get("content", "")), namespace=ns, limit=int(args.get("limit", 8))
                    ))
                return tool_error("unknown graph action")
            if tool_name == "local_memory_status":
                return _json_result(status=self._store.counts())
            return tool_error(f"Unknown Local SQLite memory tool: {tool_name}")
        except Exception as e:
            return tool_error(str(e))


def register(ctx) -> None:
    ctx.register_memory_provider(LocalSQLiteMemoryProvider())
