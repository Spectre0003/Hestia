"""
Hestia — engine (Stage 10 / v1.0)

Everything that makes Hestia Hestia, with no terminal in it: the
persona, the model, turns and tool calls, commands, memory, history,
and when a session ends. Front ends — the terminal (chat.py), the
window and tray (gui.py), voice (voice.py) — each hand a Session a
small UI object and show what it sends them:

    ui.text(token)                          a piece of the reply, as it streams
    ui.tool(name)                           a tool is about to run
    ui.notice(message)                      a status line: errors, indexing, notes
    ui.approve(name, arguments, after_web)  may this write-tier tool run? -> bool
    ui.confirm(question)                    a yes/no question, e.g. forget everything

The engine never prints and never exits the process. A startup problem
raises StartupError, because a window can't usefully print and quit.

This was chat.py's main loop until Stage 10; the behaviour is the same.
"""

import os
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import yaml
from ollama import chat, create

import embeddings
import memory
import storage
import tools
from servers import launcher

_HERE = os.path.dirname(os.path.abspath(__file__))

BASE_MODEL = "qwen2.5:7b"
NUM_CTX = 8192
# Hestia's own name for the base model with a bigger context window built
# in. Built in rather than passed per call: a call that left num_ctx out
# (memory.py makes two) would make Ollama reload the model, ~4 s each time.
# 4,096 was already 55% used before the user typed, at eight tools.
MODEL_NAME = "qwen2.5-7b-8k"
# Anchored to this folder, like data/: started at login, Hestia's working
# directory is wherever Windows chose, not the project.
PERSONALITY_PATH = os.path.join(_HERE, "personality.yaml")

# An always-on Hestia never exits, so "the end of a session" — when it's
# indexed for recall — becomes this long without a message.
IDLE_MINUTES = 20


class StartupError(Exception):
    """Hestia can't start; the message says why and what to do."""


