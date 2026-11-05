"""
Download the voice models Hestia v1.0 uses into models/ (gitignored).
Run once per machine:  venv\\Scripts\\python scripts\\download_models.py

  models/kokoro/    Kokoro text-to-speech, int8 (~88 MB) + voices (~26 MB)
  models/whisper/   faster-whisper small.en (GPU, ~480 MB) and base.en (CPU fallback, ~140 MB)
  models/wakeword/  Silero speech detector, from openWakeWord (~2 MB)

Everything runs on the CPU, so the GPU stays free for the chat model.
Files that already exist are skipped.
"""

import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models")
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")


def main():
    kokoro = os.path.join(MODELS, "kokoro")
    os.makedirs(kokoro, exist_ok=True)
    for name in KOKORO_FILES:
        target = os.path.join(kokoro, name)
        if not os.path.exists(target):
            print(f"Downloading {name}...")
            urllib.request.urlretrieve(KOKORO_URL + name, target)

    # "Rise Hestia" is heard by Whisper; only openWakeWord's speech detector is needed.
    import openwakeword
    import openwakeword.utils
    wakeword = os.path.join(MODELS, "wakeword")
    os.makedirs(wakeword, exist_ok=True)
    for model in openwakeword.VAD_MODELS.values():
        if not os.path.exists(os.path.join(wakeword, model["download_url"].split("/")[-1])):
            print("Downloading the speech detector...")
            openwakeword.utils.download_file(model["download_url"], wakeword)

    from faster_whisper import WhisperModel
    for name in ("small.en", "base.en"):
        print(f"Downloading Whisper {name}...")
        WhisperModel(name, device="cpu", compute_type="int8", download_root=os.path.join(MODELS, "whisper"))
    print("Done.")


if __name__ == "__main__":
    main()
