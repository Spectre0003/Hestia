"""
Hestia — recall tool server (Stage 9 / v0.9)

Searches past conversations by meaning. Everything said in earlier
sessions is embedded when that session ends (see chat.close_session);
this finds the closest matches to a question and hands back the
surrounding exchange so the answer has context rather than one stray
line.

Read-only, by design and by connection: this server opens the database
in mode=ro with a busy timeout, because chat.py holds a write
connection to the same file at the same time. Nothing here writes.

Recalled conversation is the most private thing Hestia holds, so recall
is registered in tools.LOCAL_DATA_TOOLS: once it has run in a turn, the
network tools are refused for the rest of that turn and none of it can
leave in a search query or a URL.

As with the other servers, whether this may run at all is decided in
tools.py (TOOL_TIERS), never here.
"""

import os
import sqlite3
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import embeddings  # noqa: E402

server = MCPServer("memory_tools", log_level="WARNING")

DB_PATH = os.path.join(_ROOT, "data", "hestia.db")

# Seconds to wait if chat.py happens to be writing. Without this a recall
# during a database write fails outright instead of waiting a moment.
BUSY_TIMEOUT_SECONDS = 5

MAX_MATCHES = 3
CONTEXT_MESSAGES = 1  # how many messages either side of a match to show
# Two hits this close together describe the same moment; showing both
# just repeats the same window twice.
NEAR_DUPLICATE_GAP = 2
# Measured against every pair of this user's indexed messages: the median
# arbitrary pair already scores 0.42, the 95th percentile 0.71. Embeddings
# weigh sentence shape heavily, so "my favourite food" matches "my favourite
# colour" at 0.74 while being about something else entirely — which is how
# an earlier floor of 0.35 (admitting 84% of all pairs) produced a confident
# answer about colour to a question about food. 0.80 admits ~3% of arbitrary
# pairs. When nothing clears it the best score is reported, so the floor can
# be re-judged from evidence rather than guessed at again.
MIN_SCORE = 0.80

# Above the floor but not clearly on-topic: worth returning, worth flagging.
STRONG_MATCH = 0.88


def _connect():
    try:
        conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS
        )
    except sqlite3.OperationalError:
        raise ToolError("There's no conversation history to search yet.")
    conn.row_factory = sqlite3.Row
    return conn


def _window(conn, session_id, message_id):
    """The matching message plus the one either side, oldest first."""
    rows = conn.execute(
        """
        SELECT id, role, content FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
          AND length(content) > 0
          AND id BETWEEN ? AND ?
        ORDER BY id
        """,
        (session_id, message_id - CONTEXT_MESSAGES * 3, message_id + CONTEXT_MESSAGES * 3),
    ).fetchall()
    # Widened above because tool rows sit between real messages; trim back
    # to CONTEXT_MESSAGES on either side of the match itself.
    middle = next((i for i, row in enumerate(rows) if row["id"] == message_id), 0)
    start = max(0, middle - CONTEXT_MESSAGES)
    return rows[start:middle + CONTEXT_MESSAGES + 1], message_id


@server.tool()
def recall(query: str) -> str:
    """Search past conversations with this user for something discussed before, by meaning rather than exact words. Use it when they refer to an earlier conversation, ask what was decided or said before, or ask something you would only know from a previous session. It does not cover the current conversation, which you can already see. Results are the closest matches found, which is not the same as relevant: if they do not actually answer the question, say you do not have it rather than piecing an answer together from them."""
    query = (query or "").strip()
    if not query:
        raise ToolError("A recall needs something to search for.")

    vector = embeddings.embed_one(query)
    if vector is None:
        raise ToolError(
            f"Couldn't reach the embedding model '{embeddings.EMBED_MODEL}', so past "
            "conversations can't be searched right now."
        )

    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, timestamp, embedding
            FROM messages WHERE embedding IS NOT NULL
            """
        ).fetchall()
        if not rows:
            return ("No past conversations have been indexed yet, so there's nothing to "
                    "search. They're indexed when a session ends.")

        scored = embeddings.rank(vector, rows, limit=len(rows))
        if not scored or scored[0][0] < MIN_SCORE:
            best = f" (closest was {scored[0][0]:.2f})" if scored else ""
            return f"Nothing in past conversations looks related to '{query}'{best}."

        chosen = []
        for score, row in scored:
            if score < MIN_SCORE:
                break
            if any(row["session_id"] == other["session_id"]
                   and abs(row["id"] - other["id"]) <= NEAR_DUPLICATE_GAP
                   for _, other in chosen):
                continue
            chosen.append((score, row))
            if len(chosen) == MAX_MATCHES:
                break

        parts = [f"From past conversations, closest first, for '{query}':"]
        if chosen[0][0] < STRONG_MATCH:
            parts.append("(These are the closest matches found, but none is a strong "
                         "match — they may not be about what was asked.)")
        for score, row in chosen:
            window, match_id = _window(conn, row["session_id"], row["id"])
            when = (row["timestamp"] or "")[:10]
            parts.append(f"\n[{when}, session {row['session_id']}, similarity {score:.2f}]")
            for message in window:
                who = "User" if message["role"] == "user" else "You"
                mark = ">" if message["id"] == match_id else " "
                text = " ".join((message["content"] or "").split())
                parts.append(f"  {mark} {who}: {text}")
        return "\n".join(parts)
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()  # stdio transport by default
