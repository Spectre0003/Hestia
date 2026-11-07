"""
Hear Hestia's candidate voices and pick one for voice.yaml.

    venv\Scripts\python scripts\voice_audition.py            play every candidate
    venv\Scripts\python scripts\voice_audition.py --high     the same, on the full-precision model
    venv\Scripts\python scripts\voice_audition.py --save     also write each one to audition\ as a .wav
    venv\Scripts\python scripts\voice_audition.py --more     round two: the voices not in round one
    venv\Scripts\python scripts\voice_audition.py --tune     round three: variations on the round-two favourite
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
    "Welcome back. 'Tis good to hear thee. "
    "Tomorrow looks clear, though cold, so take a coat before you head out. "
    "Your files are backed up, and the folder is called Projects, two thousand twenty six."
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

# Round two: every other English female voice, slowed a touch, plus calmer blends.
MORE = [
    ("af_sky", "en-us", 0.9, "airy, gentle"),
    ("af_river", "en-us", 0.9, "even, relaxed"),
    ("af_sarah", "en-us", 0.9, "plain, measured"),
    ("af_jessica", "en-us", 0.9, "light, friendly"),
    ("af_nova", "en-us", 0.9, "clear, bright"),
    ("af_alloy", "en-us", 0.9, "neutral, steady"),
    ("bf_emma", "en-gb", 0.85, "British, slow and stately"),
    ("af_heart", "en-gb", 0.88, "her voice, with a British accent and slower"),
    ({"af_heart": 50, "af_nicole": 50}, "en-us", 0.88, "blend: warm and hushed"),
    ({"af_aoede": 60, "af_sky": 40}, "en-us", 0.88, "blend: low and airy"),
    ({"bf_emma": 40, "af_nicole": 30, "af_heart": 30}, "en-gb", 0.86, "blend: poised, soft, warm"),
    ({"af_river": 50, "af_heart": 50}, "en-us", 0.9, "blend: even and warm"),
]

# Round three: variations on {bf_emma: 40, af_nicole: 30, af_heart: 30}, en-gb, 0.86.
# One thing changes at a time, so it's clear what each difference does.
_BASE = {"bf_emma": 40, "af_nicole": 30, "af_heart": 30}
TUNE = [
    (_BASE, "en-gb", 0.86, "the favourite, as it was"),
    # speed
    (_BASE, "en-gb", 0.80, "slower"),
    (_BASE, "en-gb", 0.83, "a little slower"),
    (_BASE, "en-gb", 0.90, "a little quicker"),
    # mix
    ({"bf_emma": 50, "af_nicole": 25, "af_heart": 25}, "en-gb", 0.86, "more Emma: more poise and accent"),
    ({"bf_emma": 30, "af_nicole": 35, "af_heart": 35}, "en-gb", 0.86, "less Emma: softer, less British"),
    ({"bf_emma": 40, "af_nicole": 40, "af_heart": 20}, "en-gb", 0.86, "more Nicole: more hushed and smooth"),
    ({"bf_emma": 40, "af_nicole": 20, "af_heart": 40}, "en-gb", 0.86, "more Heart: warmer, more present"),
    # a fourth voice, a little of it
    ({"bf_emma": 35, "af_nicole": 25, "af_heart": 25, "af_aoede": 15}, "en-gb", 0.86, "plus a little Aoede: lower, smoother"),
    ({"bf_emma": 35, "af_nicole": 25, "af_heart": 25, "bf_isabella": 15}, "en-gb", 0.86, "plus a little Isabella: warmer British"),
    ({"bf_isabella": 40, "af_nicole": 30, "af_heart": 30}, "en-gb", 0.86, "Isabella in place of Emma"),
    # accent
    (_BASE, "en-us", 0.86, "same mix, American pronunciation"),
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
    candidates = TUNE if "--tune" in sys.argv else MORE if "--more" in sys.argv else CANDIDATES
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