def load_personality(path=PERSONALITY_PATH):
    """
    Load and parse personality.yaml. Fails loudly rather than silently —
    running without a personality isn't a degraded mode worth allowing
    quietly, since the whole point is that it's always on.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise StartupError(f"'{path}' not found. Hestia needs a personality file to start.")
    except yaml.YAMLError as e:
        raise StartupError(f"'{path}' is not valid YAML: {e}")
    if not isinstance(data, dict) or not data.get("name"):
        raise StartupError(f"'{path}' is missing required fields (at least 'name').")
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
        ("voice_style", "Talking out loud"),
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


def stream_reply(messages, ui, offer_tools=True):
    """
    Make one model call, sending text to the front end as it streams.
    Returns (text, tool_calls) — tool_calls is empty when the model
    answered in plain text rather than asking for a tool.
    """
    text = ""
    tool_calls = []
    schemas = tools.tool_schemas() if offer_tools else []
    stream = chat(model=MODEL_NAME, messages=messages, tools=schemas or None, stream=True)
    for chunk in stream:
        token = chunk.message.content or ""
        if token:
            ui.text(token)
        text += token
        if chunk.message.tool_calls:
            tool_calls.extend(chunk.message.tool_calls)
    return text, tool_calls


def run_turn(conn, session_id, call_messages, ui):
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
    turn = {}  # what this turn has read, for tools.execute's network rule

    for _ in range(tools.MAX_TOOL_ROUNDS):
        text, tool_calls = stream_reply(call_messages, ui)
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
            ui.tool(name)
            result = tools.execute(conn, session_id, name, call["function"]["arguments"], ui.approve, turn)
            call_messages.append({"role": "tool", "tool_name": name, "content": result})
            tool_turns.append({"role": "tool", "tool_name": name, "content": tools.RESULT_NOT_KEPT})
    else:
        # Out of rounds — one last call with no tools offered, so the
        # model has to answer in words instead of asking for another.
        text, _ = stream_reply(call_messages, ui, offer_tools=False)
        reply_parts.append(text)

    reply = " ".join(part.strip() for part in reply_parts if part.strip())
    # Live testing caught the model listing "search results" with made-up
    # links in a turn where no search ran. Prompt wording hasn't stopped this
    # kind of thing before, so the code says it instead. Shown, not saved.
    if ("http://" in reply or "https://" in reply) and not turn.get("web"):
        ui.notice("[Note: no web search or page fetch ran for this answer, "
                  "so any web addresses in it weren't looked up.]")
    return reply, tool_turns


OLLAMA_URL = "http://127.0.0.1:11434/api/version"
OLLAMA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")


def _ollama_up():
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=1):
            return True
    except OSError:
        return False


def start_services(wait_seconds=30):
    """
    Started from the desktop or at login, nothing else may be running yet.
    Starts Ollama (its own tray app, as its installer sets up) and waits for
    it; starts Docker Desktop and the SearXNG container in the background,
    so web search works without anyone starting them. Problems are left for
    the model setup below to report.
    """
    if not _ollama_up():
        app = os.path.join(OLLAMA_DIR, "ollama app.exe")
        command = [app] if os.path.exists(app) else [shutil.which("ollama") or "ollama", "serve"]
        try:
            subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
        except OSError:
            return
        end = time.monotonic() + wait_seconds
        while time.monotonic() < end and not _ollama_up():
            time.sleep(0.5)
    # Web search: Docker Desktop (if it isn't running) and the SearXNG container,
    # in the background — Docker takes about a minute and nothing waits for it.
    threading.Thread(target=launcher.start_search, daemon=True).start()


class Session:
    """
    One running Hestia: the model, the tool servers, the database, and
    the conversation in progress. Safe to use from a worker thread while
    a timer calls close_if_idle from another — one lock covers both.
    """

    def __init__(self, ui, resume=False):
        self.ui = ui
        self.persona = load_personality()
        start_services()
        try:
            # ~0.1 s, copies nothing, and is a no-op once the variant exists.
            create(model=MODEL_NAME, from_=BASE_MODEL, parameters={"num_ctx": NUM_CTX})
        except Exception as e:
            raise StartupError(
                f"Couldn't set up '{MODEL_NAME}' from '{BASE_MODEL}': {e}. Is Ollama running, "
                f"and has '{BASE_MODEL}' been pulled ('ollama pull {BASE_MODEL}')?")
        self.conn = storage.get_connection()
        # A tool server that won't start costs its tools, not the chat.
        self.problems = tools.start()
        self._lock = threading.RLock()
        self._closed = False

        self.session_id = storage.get_last_session_id(self.conn) if resume else None
        if resume and self.session_id is None:
            ui.notice("[No previous session found. Starting a new one.]")
        if self.session_id is None:
            self.session_id = storage.start_new_session(self.conn)
        self._start_history()

    @property
    def name(self):
        return self.persona["name"]

    def _start_history(self):
        """The system prompt, then this session's recent messages (none for a new one)."""
        system_prompt = build_system_prompt(self.persona) + "\n\n" + tools.TOOL_GUIDANCE
        self.history = [{"role": "system", "content": system_prompt}]
        self.history.extend(storage.load_recent_messages(self.conn, self.session_id))
        self.exchanges = 0
        self.last_activity = datetime.now()

    def _end_session(self):
        """
        Index for recall, then mark the session ended. Indexing waits for
        the end so it costs nothing while you're talking — on an 8GB card
        the embedding model shares VRAM with the chat model. Everything
        outstanding is indexed, so a session that ended in a crash is
        caught up too. Failure is reported and swallowed: not indexing is a
        lost convenience, never a reason Hestia can't move on or quit.
        """
        try:
            waiting = embeddings.pending_count(self.conn)
            if waiting:
                indexed, failed = embeddings.index_pending(self.conn)
                self.ui.notice(f"[Indexed {indexed} item(s) for recall." + (
                    f" {failed} couldn't be; run scripts/index_history.py to retry.]" if failed else "]"))
        except Exception:
            self.ui.notice("[Couldn't index this session. Run scripts/index_history.py later.]")
        storage.end_session(self.conn, self.session_id)

    def _new_session(self):
        self._end_session()
        self.session_id = storage.start_new_session(self.conn)
        self._start_history()

    def close_if_idle(self, now=None):
        """
        End and index the session once nothing has been said for
        IDLE_MINUTES, and start a fresh one. Returns True if it did.
        A session nobody has spoken in yet is left alone.
        """
        with self._lock:
            now = now or datetime.now()
            if self._closed or not self.exchanges or now - self.last_activity < timedelta(minutes=IDLE_MINUTES):
                return False
            self._new_session()
            return True

    def handle(self, text):
        """
        One thing the user said. Returns (text, was_command): a command's
        output, or the full reply (already streamed through ui.text).
        """
        with self._lock:
            self.close_if_idle()
            text = (text or "").strip()
            command = text.lower()
            if not text:
                return "", True
            if command == "new":
                self._new_session()
                return "[Started a new session.]", True
            if command == "memories":
                return memory.format_memory_list(self.conn), True
            if command == "tools":
                return tools.format_tool_log(self.conn), True
            if memory.is_forget_command(text):
                message, needs_confirmation = memory.handle_forget(self.conn, text)
                if not needs_confirmation:
                    return message, True
                if self.ui.confirm(message):
                    return memory.confirm_forget_all(self.conn), True
                return "[Cancelled.]", True
            if memory.is_remember_command(text):
                memory.store_explicit_memory(self.conn, MODEL_NAME, text)
                return "[Got it, I'll remember that.]", True
            return self._turn(text), False

    def _turn(self, user_input):
        self.history.append({"role": "user", "content": user_input})

        # Everything extra for this turn goes into `call_messages`, a
        # copy of history: matched memories and full tool results never
        # get appended to `history` itself, so they can't compound or get
        # persisted as if they were part of the actual conversation.
        call_messages = list(self.history)

        # The clock is injected every turn rather than left to the
        # get_current_time tool: asked the time twice in a session, the
        # model reuses the time from its own earlier reply instead of
        # calling the tool again.
        now = datetime.now().astimezone().strftime("%A, %d %B %Y, %H:%M (%Z)")
        call_messages.insert(-1, {
            "role": "system",
            "content": f"The current date and time is {now}. Use this, not any "
                       f"time mentioned earlier in the conversation.",
        })

        relevant_memories = memory.retrieve_relevant_memories(self.conn, user_input)
        memory_context = memory.format_memory_context(relevant_memories)
        if memory_context:
            call_messages.insert(-1, {"role": "system", "content": memory_context})

        try:
            reply, tool_turns = run_turn(self.conn, self.session_id, call_messages, self.ui)
        except Exception as e:
            self.ui.notice(f"[Error talking to Ollama: {e}. Is Ollama running? "
                           f"Try 'ollama run {MODEL_NAME}' in a terminal to check.]")
            self.history.pop()  # drop the user message — no reply came back for it
            return ""

        self.history.extend(tool_turns)
        self.history.append({"role": "assistant", "content": reply})

        # Only log once the whole exchange succeeded — an unanswered user
        # message shouldn't end up persisted. Tool turns are saved too, so
        # a resumed session still shows the model its own tool use.
        storage.log_message(self.conn, self.session_id, "user", user_input)
        for message in tool_turns:
            storage.log_message(
                self.conn, self.session_id, message["role"], message["content"],
                tool_calls=message.get("tool_calls"), tool_name=message.get("tool_name"),
            )
        storage.log_message(self.conn, self.session_id, "assistant", reply)
        self.exchanges += 1
        self.last_activity = datetime.now()

        # Failures here never break the conversation (see
        # memory.extract_auto_memory). Only the user's own message is ever
        # considered — the assistant's reply is never evidence of a fact
        # about the user.
        memory.extract_auto_memory(self.conn, MODEL_NAME, user_input)
        return reply

    def close(self):
        """End and index the session, stop the tool servers, close the database."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._end_session()
            self.conn.close()
            tools.stop()
