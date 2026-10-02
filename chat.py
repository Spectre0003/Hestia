"""
Hestia — Stage 5 (v0.5): Tool calling

A command-line chat loop that talks to a local model running in Ollama,
with a persona loaded from personality.yaml, conversation history
persisted to SQLite via storage.py, and long-term memory via memory.py.
Memory has two capture paths (a 'remember ...' command, and automatic
extraction after every exchange) and tag-based retrieval that injects
relevant facts into a given call without polluting saved history.

The model can now ask for tools, served by MCP servers that tools.py
starts at launch. Each turn loops: call the model, run whatever tools
it asks for (subject to permission tiers), hand back the results, call
it again — until it answers in plain text. History keeps each tool
call, but not its full result (see tools.RESULT_NOT_KEPT).
"""

import sys
import json
import argparse
import functools
import yaml
from ollama import chat

import storage
import memory
import tools

MODEL_NAME = "qwen2.5:7b"
PERSONALITY_PATH = "personality.yaml"

# How much of each argument the tool-approval prompt shows.
CONFIRM_PREVIEW_CHARS = 300


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

    Order matters: identity first, then character description, then the
    hard boundaries, then short imperative directives LAST. Models weight
    the end of a long instruction block more heavily, so the concrete
    "do this, not that" rules go there rather than getting buried in the
    middle of abstract character prose.

    Adding a new field needs one line in `list_fields` (for a YAML list)
    or `style_fields` (for a freeform block of text) — no other code
    changes. Editing an existing field needs no code changes at all.
    """
    lines = [
        f"You are {persona['name']}.",
        persona.get("essence", "").strip(),
    ]

    def add_list(key, label):
        values = persona.get(key)
        if values:
            lines.append(f"{label}:")
            lines.extend(f"- {v}" for v in values)

    add_list("traits", "Core traits")

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
        ("memory_style", "Using what you remember"),
        ("answer_shape", "Shape of an answer"),
        ("speech_style", "Speech style"),
        ("formality_range", "Formality"),
    ]
    for key, label in style_fields:
        value = persona.get(key)
        if value:
            lines.append(f"{label}: {value.strip()}")

    add_list("avoids", "Never do these")

    boundaries = persona.get("boundaries")
    if boundaries:
        lines.append("Boundaries:")
        for key, value in boundaries.items():
            if value:
                lines.append(f"- {key.replace('_', ' ')}: {value.strip()}")

    # Last on purpose — recency weighting.
    add_list("directives", "Above all, follow these")

    return "\n".join(line for line in lines if line)


def start_history(persona, conn, session_id):
    """
    Build a fresh in-memory history for a session: the system prompt,
    followed by that session's recent messages loaded back from SQLite
    (empty for a brand-new session).
    """
    system_prompt = build_system_prompt(persona) + "\n\n" + tools.TOOL_GUIDANCE
    history = [{"role": "system", "content": system_prompt}]
    history.extend(storage.load_recent_messages(conn, session_id))
    return history


def stream_reply(messages, offer_tools=True):
    """
    Make one model call, streaming any text straight to the terminal.
    Returns (text, tool_calls) — tool_calls is empty when the model
    answered in plain text rather than asking for a tool.
    """
    text = ""
    tool_calls = []
    schemas = tools.tool_schemas() if offer_tools else []
    stream = chat(
        model=MODEL_NAME,
        messages=messages,
        tools=schemas or None,
        stream=True,
    )
    for chunk in stream:
        token = chunk.message.content or ""
        print(token, end="", flush=True)
        text += token
        if chunk.message.tool_calls:
            tool_calls.extend(chunk.message.tool_calls)
    return text, tool_calls


def confirm_tool(persona_name, tool_name, arguments):
    """
    Ask the user before a TIER_WRITE tool runs. Anything other than an
    explicit 'y'/'yes' — including Ctrl+C — counts as no. Each argument
    is shown on its own line; long values are cut short, with a count
    of what's hidden, so a big file write can't flood the terminal.
    """
    print(f"\n[{persona_name} wants to run '{tool_name}':")
    for key, value in arguments.items():
        shown = json.dumps(value, ensure_ascii=False)
        if len(shown) > CONFIRM_PREVIEW_CHARS:
            hidden = len(shown) - CONFIRM_PREVIEW_CHARS
            shown = f"{shown[:CONFIRM_PREVIEW_CHARS]}... ({hidden} more characters)"
        print(f"    {key}: {shown}")
    print(" Allow? (y/N)]")
    try:
        answer = input("You: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    print(f"{persona_name}: ", end="", flush=True)
    return answer in ("y", "yes")


def run_turn(conn, session_id, call_messages, confirm):
    """
    Get the model's reply to one user message, running any tools it asks
    for along the way, until it answers in plain text or runs out of
    rounds (tools.MAX_TOOL_ROUNDS).

    Full tool results only ever go into `call_messages`, for this turn.
    What's returned for history keeps each tool call but swaps its
    result for tools.RESULT_NOT_KEPT — see there for why.

    Returns (reply_text, tool_turns): tool_turns is the history-ready
    tool messages, empty if no tool was used.
    """
    reply_parts = []
    tool_turns = []

    for _ in range(tools.MAX_TOOL_ROUNDS):
        text, tool_calls = stream_reply(call_messages)
        reply_parts.append(text)
        if not tool_calls:
            break

        calls = [
            {"function": {"name": call.function.name, "arguments": dict(call.function.arguments or {})}}
            for call in tool_calls
        ]
        call_messages.append({"role": "assistant", "content": text, "tool_calls": calls})
        # The round's text is already part of the final reply, so it isn't repeated here.
        tool_turns.append({"role": "assistant", "content": "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            print(f"[tool: {name}] ", end="", flush=True)
            result = tools.execute(conn, session_id, name, call["function"]["arguments"], confirm)
            call_messages.append({"role": "tool", "tool_name": name, "content": result})
            tool_turns.append({"role": "tool", "tool_name": name, "content": tools.RESULT_NOT_KEPT})
    else:
        # Out of rounds — one last call with no tools offered, so the
        # model has to answer in words instead of asking for another.
        text, _ = stream_reply(call_messages, offer_tools=False)
        reply_parts.append(text)

    reply = " ".join(part.strip() for part in reply_parts if part.strip())
    return reply, tool_turns


def main():
    parser = argparse.ArgumentParser(description="Hestia chat interface")
    parser.add_argument(
        "--resume", action="store_true",
        help="Continue the most recent session instead of starting a new one",
    )
    args = parser.parse_args()

    persona = load_personality()
    conn = storage.get_connection()

    # A tool server that won't start costs its tools, not the chat.
    for problem in tools.start():
        print(f"[Tool server problem — {problem}. Its tools are unavailable this run.]")

    if args.resume:
        session_id = storage.get_last_session_id(conn)
        if session_id is None:
            print("[No previous session found. Starting a new one.]\n")
            session_id = storage.start_new_session(conn)
    else:
        session_id = storage.start_new_session(conn)

    history = start_history(persona, conn, session_id)
    ask_tool_permission = functools.partial(confirm_tool, persona["name"])

    print(f"{persona['name']} v0.5 — talking to {MODEL_NAME}.")
    print(f"Tools: {', '.join(tools.available_tools()) or 'none'}")
    print("Commands: 'exit'/'quit' to leave, 'new' for a fresh session,")
    print("          'remember ...' to save a fact, 'forget ...' to remove one, 'memories' to list what's stored.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            storage.end_session(conn, session_id)
            conn.close()
            tools.stop()
            break

        if user_input.lower() in ("exit", "quit"):
            print("Exiting.")
            storage.end_session(conn, session_id)
            conn.close()
            tools.stop()
            break

        if user_input.lower() == "new":
            storage.end_session(conn, session_id)
            session_id = storage.start_new_session(conn)
            history = start_history(persona, conn, session_id)
            print("[Started a new session.]\n")
            continue

        if user_input.lower() == "memories":
            print(memory.format_memory_list(conn) + "\n")
            continue

        if not user_input:
            continue  # skip empty submissions rather than sending them to the model

        if memory.is_forget_command(user_input):
            message, needs_confirmation = memory.handle_forget(conn, user_input)
            print(message + "\n")
            if needs_confirmation:
                confirm = input("You: ").strip().lower()
                if confirm == "yes":
                    print(memory.confirm_forget_all(conn) + "\n")
                else:
                    print("[Cancelled.]\n")
            continue

        if memory.is_remember_command(user_input):
            memory.store_explicit_memory(conn, MODEL_NAME, user_input)
            print("[Got it, I'll remember that.]\n")
            continue

        history.append({"role": "user", "content": user_input})

        print(f"{persona['name']}: ", end="", flush=True)

        # Everything extra for this turn goes into `call_messages`, a
        # copy of history: matched memories and full tool results never
        # get appended to `history` itself, so they can't compound or get
        # persisted as if they were part of the actual conversation.
        call_messages = list(history)
        relevant_memories = memory.retrieve_relevant_memories(conn, user_input)
        memory_context = memory.format_memory_context(relevant_memories)
        if memory_context:
            call_messages.insert(-1, {"role": "system", "content": memory_context})

        try:
            assistant_reply, tool_turns = run_turn(conn, session_id, call_messages, ask_tool_permission)
        except Exception as e:
            print(f"\n[Error talking to Ollama: {e}]")
            print(f"Is Ollama running? Try 'ollama run {MODEL_NAME}' in another terminal to check.")
            history.pop()  # drop the user message — no reply came back for it
            continue

        print("\n")
        history.extend(tool_turns)
        history.append({"role": "assistant", "content": assistant_reply})

        # Only log once the whole exchange succeeded — an unanswered user
        # message shouldn't end up persisted. Tool turns are saved too, so
        # a --resume'd session still shows the model its own tool use.
        storage.log_message(conn, session_id, "user", user_input)
        for message in tool_turns:
            storage.log_message(
                conn, session_id, message["role"], message["content"],
                tool_calls=message.get("tool_calls"), tool_name=message.get("tool_name"),
            )
        storage.log_message(conn, session_id, "assistant", assistant_reply)

        # Runs after every exchange; failures here never break the chat
        # loop itself (see memory.extract_auto_memory). When tools were
        # used, the reply may repeat tool output, so only the user's own
        # message is considered.
        memory.extract_auto_memory(
            conn, MODEL_NAME, user_input, None if tool_turns else assistant_reply
        )


if __name__ == "__main__":
    main()