"""
Download the voice models Hestia v1.0 uses into models/ (gitignored).
Run once per machine:  venv\\Scripts\\python scripts\\download_models.py

  models/kokoro/    Kokoro text-to-speech, int8 (~88 MB) + voices (~26 MB)
  models/whisper/   faster-whisper base.en speech-to-text (~140 MB)
  models/wakeword/  openWakeWord feature models + "hey jarvis" (~9 MB)

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

    import openwakeword.utils
    print("Downloading wake-word models...")
    openwakeword.utils.download_models(model_names=["hey_jarvis"],
                                       target_directory=os.path.join(MODELS, "wakeword"))

    from faster_whisper import WhisperModel
    print("Downloading Whisper base.en...")
    WhisperModel("base.en", device="cpu", compute_type="int8",
                 download_root=os.path.join(MODELS, "whisper"))
    print("Done.")


if __name__ == "__main__":
    main()
