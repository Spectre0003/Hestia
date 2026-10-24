# Hestia

A fully local, privacy-first personal AI assistant — built stage by stage, from a bare Ollama install up through personality, memory, tool use, and voice.

This repo is the public build log: what got built, in what order, and why.

## Why local

No conversation, memory, or tool call leaves the machine except through the two internet tools added in Stage 8, and then only what they send: a search query (to a self-hosted SearXNG on this machine, which passes it on to public search engines) or the address of a public page to read. Both run under a `network` permission tier that refuses them for the rest of any turn in which local data — files, repos, running processes — has been read, so none of it can leave in a query or an address. Nothing here depends on a cloud API or an account.

## Hardware

| Component | Spec |
|---|---|
| GPU | RTX 5060, 8GB VRAM |
| RAM | 16GB |
| OS | Windows |

Hardware shapes model choice throughout — most stages plan around 7B–8B, Q4_K_M-class quantized models as the sweet spot for this GPU.

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
| 10 | v1.0 | Actual assistant | Voice (whisper.cpp, Kokoro/XTTS), GUI, background service | ⏭️ Up next |

Detailed, dated build notes for each stage live in [PROGRESS.md](./PROGRESS.md).

## Principles carried through every stage

- Tool output is always treated as data, never as instructions (critical from Stage 5 on)
- Secrets live in `.env` (gitignored, `.env.example` documents it), never hardcoded (from Stage 8 on)
- Every tool call gets a structured log entry (from Stage 5 on)
- Backup capability for any persistent database, run manually for now (from Stage 3 on; actual scheduling may come later)
- Permission tiers enforced in code, not convention (from Stage 5 on)