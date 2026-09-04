# Progress Log

Reverse-chronological build log for Hestia. Each entry is one working session.

---

## Stage 1 (v0.1) — Local model runtime + CLI chat

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

---