# Progress Log

Reverse-chronological build log for Hestia. Each entry is one working session.

---

## Roadmap change — 2026-09-05

**Dropped Stage 11 (v2.0 — Home SOC / Wazuh convergence)** from the roadmap, and removed the "Home SOC" mention from Stage 0's ongoing foundations list. That convergence work may become its own separate project later, but it's no longer part of Hestia's plan. Hestia's roadmap now ends at Stage 10 (v1.0 — voice, GUI, background service).

---

## Stage 3 (v0.3) — Conversation persistence — ✅ Complete

**Goal:** conversations survive process restarts, without silently growing every prompt into an ever-larger blob of history.

### 2026-09-05 — SQLite storage layer, session model, and a merge with a collaborator's parallel implementation

**Designed and built the storage layer independently first**
- `storage.py`: two tables, `sessions` (id, started_at, ended_at) and `messages` (id, session_id, role, content, timestamp), linked by foreign key.
- Manual session control via a `new` command inside the chat loop, with the DB living at `data/hestia.db`.
- Verified end to end: resume-without-`new` correctly picked up prior messages, `new` correctly closed the old session and opened an empty one, and a message cap kept reloaded history bounded.

**A collaborator built a parallel Stage 3 independently in the same window**
- Different design: single `messages` table (no dedicated sessions table), UUID session IDs, a `--resume` CLI flag (default = brand-new session every launch, `--resume` = continue the most recent one), no history cap, and a `backup.py` script for manual on-demand backups.
- Reviewed both implementations side by side rather than picking one blind. The single-table/UUID approach had a latent bug (`get_latest_session_id` finds the session of the most recent *message* in the whole table, not the most recent *session* — fine today, fragile if that assumption ever breaks) and no cap on resumed history (fine today, will slow down or eventually break on a long-running session).

**Reconciled into one implementation**
- Kept the `sessions` table design (cleaner `started_at`/`ended_at` tracking than a bare `session_id` column).
- Adopted the collaborator's default-session-per-launch behavior plus `--resume` flag, replacing the original manual-only `new` command as the *default* (the in-chat `new` command still exists, for resetting mid-run without restarting).
- Added a 20-exchange (40-message) cap to history reloaded via `--resume`, closing the one real gap in the collaborator's version — full session history always stays in the database regardless, the cap only affects what's reloaded into context.
- Adopted `backup.py` from the collaborator, adapted to import `DB_PATH` from `storage.py` instead of duplicating the path, so the two files can't drift apart if the DB location ever changes.
- Explicitly did not adopt the collaborator's proposal to sync session data to a remote/cloud store — this conflicts directly with the project's local-only privacy principle stated in README.md, and was flagged and declined rather than merged.
- `db.py` (the collaborator's single-table/UUID module) was not added to the repo; every piece of it worth keeping was already folded into `storage.py`, `chat.py`, and `backup.py` above.

**Verified**
- Default launch always starts a new, empty session — confirmed it ignores prior sessions even when they exist.
- `--resume` correctly loads the most recently created session, not an older one.
- The 20-exchange cap trims correctly from the oldest end of a long session while leaving full history intact in the database.
- `backup.py` correctly locates and copies the live database to a timestamped file under `backups/`.
- All of the above tested against the real local setup (not just the dry-run stub testing done during development).

**Stage 3 (v0.3) milestone met:** conversations persist across restarts via SQLite, with a bounded resume path and manual backups, entirely on-device.

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