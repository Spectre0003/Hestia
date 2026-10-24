"""
Hestia — index past conversations for semantic recall (Stage 9 / v0.9)

Embeds everything that has no vector yet: old messages from before
Stage 9, any session that wasn't indexed when it ended, and stored
memories. Safe to run any time — already-indexed rows are skipped, and
it commits as it goes, so stopping it halfway loses nothing.

    python scripts/index_history.py            index what's missing
    python scripts/index_history.py --reset    re-index everything

Use --reset after changing EMBED_MODEL: vectors from different models
aren't comparable, so old ones would score as noise.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embeddings  # noqa: E402
import storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Index past conversations for recall.")
    parser.add_argument("--reset", action="store_true",
                        help="Clear every stored vector and index from scratch.")
    args = parser.parse_args()

    conn = storage.get_connection()

    # Checked before any work: the usual reason this fails is the model
    # not being pulled, and finding that out after a long run is unkind.
    if embeddings.embed_one("hello") is None:
        print(f"Couldn't reach the embedding model '{embeddings.EMBED_MODEL}'.")
        print("Is Ollama running, and has the model been pulled?")
        print(f"    ollama pull {embeddings.EMBED_MODEL}")
        return 1

    if args.reset:
        conn.execute("UPDATE messages SET embedding = NULL")
        conn.execute("UPDATE memories SET embedding = NULL")
        conn.commit()
        print("Cleared every stored vector.")

    def progress(indexed, failed, total):
        note = f", {failed} failed" if failed else ""
        print(f"\r  {indexed + failed}/{total} ({indexed} indexed{note})", end="", flush=True)

    try:
        indexed, failed = embeddings.index_pending(conn, progress=progress)
    except KeyboardInterrupt:
        print("\nStopped. What finished is saved; run again to continue.")
        return 1
    finally:
        conn.close()

    if indexed or failed:
        print()
    if not indexed and not failed:
        print("Nothing to index — everything is already up to date.")
    elif failed:
        print(f"Indexed {indexed}; {failed} failed. Run again to retry those.")
    else:
        print(f"Indexed {indexed}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
