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
FORGET_PREFIX = "forget"
FORGET_ALL_PHRASES = {"everything", "everything.", "all", "all memories", "all of it", "all of them"}

EXTRACTION_SYSTEM_PROMPT = """You extract durable personal facts about the user from a single conversation exchange, for long-term memory storage.

A durable fact is: a preference, an identity detail (job, field of study, location, relationships), an ongoing project, or a recurring routine. It is NOT a one-off request, a question, small talk, or anything purely about the current task.

IMPORTANT: extract the fact even if it's mentioned briefly or in passing, as a side note to an unrelated question. A message can be mostly about one thing (e.g. asking for help with code) while still containing a durable fact worth keeping (e.g. mentioning what the user studies or does for work). Don't skip a fact just because it wasn't the main point of the message.

Examples:
User: "quick question, how do I reverse a linked list in Python"
Assistant: "..."
-> NONE (pure task question, no personal fact)

User: "I'm a nurse and just got back from a night shift, can you help me relax"
Assistant: "..."
-> {"key": "job_title", "content": "User works as a nurse.", "tags": ["job", "work", "nurse"]}

User: "anyway, I'm a cybersecurity student so this is for a class project — how does XSS work"
Assistant: "..."
-> {"key": "field_of_study", "content": "User is a cybersecurity student.", "tags": ["study", "school", "cybersecurity", "education"]}

User: "what's the weather like today"
Assistant: "..."
-> NONE

Given the user's message and the assistant's reply, respond with ONLY one of:
- A JSON object: {"key": "<short_snake_case_id_or_null>", "content": "<one sentence fact, third person>", "tags": ["<tag1>", "<tag2>"]}
- The single word: NONE

RULES for "content": it must be a COMPLETE standalone sentence naming both the subject and the value. Write "User's favorite color is green." — NOT just "green". Someone reading the content alone, with no other context, must understand the whole fact.

RULES for "tags": include the core nouns of the fact itself, not just an abstract category. For a favorite color, use ["color", "favorite", "green"] — NOT just ["preference"]. Tags are used to find this fact later, so generic tags make it unfindable.

Use "key" only for facts that can only have one true value at a time (e.g. favorite_color, home_city, job_title, field_of_study) — this lets a later fact overwrite an earlier one instead of both being stored. Use null for facts that can coexist (e.g. hobbies, one-off events).
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


def _repair_content(content, key):
    """
    Rescue a fragmentary extraction. A 7B model will sometimes return
    just "green" as the content when the key is "favorite_color" —
    technically the right value, but useless once the key isn't in
    front of it. If the content is too short to stand alone and there's
    a key to work from, rebuild it into a full sentence.
    """
    content = (content or "").strip().rstrip(".")
    if not content:
        return None
    if len(content.split()) >= 4 or not key:
        return content if content.endswith(".") else content + "."
    label = key.replace("_", " ")
    return f"User's {label} is {content}."


def _enrich_tags(tags, content, key):
    """
    Make sure tags carry the fact's own nouns, not just an abstract
    category. A memory tagged only "preference" can't be found by
    anything; folding in tokens from the key and content keeps it
    reachable even when the model tags lazily.
    """
    tag_set = {str(t).strip().lower() for t in tags if str(t).strip()}
    tag_set |= _tokenize(key or "")
    tag_set |= _tokenize(content or "")
    tag_set.discard("user")
    return sorted(t for t in tag_set if t)
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
    tags = _enrich_tags(tags, content, key)
    return storage.upsert_memory(conn, content=content, tags=tags, source="explicit", key=key)


def is_forget_command(user_input):
    return user_input.strip().lower().startswith(FORGET_PREFIX)


def _strip_forget_prefix(user_input):
    text = re.sub(r"(?i)^forget\b", "", user_input.strip()).strip()
    return text.lstrip(":").strip()


def handle_forget(conn, user_input):
    """
    Handle a 'forget ...' command. Three forms:
    - "forget <id>"          — delete by numeric id (shown in `memories`)
    - "forget <key>"         — delete by canonical key (e.g. favorite_color)
    - "forget everything"    — wipe all stored memories

    Returns a tuple: (message_to_print, needs_confirmation).
    When needs_confirmation is True, the caller must ask the user to
    confirm before calling confirm_forget_all — wiping everything is
    destructive and shouldn't happen on a single ambiguous command.
    """
    target = _strip_forget_prefix(user_input)
    if not target:
        return ("[Forget what? Try 'forget <id>', 'forget <key>', or 'forget everything'.]", False)

    if target.lower() in FORGET_ALL_PHRASES:
        count = len(storage.get_all_memories(conn))
        if count == 0:
            return ("[Nothing to forget — no memories stored.]", False)
        return (f"[This will permanently delete all {count} stored memories. Type 'yes' to confirm, anything else to cancel.]", True)

    if target.isdigit():
        deleted = storage.delete_memory_by_id(conn, int(target))
        if deleted:
            return (f"[Forgot memory #{target}.]", False)
        return (f"[No memory with id {target} found. Try 'memories' to see what's stored.]", False)

    deleted = storage.delete_memory_by_key(conn, target)
    if deleted:
        return (f"[Forgot memory with key '{target}'.]", False)
    return (f"[Couldn't find a memory with id or key '{target}'. Try 'memories' to see ids and keys.]", False)


def confirm_forget_all(conn):
    """Actually wipe every memory. Only call this after explicit user confirmation."""
    count = storage.clear_all_memories(conn)
    return f"[Forgot all {count} memories.]"


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
    key = data.get("key") or None
    content = _repair_content(content, key)
    if not content:
        return None
    tags = _enrich_tags(data.get("tags") or [], content, key)

    return storage.upsert_memory(conn, content=content, tags=tags, source="auto", key=key)


# Words too common to be meaningful signal for retrieval matching.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am",
    "i", "me", "my", "mine", "you", "your", "yours", "it", "its",
    "of", "to", "in", "on", "at", "for", "with", "about", "and", "or",
    "but", "if", "so", "as", "that", "this", "these", "those", "what",
    "whats", "which", "who", "how", "when", "where", "why", "do", "does",
    "did", "can", "could", "would", "should", "will", "have", "has",
    "had", "not", "no", "yes", "again", "tell", "know", "like", "get",
}

# How many memories to inject at most when filtering by relevance, so
# a big store can't flood the prompt. Ordered by strongest overlap first.
MAX_INJECTED_MEMORIES = 6

# Below this many total memories, skip keyword filtering entirely and
# inject everything. Keyword matching can only find facts that share
# literal words with the question — it can't connect "cybersecurity
# student" to "what's my job status". While the store is small enough
# that everything fits comfortably in the prompt, injecting all of it
# is both simpler and strictly more capable. Real semantic retrieval
# arrives in Stage 9 with embeddings.
ALWAYS_INJECT_LIMIT = 25


def _normalize_word(word):
    """
    Normalize a single word so British and American spellings, plurals,
    and casing all collapse to the same form. Correctness matters less
    than *consistency* here — both sides of the comparison get the same
    treatment, so even a crude rule still matches like with like.
    """
    w = word.lower()

    # British -> American spelling variants
    if len(w) >= 6 and "our" in w[1:]:
        w = w[0] + w[1:].replace("our", "or")   # colour->color, favourite->favorite
    if w.endswith("ise"):
        w = w[:-3] + "ize"
    if w.endswith("isation"):
        w = w[:-7] + "ization"
    if w.endswith("re") and len(w) > 4:
        w = w[:-2] + "er"                       # centre->center, theatre->theater
    w = w.replace("grey", "gray")

    # crude plural / possessive stripping
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 3 and w.endswith("es") and not w.endswith("ses"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s"):
        w = w[:-1]

    return w


def _tokenize(text):
    """Split text into a set of normalized, meaningful tokens."""
    words = re.findall(r"[a-zA-Z']+", text or "")
    tokens = set()
    for word in words:
        normalized = _normalize_word(word)
        if normalized and normalized not in STOPWORDS and len(normalized) > 2:
            tokens.add(normalized)
    return tokens


def retrieve_relevant_memories(conn, text):
    """
    Return memories to inject for this turn.

    While the store is small (<= ALWAYS_INJECT_LIMIT), returns
    everything — keyword overlap can't bridge meaning, so filtering at
    that size loses facts for no real benefit. Above that, falls back to
    token-overlap matching against the memory's key, tags, and content,
    strongest overlap first.
    """
    all_memories = storage.get_all_memories(conn)

    if len(all_memories) <= ALWAYS_INJECT_LIMIT:
        return all_memories

    query_tokens = _tokenize(text)
    if not query_tokens:
        return []

    scored = []
    for row in all_memories:
        memory_tokens = (
            _tokenize(row["tags"])
            | _tokenize(row["content"])
            | _tokenize(row["key"] or "")
        )
        overlap = query_tokens & memory_tokens
        if overlap:
            scored.append((len(overlap), row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:MAX_INJECTED_MEMORIES]]


def format_memory_context(memories):
    """
    Turn memory rows into a context string for injection.

    Wording here is a balancing act. Too soft and the model replies
    "I don't have access to your personal information" while the facts
    sit in its context. Too strong and it works remembered details into
    every reply as proof it has them. This aims at the middle: you know
    these, use them when relevant, otherwise stay quiet about them.
    """
    if not memories:
        return None
    lines = [
        "Background — things you already know about the user from past "
        "conversations. This is established knowledge you have; if the user "
        "asks about any of it, answer directly and never claim you lack "
        "access to their information.",
        "",
        "However: do NOT bring these up unprompted. Most replies should not "
        "reference them at all. Only use a fact when it's directly relevant "
        "to what's being discussed right now. Never mention that you "
        "remembered something, never work a detail in to show that you know "
        "it, and never let these steer the topic. If none apply, ignore them "
        "entirely and answer as though they weren't here.",
        "",
    ]
    for m in memories:
        if m["key"]:
            label = m["key"].replace("_", " ")
            lines.append(f"- {label}: {m['content']}")
        else:
            lines.append(f"- {m['content']}")
    return "\n".join(lines)


def format_memory_list(conn):
    """
    Human-readable dump of everything stored, for the `memories`
    command. Useful for checking what actually got captured rather than
    inferring it from replies.
    """
    rows = storage.get_all_memories(conn)
    if not rows:
        return "[No memories stored yet.]"
    lines = [f"[{len(rows)} memory/memories stored]"]
    for row in rows:
        key_part = f" (key: {row['key']})" if row["key"] else ""
        lines.append(f"  #{row['id']} [{row['source']}]{key_part} {row['content']}")
        lines.append(f"      tags: {row['tags']}")
    return "\n".join(lines)