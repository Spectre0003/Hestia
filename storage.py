"""
Hestia — storage layer (Stage 3 / v0.3)

Handles SQLite persistence for conversations. Default behavior is a
fresh session every launch; passing --resume at the command line (see
chat.py) continues the most recently created session instead. This
module knows nothing about personality or the model — it only reads
and writes rows.
"""

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
    schema already exists.
    """
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
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
    {"role": ..., "content": ...} dicts ready to drop into `history`.
    """
    rows = conn.execute(
        """
        SELECT role, content FROM (
            SELECT role, content, id FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
        """,
        (session_id, limit),
    ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


def log_message(conn, session_id, role, content):
    """Persist a single message. Called only after a successful exchange."""
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, _now()),
    )
    conn.commit()