"""
Hestia — long-term memory (Stage 4 / v0.4)

Two capture paths:
- Explicit: a user message starting with "remember" is stored verbatim,
  no judgment call involved.
- Automatic: after every normal exchange, a short secondary call to the
  model asks whether that exchange contains a durable personal fact
  worth keeping long-term — separate from the main conversation, and
  never shown to the user.

Both paths ask the model for an optional "key" (for facts that can only
have one true value at a time, like favorite_color) so a later
statement of the same fact overwrites the earlier one instead of both
being stored side by side.

Retrieval is tag-based, not semantic: before each message is sent to
the model, this module checks the current message for keyword overlap
against stored tags and returns any matches. The chat loop is
responsible for injecting those as extra, unsaved context for that one
call — this module never touches conversation history directly.
"""

import json
import re

from ollama import chat

import storage

REMEMBER_PREFIX = "remember"

EXTRACTION_SYSTEM_PROMPT = """You extract durable personal facts about the user from a single conversation exchange, for long-term memory storage.

A durable fact is something like: a preference, an identity detail, an ongoing project, a relationship, or a recurring routine. It is NOT a one-off request, a question, small talk, or anything purely about the current task.

Given the user's message and the assistant's reply, respond with ONLY one of:
- A JSON object: {"key": "<short_snake_case_id_or_null>", "content": "<one sentence fact, third person>", "tags": ["<tag1>", "<tag2>"]}
- The single word: NONE

Use "key" only for facts that can only have one true value at a time (e.g. favorite_color, home_city, job_title) — this lets a later fact overwrite an earlier one instead of both being stored. Use null for facts that can coexist (e.g. hobbies, one-off events).
Respond with ONLY the JSON object or NONE — no other text."""


def _clean_model_output(raw):
    """Strip markdown code fences the model may wrap output in."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    return raw


def _structure_prompt(content):
    return (
        "For this fact about a user, respond with ONLY a JSON object: "
        '{"key": "<short_snake_case_id_or_null>", "tags": ["<tag1>", "<tag2>"]}\n'
        'Use "key" only if the fact can only have one true value at a time '
        "(e.g. favorite_color, home_city, job_title) — this lets a later "
        'statement of the same fact overwrite this one. Use null for facts '
        "that can coexist (e.g. hobbies, one-off events).\n"
        f'Fact: "{content}"\n'
        "Respond with ONLY the JSON object, no other text."
    )


def structure_fact(model_name, content):
    """
    Ask the model for a key (or null) and tags for a piece of content.
    Used by the explicit 'remember' path so it gets the same key-based
    dedup behavior as automatic extraction. Falls back to
    (None, ["general"]) on any failure or malformed output — a fact
    still gets saved even if structuring it fails.
    """
    try:
        response = chat(
            model=model_name,
            messages=[{"role": "user", "content": _structure_prompt(content)}],
            options={"num_predict": 60, "temperature": 0.2},
        )
        raw = _clean_model_output(response["message"]["content"])
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None, ["general"]
        data = json.loads(match.group(0))
        key = data.get("key") or None
        tags = data.get("tags") or ["general"]
        return key, tags
    except Exception:
        return None, ["general"]


def is_remember_command(user_input):
    return user_input.strip().lower().startswith(REMEMBER_PREFIX)


def strip_remember_prefix(user_input):
    """Strip a leading 'remember', then an optional 'that'/':' after it."""
    text = re.sub(r"(?i)^remember\b", "", user_input.strip()).strip()
    text = re.sub(r"(?i)^that\b", "", text).strip()
    text = text.lstrip(":").strip()
    return text


def store_explicit_memory(conn, model_name, user_input):
    """Handle a message starting with 'remember' — always stored, no judgment call."""
    content = strip_remember_prefix(user_input)
    if not content:
        return None
    key, tags = structure_fact(model_name, content)
    return storage.upsert_memory(conn, content=content, tags=tags, source="explicit", key=key)


def extract_auto_memory(conn, model_name, user_message, assistant_reply):
    """
    Run the lightweight extraction call after a normal exchange. Stores
    a memory if the model judges one worth keeping; otherwise a no-op.
    Never raises — extraction problems must never break the main chat.
    """
    prompt_messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"User: {user_message}\nAssistant: {assistant_reply}"},
    ]
    try:
        response = chat(
            model=model_name,
            messages=prompt_messages,
            options={"num_predict": 120, "temperature": 0.2},
        )
        raw = _clean_model_output(response["message"]["content"])
    except Exception:
        return None

    if raw.strip().upper().startswith("NONE"):
        return None

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None  # didn't follow the format — skip rather than guess

    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None

    content = data.get("content")
    if not content:
        return None
    tags = data.get("tags") or ["general"]
    key = data.get("key") or None

    return storage.upsert_memory(conn, content=content, tags=tags, source="auto", key=key)


def retrieve_relevant_memories(conn, text):
    """
    Return stored memories whose tags appear (case-insensitive) as a
    substring of the given text. Simple on purpose — no vectors until
    Stage 9.
    """
    text_lower = text.lower()
    matches = []
    for row in storage.get_all_memories(conn):
        tags = [t.strip().lower() for t in row["tags"].split(",") if t.strip()]
        if any(tag and tag in text_lower for tag in tags):
            matches.append(row)
    return matches


def format_memory_context(memories):
    """Turn matched memory rows into a single context string for injection."""
    if not memories:
        return None
    lines = ["Relevant things you know about the user:"]
    lines.extend(f"- {m['content']}" for m in memories)
    return "\n".join(lines)