"""
Audition Pocket TTS (Kyutai, ~100M, MIT, CPU-only) as Hestia's voice.

Not part of Hestia yet: install it into the venv only to listen —
    venv\Scripts\python -m pip install pocket-tts
(on Windows this brings PyTorch for the CPU, a few hundred MB; the model
itself downloads from Hugging Face on the first run.)

    venv\Scripts\python scripts\voice_audition_pocket.py               all 21 built-in English voices
    venv\Scripts\python scripts\voice_audition_pocket.py vera eve      only these
    venv\Scripts\python scripts\voice_audition_pocket.py clip.wav      a voice cloned from a recording
    add --save to keep each one in audition\pocket\ as a .wav

A cloned voice copies whoever is in the clip, so only use a recording you
have the right to use: your own, someone who has agreed to it, or a
recording whose licence allows it. A clean 10-30 s clip of one calm voice,
no music or room echo, gives the best result — the clip's quality is what
comes out.
"""

import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LINES = (
    "Welcome back. 'Tis good to hear thee. "
    "Tomorrow looks clear, though cold, so take a coat before you head out. "
    "Your files are backed up, and the folder is called Projects, two thousand twenty six."
)

VOICES = ["alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles", "cosette",
          "eponine", "eve", "fantine", "george", "jane", "jean", "javert", "marius",
          "mary", "michael", "paul", "peter_yearsley", "stuart_bell", "vera"]


def save(path, audio, rate):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def main():
    try:
        from pocket_tts import TTSModel
    except ImportError:
        sys.exit("Pocket TTS isn't installed: venv\\Scripts\\python -m pip install pocket-tts")
    import sounddevice

    names = [a for a in sys.argv[1:] if not a.startswith("--")] or VOICES
    keep = "--save" in sys.argv
    out = os.path.join(ROOT, "audition", "pocket")
    if keep:
        os.makedirs(out, exist_ok=True)

    print("Loading Pocket TTS (the first run downloads it)...")
    model = TTSModel.load_model()
    print()
    for number, name in enumerate(names, 1):
        try:
            started = time.perf_counter()
            state = model.get_state_for_audio_prompt(name)
            audio = model.generate_audio(state, LINES).detach().cpu().numpy().astype(np.float32)
            took = time.perf_counter() - started
        except Exception as e:
            print(f"{number:2}. {name}: skipped ({str(e).splitlines()[0][:120]})")
            continue
        length = len(audio) / model.sample_rate
        print(f"{number:2}. {os.path.basename(name):20} made in {took:.1f} s for {length:.1f} s of speech")
        if keep:
            stem = os.path.splitext(os.path.basename(name))[0]
            save(os.path.join(out, f"{number:02}_{stem}.wav"), audio, model.sample_rate)
        sounddevice.play(audio, model.sample_rate)
        sounddevice.wait()
        time.sleep(0.6)

    print("\nTell me the name (or clip) you liked, and how fast each one was made.")


if __name__ == "__main__":
    main()
