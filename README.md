# Hestia

A fully local, privacy-first personal AI assistant — built stage by stage, from a bare Ollama install up through personality, memory, tool use, and voice.

This repo is the public build log: what got built, in what order, and why.

## Why local

No conversation, memory, or tool call leaves the machine except through the two internet tools added in Stage 8, and then only what they send: a search query (to a self-hosted SearXNG on this machine, which passes it on to public search engines) or the address of a public page to read. Both run under a `network` permission tier that refuses them for the rest of any turn in which local data — files, repos, running processes — has been read, so none of it can leave in a query or an address. Nothing here depends on a cloud API or an account. Voice is local too: the wake word, speech recognition and Hestia's own voice all run on this machine's CPU, audio is never saved, and muting the microphone closes it rather than ignoring it.

## Hardware

| Component | Spec |
|---|---|
| GPU | RTX 5060, 8GB VRAM |
| RAM | 16GB |
| OS | Windows |

Hardware shapes model choice throughout — most stages plan around 7B–8B, Q4_K_M-class quantized models as the sweet spot for this GPU.

## Running Hestia

Windows, Python 3.11+, and [Ollama](https://ollama.com) with the chat and memory models:

```
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python scripts\download_models.py
```

On Python 3.14 the `requirements.txt` install stops at `kokoro-onnx`, which still
declares a `<3.14` cap even though it works there. Install the rest first, then add it:

```
venv\Scripts\python -m pip install PySide6-Essentials openwakeword faster-whisper sounddevice
venv\Scripts\python -m pip install --ignore-requires-python kokoro-onnx
```

Then either:

- `venv\Scripts\pythonw gui.py` — the window, with a tray icon. Closing the window keeps Hestia in the tray; right-click the icon to quit, mute the microphone, or have her start with Windows. Say **"Rise Hestia"** to start a conversation: from then on just talk — she answers out loud and waits for you, no wake word needed — until **"Fall Hestia"** ends it. **"Mute Hestia"** closes the microphone until you unmute it from the tray. She can open any app in your Start menu. She can start and stop Docker for you ("start Docker", "turn off Docker"), and asks before reading out a long answer. Talking to her never brings up the window; approvals are always a click, never your voice. While she's waiting she uses under 300 MB of RAM and none of the GPU: "Rise Hestia" loads everything (about 5 seconds), and two quiet minutes put her back on standby.
- `venv\Scripts\python chat.py` — the same Hestia in a terminal.

For a desktop icon, run `venv\Scripts\python gui.py --desktop-shortcut` once. Hestia starts Ollama herself if it isn't running, and her SearXNG search container if Docker is.

Optional, per machine: `apps.yaml`, `repos.yaml` and `.env` (copy the `.example` files), and `docker compose -f searxng/compose.yaml up -d` for web search.

Her character lives in `personality.yaml` and her voice in `voice.yaml` — one Kokoro voice or a blend, speed, accent and quality. To hear the options, run `venv\Scripts\python scripts\voice_audition.py` (add `--more` for more voices, `--tune` for variations on the current favourite). For `quality: high`, the smoother full-precision model, run `venv\Scripts\python scripts\download_models.py --high` once.

## Roadmap

| Stage | Version | Focus | New tools | Status |
|---|---|---|---|---|
| 0 | — | Foundations (ongoing, not gated) | Python, Docker, networking | Ongoing |
| 1 | v0.1 | Local model runtime + CLI chat | Ollama, LM Studio | ✅ Complete |
| 2 | v0.2 | Personality | YAML config | ✅ Complete |
| 3 | v0.3 | Conversation persistence | SQLite, backup script | ✅ Complete |
| 4 | v0.4 | Long-term memory (no vectors yet) | SQLite tables, tag-based retrieval | ✅ Complete |
| 5 | v0.5 | Tool calling | MCP (Python SDK), logging, permission tiers | ✅ Complete |
| 6 | v0.6 | Computer interaction | psutil, second MCP server, app allowlist | ✅ Complete |
| 7 | v0.7 | Dev environment integration | Git, VS Code, Docker, WSL2, repo allowlist | ✅ Complete |
| 8 | v0.8 | Internet tools | Self-hosted SearXNG, fetch_page, network tier, `.env`/secrets | ✅ Complete |
| 9 | v0.9 | Advanced memory | nomic-embed-text via Ollama, vectors in SQLite, recall tool | ✅ Complete |
| 10 | v1.0 | Actual assistant | Qt window + tray, wake word (openWakeWord), faster-whisper, Kokoro, start with Windows | ✅ Complete |
| 11 | v1.1 | Optimisation and polish | "Rise Hestia" conversations, Whisper on the GPU, standby (loads on wake), any app, Docker on demand, her lore and old-English manner, a voice chosen by audition, hearth-fire indicator and violet theme | ✅ Complete |

Detailed, dated build notes for each stage live in [PROGRESS.md](./PROGRESS.md).

## Principles carried through every stage

- Tool output is always treated as data, never as instructions (critical from Stage 5 on)
- Secrets live in `.env` (gitignored, `.env.example` documents it), never hardcoded (from Stage 8 on)
- Every tool call gets a structured log entry (from Stage 5 on)
- Backup capability for any persistent database, run manually for now (from Stage 3 on; actual scheduling may come later)
- Permission tiers enforced in code, not convention (from Stage 5 on)