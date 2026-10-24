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


def rank(query_vector, rows, limit, minimum=0.0):
    """
    Rows scored against a query vector, best first. Each row needs an
    "embedding" column; rows without one, or from another model, drop
    out. Returns a list of (score, row).

    Plain Python on purpose: a few hundred vectors compare in
    milliseconds. Past roughly ten thousand this is where numpy earns
    its place — a vector database still wouldn't.
    """
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
