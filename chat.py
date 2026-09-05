"""
Hestia — Stage 3 (v0.3): Conversation persistence

A command-line chat loop that talks to a local model running in Ollama,
with a persona loaded from personality.yaml and conversation history
persisted to SQLite via storage.py. Default behavior is a fresh session
every launch; pass --resume to continue the most recent session instead.
The in-chat `new` command starts a fresh session mid-run without
restarting. No tools yet — that's Stage 5.
"""

import sys
import argparse
import yaml
from ollama import chat

import storage

MODEL_NAME = "qwen2.5:7b"
PERSONALITY_PATH = "personality.yaml"


def load_personality(path=PERSONALITY_PATH):
    """
    Load and parse personality.yaml. Fails loudly rather than silently —
    running without a personality isn't a degraded mode worth allowing
    quietly, since the whole point of this stage is that it's always on.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[Error: '{path}' not found. Hestia needs a personality file to start.]")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[Error: '{path}' is not valid YAML: {e}]")
        sys.exit(1)

    if not isinstance(data, dict) or not data.get("name"):
        print(f"[Error: '{path}' is missing required fields (at least 'name').]")
        sys.exit(1)

    return data


def build_system_prompt(persona):
    """
    Turn the parsed personality dict into a single system-prompt string.
    Order roughly follows: who she is, then how she behaves, then the
    hard boundaries last, so the model sees identity before rules.
    """
    lines = [
        f"You are {persona['name']}.",
        persona.get("essence", "").strip(),
    ]

    traits = persona.get("traits")
    if traits:
        lines.append("Core traits:")
        lines.extend(f"- {t}" for t in traits)

    # Freeform *_style / values fields, in a fixed, readable order.
    style_fields = [
        ("values", "Values"),
        ("curiosity_style", "Curiosity"),
        ("humor_style", "Humor"),
        ("empathy_style", "Empathy"),
        ("pacing_style", "Pacing"),
        ("collaboration_style", "Collaboration"),
        ("knowledge_style", "Knowledge"),
        ("disagreement_style", "Disagreement"),
        ("relationship_to_user", "Relationship to the user"),
        ("speech_style", "Speech style"),
        ("formality_range", "Formality"),
    ]
    for key, label in style_fields:
        value = persona.get(key)
        if value:
            lines.append(f"{label}: {value.strip()}")

    boundaries = persona.get("boundaries")
    if boundaries:
        lines.append("Boundaries:")
        for key, value in boundaries.items():
            if value:
                lines.append(f"- {key.replace('_', ' ')}: {value.strip()}")

    return "\n".join(line for line in lines if line)


def start_history(persona, conn, session_id):
    """
    Build a fresh in-memory history for a session: the system prompt,
    followed by that session's recent messages loaded back from SQLite
    (empty for a brand-new session).
    """
    system_prompt = build_system_prompt(persona)
    history = [{"role": "system", "content": system_prompt}]
    history.extend(storage.load_recent_messages(conn, session_id))
    return history


def main():
    parser = argparse.ArgumentParser(description="Hestia chat interface")
    parser.add_argument(
        "--resume", action="store_true",
        help="Continue the most recent session instead of starting a new one",
    )
    args = parser.parse_args()

    persona = load_personality()
    conn = storage.get_connection()

    if args.resume:
        session_id = storage.get_last_session_id(conn)
        if session_id is None:
            print("[No previous session found. Starting a new one.]\n")
            session_id = storage.start_new_session(conn)
    else:
        session_id = storage.start_new_session(conn)

    history = start_history(persona, conn, session_id)

    print(f"{persona['name']} v0.3 — talking to {MODEL_NAME}.")
    print("Type 'exit' or 'quit' to leave, 'new' to start a fresh session.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            storage.end_session(conn, session_id)
            conn.close()
            break

        if user_input.lower() in ("exit", "quit"):
            print("Exiting.")
            storage.end_session(conn, session_id)
            conn.close()
            break

        if user_input.lower() == "new":
            storage.end_session(conn, session_id)
            session_id = storage.start_new_session(conn)
            history = start_history(persona, conn, session_id)
            print("[Started a new session.]\n")
            continue

        if not user_input:
            continue  # skip empty submissions rather than sending them to the model

        history.append({"role": "user", "content": user_input})

        print(f"{persona['name']}: ", end="", flush=True)
        assistant_reply = ""

        try:
            stream = chat(model=MODEL_NAME, messages=history, stream=True)
            for chunk in stream:
                token = chunk["message"]["content"]
                print(token, end="", flush=True)
                assistant_reply += token
        except Exception as e:
            print(f"\n[Error talking to Ollama: {e}]")
            print(f"Is Ollama running? Try 'ollama run {MODEL_NAME}' in another terminal to check.")
            history.pop()  # drop the user message — no reply came back for it
            continue

        print("\n")
        history.append({"role": "assistant", "content": assistant_reply})

        # Only log once both sides of the exchange succeeded — an
        # unanswered user message shouldn't end up persisted.
        storage.log_message(conn, session_id, "user", user_input)
        storage.log_message(conn, session_id, "assistant", assistant_reply)


if __name__ == "__main__":
    main()