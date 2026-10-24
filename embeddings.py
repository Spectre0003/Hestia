"""
Hestia — embeddings (Stage 9 / v0.9)

Turns text into vectors so past conversations and stored facts can be
found by meaning rather than by shared words. The keyword matching this
replaces could never connect "cybersecurity student" to "what is my job
status"; nothing about tags or spelling fixes that, only this.

The model runs locally through Ollama, like everything else here — one
more small model (~280MB) beside the chat model, not a cloud service.
Vectors are stored normalised, so comparing two is a plain dot product.

Nothing in here raises. Indexing is a background nicety: if Ollama is
busy or the embedding model is missing, the caller gets None and carries
on, and the text stays unindexed until something tries again.

Changing EMBED_MODEL invalidates every stored vector — different models
put text in different spaces, and comparing across them is meaningless.
Vectors of a different length are skipped automatically, but a new model
with the same width would compare as nonsense, so re-index after a
change: python scripts/index_history.py --reset
"""

import math
import struct

import ollama

EMBED_MODEL = "nomic-embed-text"

# Ollama takes a list and embeds it in one request, which matters when
# indexing a few hundred messages at once.
BATCH_SIZE = 32

# Let the embedding model fall out of VRAM shortly after indexing: on an
# 8GB card it shares with the chat model, and indexing is occasional.
KEEP_ALIVE = "30s"

# Below this, a message is "ok" or "y" — no meaning worth indexing, and
# it would only dilute search results.
MIN_INDEX_CHARS = 25


def embed_many(texts):
    """
    Vectors for a list of texts, in the same order. Returns None if the
    whole call failed — never raises, so indexing can't break a session.
    """
    texts = [t for t in texts]
    if not texts:
        return []
    try:
        response = ollama.embed(model=EMBED_MODEL, input=texts, keep_alive=KEEP_ALIVE)
        vectors = list(response.embeddings)
    except Exception:
        return None
    if len(vectors) != len(texts):
        return None
    return vectors


def embed_one(text):
    """Vector for a single text, or None."""
    vectors = embed_many([text])
    if not vectors:
        return None
    return vectors[0]


def pack(vector):
    """
    A vector as float32 bytes for a BLOB column, normalised to unit
    length first so similarity() is a dot product. None if the vector is
    empty or has no length (which a real embedding never does).
    """
    if not vector:
        return None
    length = math.sqrt(sum(value * value for value in vector))
    if not length:
        return None
    return struct.pack(f"{len(vector)}f", *(value / length for value in vector))


def unpack(blob):
    """A stored BLOB back into a tuple of floats, or None if unreadable."""
    if not blob or len(blob) % 4:
        return None
    try:
        return struct.unpack(f"{len(blob) // 4}f", blob)
    except struct.error:
        return None


def similarity(a, b):
    """
    How alike two stored vectors are, from -1 to 1. Both must already be
    normalised (pack does that), so this is a dot product. Vectors of
    different lengths come from different models and aren't comparable,
    so they score 0 rather than raising.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _pending_messages(conn, session_id=None):
    """
    Messages worth indexing that have no vector yet.

    Only the user's own messages, for the same reason extraction ignores
    the assistant's reply: what the user said is a record, what the model
    said is sometimes an invention. Indexing replies made that invention
    searchable — asked about a favourite food it had never been told, the
    model answered "pizza", and that reply ("I remember you saying your
    favorite food is pizza") would then have been the top match for the
    next question about food, presented as evidence from history.

    Nothing useful is lost: recall returns the messages either side of a
    match, so the reply to a matching question is still shown. It just
    cannot be the thing that matched, so a fabrication can never surface
    itself.

    The length floor also drops "ok" and "yes", which carry no meaning
    worth searching for.
    """
    sql = ("SELECT id, content FROM messages WHERE embedding IS NULL "
           "AND role = 'user' AND length(content) >= ?")
    params = [MIN_INDEX_CHARS]
    if session_id is not None:
        sql += " AND session_id = ?"
        params.append(session_id)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def pending_count(conn, session_id=None):
    """
    How many items are waiting to be indexed — matching what
    index_pending would actually do, memories included when no session
    is named, so a caller can report the count honestly.
    """
    waiting = len(_pending_messages(conn, session_id))
    if session_id is None:
        waiting += conn.execute(
            "SELECT COUNT(*) FROM memories WHERE embedding IS NULL").fetchone()[0]
    return waiting


def index_pending(conn, session_id=None, progress=None):
    """
    Embed whatever has no vector yet and store it. With a session_id,
    only that session's messages; without one, every session plus the
    memories. Returns (indexed, failed).

    Each batch is committed as it finishes, so an interruption — Ctrl+C,
    Ollama restarting, the machine sleeping — keeps the work already
    done and the next run picks up where this one stopped.
    """
    jobs = [("messages", row["id"], row["content"])
            for row in _pending_messages(conn, session_id)]
    if session_id is None:
        jobs += [("memories", row["id"], row["content"]) for row in conn.execute(
            "SELECT id, content FROM memories WHERE embedding IS NULL").fetchall()]

    indexed = failed = 0
    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start:start + BATCH_SIZE]
        vectors = embed_many([text for _, _, text in batch])
        if vectors is None:  # the whole call failed; leave them for next time
            failed += len(batch)
        else:
            for (table, row_id, _), vector in zip(batch, vectors):
                blob = pack(vector)
                if blob is None:
                    failed += 1
                    continue
                # table is one of two literals above, never outside input.
                conn.execute(f"UPDATE {table} SET embedding = ? WHERE id = ?", (blob, row_id))
                indexed += 1
            conn.commit()
        if progress:
            progress(indexed, failed, len(jobs))
    return indexed, failed


def rank(query_vector, rows, limit, minimum=0.0):
    """
    Rows scored against a query vector, best first. Each row needs an
    "embedding" column; rows without one, or from another model, drop
    out. Returns a list of (score, row).

    The query is normalised here rather than trusted to arrive that way.
    Stored vectors are unit length because pack() makes them so, but a
    vector straight from the model is whatever the model returned, and
    mixing the two scales every score by the query's magnitude — which
    would quietly invalidate the floors that callers compare against.

    Plain Python on purpose: a few hundred vectors compare in
    milliseconds. Past roughly ten thousand this is where numpy earns
    its place — a vector database still wouldn't.
    """
    query_vector = unpack(pack(query_vector))
    if query_vector is None:
        return []
    scored = []
    for row in rows:
        vector = unpack(row["embedding"])
        if vector is None:
            continue
        score = similarity(query_vector, vector)
        if score >= minimum:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]
