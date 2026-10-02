"""
Hestia — storage layer (Stage 3-5 / v0.3-0.5)

Handles SQLite persistence for conversations (sessions/messages),
long-term memory (memories) — facts about the user that persist across
sessions independent of any single conversation — and the tool call log
(tool_calls), one row per tool request the model makes. This module
knows nothing about personality or the model — it only reads and writes
rows.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "hestia.db")

# How many past messages to reload into context when --resume pulls in
# an existing session (or when the in-chat `new` command starts one).
# 20 exchanges = 40 messages. Caps prompt size; doesn't affect what's
# stored — the full session history always stays in the database.
HISTORY_LOAD_LIMIT = 40


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    """
    Open (and if needed, create) the database and its tables. Safe to
    call every startup — CREATE TABLE IF NOT EXISTS is a no-op once the
    schema already exists. Rows come back as sqlite3.Row, which supports
    both index and key access — existing tuple-unpacking code elsewhere
    keeps working unchanged.
    """
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
    """)
    # tool_calls: JSON list, on an assistant message that asked for tools.
    # tool_name:  which tool, on a role='tool' message.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            tool_calls TEXT,
            tool_name TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    # Databases from before Stage 5 have `messages` without the two tool
    # columns; CREATE TABLE IF NOT EXISTS won't touch an existing table,
    # so add them here. Existing rows just get NULL for both.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    for column in ("tool_calls", "tool_name"):
        if column not in existing:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            content TEXT NOT NULL,
            tags TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # server:   which MCP server offered the tool (NULL if none did).
    # decision: auto / approved / denied / blocked — whether it was allowed to run.
    # status:   ok / error / skipped — what happened when it did (or didn't).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            server TEXT,
            tool_name TEXT NOT NULL,
            arguments TEXT NOT NULL,
            tier INTEGER NOT NULL,
            decision TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            result_preview TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    conn.commit()
    return conn


def get_last_session_id(conn):
    """
    Return the id of the most recently created session (regardless of
    whether it was cleanly ended), or None if no session has ever been
    created — i.e. this is the very first run ever. Used only by
    --resume; normal startup always creates a new session instead.
    """
    row = conn.execute(
        "SELECT id FROM sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def start_new_session(conn):
    """Insert a new session row and return its id."""
    cursor = conn.execute(
        "INSERT INTO sessions (started_at, ended_at) VALUES (?, NULL)",
        (_now(),),
    )
    conn.commit()
    return cursor.lastrowid


def end_session(conn, session_id):
    """Mark a session as cleanly ended."""
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE id = ?",
        (_now(), session_id),
    )
    conn.commit()


def load_recent_messages(conn, session_id, limit=HISTORY_LOAD_LIMIT):
    """
    Return the last `limit` messages for a session, oldest first, as
    message dicts ready to drop into `history` — including tool_calls /
    tool_name on the messages that have them.
    """
    rows = conn.execute(
        """
        SELECT role, content, tool_calls, tool_name FROM (
            SELECT role, content, tool_calls, tool_name, id FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
        """,
        (session_id, limit),
    ).fetchall()

    messages = []
    for row in rows:
        message = {"role": row["role"], "content": row["content"]}
        if row["tool_calls"]:
            message["tool_calls"] = json.loads(row["tool_calls"])
        if row["tool_name"]:
            message["tool_name"] = row["tool_name"]
        messages.append(message)

    # The limit can cut an exchange in half — e.g. keep a tool result but
    # drop the call that produced it. Start at a user message instead.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def log_message(conn, session_id, role, content, tool_calls=None, tool_name=None):
    """Persist a single message. Called only after a successful exchange."""
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, timestamp, tool_calls, tool_name)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, role, content, _now(),
         json.dumps(tool_calls) if tool_calls else None, tool_name),
    )
    conn.commit()


def upsert_memory(conn, content, tags, source, key=None):
    """
    Store a long-term memory. If `key` is given and a memory with that
    key already exists, update it in place instead of creating a
    duplicate — this is what lets "favorite color: red" become
    "favorite color: blue" later instead of both floating around and
    contradicting each other. Facts without a key (most auto-captured
    ones) always insert as new rows.
    """
    now = _now()
    tags_str = ", ".join(tags) if isinstance(tags, (list, tuple)) else str(tags)

    if key:
        existing = conn.execute(
            "SELECT id FROM memories WHERE key = ?", (key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE memories SET content = ?, tags = ?, source = ?, updated_at = ? WHERE key = ?",
                (content, tags_str, source, now, key),
            )
            conn.commit()
            return existing["id"]

    cursor = conn.execute(
        "INSERT INTO memories (key, content, tags, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key, content, tags_str, source, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def get_all_memories(conn):
    """Return every stored memory, for tag-matching against a message."""
    return conn.execute(
        "SELECT id, key, content, tags, source, created_at, updated_at FROM memories"
    ).fetchall()


def get_memory_by_id(conn, memory_id):
    return conn.execute(
        "SELECT id, key, content, tags, source, created_at, updated_at FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()


def delete_memory_by_id(conn, memory_id):
    """Delete one memory by id. Returns True if a row was actually deleted."""
    cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_memory_by_key(conn, key):
    """Delete one memory by its canonical key. Returns True if found and deleted."""
    cursor = conn.execute("DELETE FROM memories WHERE key = ?", (key,))
    conn.commit()
    return cursor.rowcount > 0


def clear_all_memories(conn):
    """Delete every stored memory. Returns the number of rows removed."""
    cursor = conn.execute("DELETE FROM memories")
    conn.commit()
    return cursor.rowcount


# How much of a tool's result to keep in the log. Enough to see what
# came back; the full result only ever lives in that turn's API call.
RESULT_PREVIEW_CHARS = 500


def log_tool_call(conn, session_id, server, tool_name, arguments, tier, decision, status, duration_ms, result):
    """
    Record one tool request — including ones that were blocked or denied
    and never ran. Written once per request, after the outcome is known.
    """
    conn.execute(
        """
        INSERT INTO tool_calls (session_id, server, tool_name, arguments, tier, decision,
                                status, duration_ms, result_preview, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, server, tool_name, json.dumps(arguments), tier, decision,
         status, duration_ms, (result or "")[:RESULT_PREVIEW_CHARS], _now()),
    )
    conn.commit()