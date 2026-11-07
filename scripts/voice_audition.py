"""
Hear Hestia's candidate voices and pick one for voice.yaml.

    venv\Scripts\python scripts\voice_audition.py            play every candidate
    venv\Scripts\python scripts\voice_audition.py --high     the same, on the full-precision model
    venv\Scripts\python scripts\voice_audition.py --save     also write each one to audition\ as a .wav
    venv\Scripts\python scripts\voice_audition.py af_bella "af_heart:60,bf_emma:40"
                                                             only the voices or blends named

Each candidate says the same lines, in her new manner, so they're easy to
compare. The number before each one is what to look for in the list it
prints at the end. The full-precision model needs downloading once:
venv\Scripts\python scripts\download_models.py --high
"""

import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import voice  # noqa: E402

LINES = (
    "Ah, thou hast returned. 'Tis good to hear thee. "
    "Tomorrow looks clear, though cold, so take thy coat ere thou leavest. "
    "Thy files are backed up, and the folder is called Projects, two thousand twenty six."
)

# (voice or blend, accent, speed, what it's like)
CANDIDATES = [
    ("af_heart", "en-us", 1.0, "her current voice: warm, American"),
    ("af_heart", "en-us", 0.9, "the same, a little slower and calmer"),
    ("af_bella", "en-us", 0.95, "fuller, richer, American"),
    ("af_nicole", "en-us", 0.95, "soft, close, almost a whisper"),
    ("af_aoede", "en-us", 0.95, "smooth, a little lower"),
    ("af_kore", "en-us", 0.95, "clear, composed"),
    ("bf_emma", "en-gb", 0.95, "British, poised and mature"),
    ("bf_isabella", "en-gb", 0.95, "British, warm"),
    ("bf_alice", "en-gb", 0.95, "British, crisp"),
    ("bf_lily", "en-gb", 0.95, "British, light"),
    ({"af_heart": 60, "bf_emma": 40}, "en-gb", 0.92, "blend: heart's warmth, Emma's poise"),
    ({"bf_emma": 50, "af_bella": 50}, "en-gb", 0.92, "blend: British and rich"),
    ({"bf_isabella": 60, "af_nicole": 40}, "en-gb", 0.9, "blend: warm and soft"),
    ({"af_heart": 50, "af_aoede": 50}, "en-us", 0.92, "blend: warm and low"),
]


def parse(arg):
    """'af_bella' or 'af_heart:60,bf_emma:40' -> a voice or a blend."""
    if ":" not in arg:
        return arg
    blend = {}
    for part in arg.split(","):
        name, weight = part.split(":")
        blend[name.strip()] = float(weight)
    return blend


def label(choice):
    if isinstance(choice, str):
        return choice
    return "{" + ", ".join(f"{k}: {int(v)}" for k, v in choice.items()) + "}"


def save(path, audio, rate):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    high = "--high" in sys.argv
    keep = "--save" in sys.argv
    model = voice.KOKORO_MODEL_HIGH if high else voice.KOKORO_MODEL
    if not os.path.exists(model):
        sys.exit(f"{os.path.basename(model)} isn't downloaded: run scripts\\download_models.py"
                 + (" --high" if high else ""))

    from kokoro_onnx import Kokoro
    import sounddevice
    tts = Kokoro(model, voice.KOKORO_VOICES)
    candidates = CANDIDATES
    if args:
        candidates = [(parse(a), "en-gb" if parse(a) and "bf_" in str(a) else "en-us", 0.95, "")
                      for a in args]
    out = os.path.join(ROOT, "audition")
    if keep:
        os.makedirs(out, exist_ok=True)

    print(f"Model: {'full precision' if high else 'standard (int8)'}\n")
    for number, (choice, accent, speed, note) in enumerate(candidates, 1):
        try:
            style = voice.resolve_voice(tts, choice)
        except ValueError as e:
            print(f"{number:2}. {label(choice)}: skipped, {e}")
            continue
        started = time.perf_counter()
        audio, rate = tts.create(LINES, voice=style, speed=speed, lang=accent)
        took = time.perf_counter() - started
        length = len(audio) / rate
        print(f"{number:2}. {label(choice):38} {accent}  speed {speed}   {note}")
        print(f"    made in {took:.1f} s for {length:.1f} s of speech")
        if keep:
            save(os.path.join(out, f"{number:02}.wav"), audio, rate)
        sounddevice.play(audio, rate)
        sounddevice.wait()
        time.sleep(0.6)

    print("\nPut your favourite in voice.yaml, for example:")
    print("  voice: {af_heart: 60, bf_emma: 40}\n  accent: en-gb\n  speed: 0.92")
    if high:
        print("  quality: high")


if __name__ == "__main__":
    main()
