# Progress Log

Reverse-chronological build log for Hestia. Each entry is one working session.

---

## Stage 3 (v0.3) — Conversation persistence — ✅ Complete

**Goal:** conversations survive process restarts. Built with SQLite and a backup script.

### 2026-09-05 — Database layer, session management, and backups

**Added `db.py`**
- Manages an SQLite database `hestia.db` with a `messages` table.
- Stores `session_id`, `role`, `content`, and a `timestamp`.
- Functions for loading, saving, and managing session UUIDs.

**Updated `chat.py`**
- Now accepts a `--resume` flag to continue the most recent session.
- Generates a new session ID by default unless `--resume` is passed.
- Loads existing session history and injects the system prompt dynamically on startup.
- Saves user messages and assistant responses to the DB in real-time.

**Created `backup.py`**
- Simple script to copy the `hestia.db` to a `backups/` folder with a timestamped filename.
- Fulfils the Stage 3 principle of scheduled backups for persistent databases.

**Status:** Stage 3 (v0.3) milestone met.

---

## Stage 2 (v0.2) — Personality — ✅ Complete

**Goal:** move Hestia's behavior from nothing (Stage 1 had zero personality shaping) into a YAML config, so tone, values, and behavioral boundaries are data, not code.

### 2026-09-05 — personality.yaml schema, prompt assembly, and wiring

**Designed and wrote `personality.yaml`**
- Rich persona: name, essence, traits, values, and dedicated style fields (curiosity, humor, empathy, pacing, collaboration, knowledge, disagreement, relationship to the user, speech style, formality).
- `boundaries` block covers three specific behaviors: can say no firmly, can say "I don't know," handles being wrong without over-apologizing.
- `lore` deliberately left commented out, not stubbed as an empty field — background/mythology may be added in a later stage; the distinction between "deferred" and "empty" matters for how the assembler and future edits treat it.

**Added personality loading to `chat.py`**
- `load_personality()` reads and parses the YAML with `PyYAML`, fails loudly (clear message + clean exit) on a missing file or invalid YAML rather than silently running without a persona.
- `build_system_prompt()` assembles the parsed dict into a single system-prompt string in a fixed, readable order: identity → traits → style fields → boundaries. New freeform fields need one line added to the `style_fields` list; existing fields need no code changes to edit.
- System prompt is injected as `history[0]` (`role: system`) before the loop starts, so every model call sees it first.
- Reply label in the terminal now pulls from `persona['name']` instead of a hardcoded string, so it can't drift out of sync with the YAML.

**Verified**
- `chat.py` compiles cleanly; `build_system_prompt()` dry-run confirmed against the real `personality.yaml` produces a well-formed prompt covering every field.
- Ran end to end: persona genuinely shows up in tone (calm, composed, philosophical without hedging) on open-ended questions.
- Missing/invalid `personality.yaml` confirmed to fail with a clear message and clean exit, not a stack trace.

**Known, deferred issue:** `essence` currently leans on "hearth" imagery heavily enough that it surfaces even in loosely-related answers. Not fixed yet — deliberately deferred to a fuller persona depth pass planned after Stage 3/4 (persistence + memory), since memory will surface real usage patterns worth tuning against, rather than guessing now against a blank-slate test surface.

**Stage 2 (v0.2) milestone met:** Hestia's personality lives entirely in `personality.yaml`, is injected as a system prompt at runtime, and the loader fails safely rather than silently if the file is missing or broken.

---

## Stage 1 (v0.1) — Local model runtime + CLI chat — ✅ Complete

**Goal:** run a local model, talk to it from a terminal — no memory, tools, or personality yet.

**Hardware:** RTX 5060 (8GB VRAM), 16GB system RAM, Windows.

### 2026-09-05 — Ollama installed, model pulled, GPU inference verified

**Installed Ollama for Windows**
- Native app, no admin rights required, installs to the user profile.
- Installer auto-adds `ollama` to PATH and starts it as a background service.
- Verified: `ollama --version` returns a version string; `http://localhost:11434` returns `Ollama is running`.
- Confirmed NVIDIA driver meets Ollama's minimum (452.39+) via `nvidia-smi`.

**Pulled a model**
- `ollama pull qwen2.5:7b` — default library tag, already quantized (Q4-class, ~4-5GB on disk).
- Verified with `ollama list`.

**Ran it and confirmed GPU inference**
- `ollama run qwen2.5:7b` — interactive chat in the terminal, streaming responses.
- Generation speed well above reading pace — the expected signal of GPU (not CPU) inference.
- `nvidia-smi`'s per-process memory column showed `N/A` for `llama-server.exe` (Ollama's actual inference subprocess). This is expected, not an error — Windows' WDDM driver model doesn't expose per-process VRAM to `nvidia-smi`; only the top-level total is reliable there.
- Confirmed GPU usage a different way instead: the process was tagged `Type: C` (pure compute), distinct from every other `C+G` (compute+graphics) process in the list — combined with the speed observation, that's sufficient confirmation without needing the missing memory figure.

**Status:** steps 1–3 of 6 complete for this stage. Next: Python environment setup (step 4).

### 2026-09-05 — Python environment, repo tooling, and the chat script

**Set up the Python environment**
- Created a `venv` inside the project folder, activated it, upgraded `pip`.
- `pip install ollama` — the official Python client for Ollama's API.

**Connected the local folder to GitHub**
- `git init`, added `.gitignore` (excludes `venv/`, and forward-looking entries for `.env` and database files ahead of Stage 3/6), linked `origin`, and pulled down the README/PROGRESS already on GitHub with `--allow-unrelated-histories`.
- Linked the same folder in VS Code's Source Control panel and GitHub Desktop — all three (terminal, VS Code, GitHub Desktop) now read/write the same local `.git` folder interchangeably.

**Wrote `chat.py`**
- Minimal CLI loop: reads input, sends the running in-memory conversation history to `ollama.chat(..., stream=True)`, prints the streamed response, appends it to history.
- No persistence — history lives only for the life of the running process (that's Stage 3's job).
- Handles a missing/unreachable Ollama service with a clear message instead of a raw stack trace.

**Ran it end to end**
- Multi-turn exchange confirmed the model correctly used earlier context (in-memory history working as intended).
- Streaming output confirmed working, matching the `ollama run` behavior from step three.
- Clean exit via typed `exit` and `Ctrl+C`, both tested.

**Stage 1 (v0.1) milestone met:** a CLI loop where typing a message gets a coherent response from a model running entirely on this machine, with no internet call involved.

---