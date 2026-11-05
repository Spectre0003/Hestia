"""
Hestia — voice (Stage 10 / v1.0)

Speaking: replies are cleaned for speech (code, links and Markdown never
read out), cut into sentences as they stream, and spoken by Kokoro —
one thread makes the audio while another plays the sentence before it,
so she starts talking before the reply is finished and doesn't pause
between sentences. Everything runs on the CPU; the GPU is the chat
model's. Nothing is recorded or saved, and nothing leaves the machine.
"""

import os
import queue
import re
import threading

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(_HERE, "models")
KOKORO_MODEL = os.path.join(MODELS, "kokoro", "kokoro-v1.0.int8.onnx")
KOKORO_VOICES = os.path.join(MODELS, "kokoro", "voices-v1.0.bin")
VOICE = "af_heart"  # a calm, warm voice; Kokoro has others (af_bella, bf_emma, ...)

CODE_NOTE = "I've put some code on screen."

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")           # [label](url) -> label
_URL = re.compile(r"https?://\S+")
_EMPHASIS = re.compile(r"\*{1,3}|_{2,3}|~~")
_LINE_MARKER = re.compile(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", re.MULTILINE)
# A sentence ends at . ! or ? followed by space and a capital, digit or quote
# ("e.g. a coffee" and "3.5" don't), or at a line break.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")


def speech_text(text):
    """What a piece of reply sounds like out loud: no links, no Markdown."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _LINE_MARKER.sub("", text)
    text = _EMPHASIS.sub("", text).replace("`", "")
    return " ".join(text.split())


class SentenceSplitter:
    """
    Turns a streaming reply into speakable sentences as soon as each one
    ends. Code blocks are never spoken — each becomes one short note — and
    a block that never closes is dropped rather than read out.
    """

    def __init__(self):
        self._buffer = ""
        self._in_code = False

    def feed(self, token):
        self._buffer += token
        return self._drain(final=False)

    def flush(self):
        return self._drain(final=True)

    def _drain(self, final):
        out = []
        while True:
            if self._in_code:
                end = self._buffer.find("```")
                if end < 0:
                    # Keep two characters in case a closing fence is split across tokens.
                    self._buffer = "" if final else self._buffer[-2:]
                    break
                self._buffer = self._buffer[end + 3:]
                self._in_code = False
                out.append(CODE_NOTE)
                continue

            fence = self._buffer.find("```")
            if fence >= 0:
                region, rest = self._buffer[:fence], self._buffer[fence + 3:]
                out.extend(self._sentences(region, complete=True)[0])
                self._buffer, self._in_code = rest, True
                continue

            # A trailing ` or `` might be the start of a fence: hold it back.
            hold = 0 if final else len(self._buffer) - len(self._buffer.rstrip("`"))
            hold = min(hold, 2)
            region = self._buffer[:len(self._buffer) - hold]
            done, remainder = self._sentences(region, complete=final)
            out.extend(done)
            self._buffer = remainder + self._buffer[len(self._buffer) - hold:]
            break
        return out

    @staticmethod
    def _sentences(region, complete):
        """(spoken sentences, leftover text not yet known to be a whole sentence)."""
        pieces = _BOUNDARY.split(region)
        leftover = "" if complete else pieces.pop()
        spoken = [speech_text(p) for p in pieces]
        return [s for s in spoken if s], leftover


class Speaker:
    """
    Speaks text as it arrives. synthesize/play/stop_audio default to Kokoro
    and the default sound device; tests pass their own.
    """

    def __init__(self, synthesize=None, play=None, stop_audio=None):
        self._synthesize = synthesize or self._kokoro
        self._play = play or self._play_default
        self._stop_audio = stop_audio or self._stop_default
        self._tts = None
        self._splitter = SentenceSplitter()
        self._generation = 0  # bumped by stop(): anything queued before it is dropped
        self._texts = queue.Queue()
        self._audio = queue.Queue()
        self._pending = 0  # sentences queued, being made or playing
        self._pending_lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._synthesis_loop, daemon=True, name="hestia-tts").start()
        threading.Thread(target=self._playback_loop, daemon=True, name="hestia-audio").start()

    # --- feeding -----------------------------------------------------------
    @property
    def speaking(self):
        """Talking, or about to: something is queued, being made or playing."""
        return self._pending > 0

    def _queue(self, sentence):
        with self._pending_lock:
            self._pending += 1
        self._texts.put((self._generation, sentence))

    def _done(self):
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)

    def feed(self, token):
        for sentence in self._splitter.feed(token):
            self._queue(sentence)

    def flush(self):
        for sentence in self._splitter.flush():
            self._queue(sentence)

    def say(self, text):
        """Speak a whole message, e.g. 'I need you to approve that on screen.'"""
        splitter = SentenceSplitter()
        for sentence in splitter.feed(text) + splitter.flush():
            self._queue(sentence)

    def stop(self):
        """Stop talking now and forget everything queued."""
        self._generation += 1
        self._splitter = SentenceSplitter()
        for q in (self._texts, self._audio):
            while not q.empty():
                try:
                    if q.get_nowait() is not None:
                        self._done()
                except queue.Empty:
                    break
        self._stop_audio()

    def close(self):
        self._closed = True
        self.stop()
        self._texts.put(None)
        self._audio.put(None)

    # --- the two threads -------------------------------------------------
    def _synthesis_loop(self):
        if self._synthesize == self._kokoro:
            # Load Kokoro now, while Hestia starts up anyway: on first use it
            # took ~5 s before her first word.
            try:
                self._load_kokoro()
            except Exception:
                pass  # retried on first use; a broken voice must not break startup
        while True:
            item = self._texts.get()
            if item is None:
                return
            generation, text = item
            if generation != self._generation:
                self._done()
                continue
            try:
                audio, rate = self._synthesize(text)
            except Exception:
                self._done()
                continue  # one bad sentence is skipped, not a broken voice
            if generation == self._generation:
                self._audio.put((generation, audio, rate))
            else:
                self._done()

    def _playback_loop(self):
        while True:
            item = self._audio.get()
            if item is None:
                return
            generation, audio, rate = item
            if generation == self._generation:
                try:
                    self._play(audio, rate)
                except Exception:
                    pass
            self._done()

    # --- defaults: Kokoro and the sound card ---------------------------------
    def _load_kokoro(self):
        if self._tts is None:
            from kokoro_onnx import Kokoro
            self._tts = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
            self._tts.create("Ready.", voice=VOICE, lang="en-us")  # first call also sets up phonemes

    def _kokoro(self, text):
        self._load_kokoro()
        return self._tts.create(text, voice=VOICE, speed=1.0, lang="en-us")

    @staticmethod
    def _play_default(audio, rate):
        import sounddevice
        sounddevice.play(audio, rate)
        sounddevice.wait()

    @staticmethod
    def _stop_default():
        import sounddevice
        sounddevice.stop()


def chime():
    """A soft two-note tone: Hestia heard the wake word and is listening."""
    import sounddevice
    rate = 24_000
    notes = []
    for pitch in (660, 880):
        moment = np.arange(int(rate * 0.09)) / rate
        notes.append(0.15 * np.sin(2 * np.pi * pitch * moment) * np.hanning(moment.size))
    sounddevice.play(np.concatenate(notes).astype(np.float32), rate)


def voice_models_present():
    return os.path.exists(KOKORO_MODEL) and os.path.exists(KOKORO_VOICES)


# ---------------------------------------------------------------- listening
WAKE_DIR = os.path.join(MODELS, "wakeword")
VAD_MODEL = os.path.join(WAKE_DIR, "silero_vad.onnx")
WHISPER_DIR = os.path.join(MODELS, "whisper")
# On the GPU, small.en: 1.9% words wrong on noisy speech vs base.en's 3.8%, in
# 0.08 s a sentence (base.en on the CPU: 0.42 s; small.en on the CPU: 2 s), and
# ~0.5 GB, which leaves the chat model fully on the GPU. Without a usable GPU
# (no NVIDIA card, or its libraries missing) she falls back to base.en on the CPU.
WHISPER_MODEL = "small.en"
WHISPER_FALLBACK = "base.en"

# The wake phrase is heard by Whisper itself: openWakeWord has no ready-made
# "Rise Hestia" model, and training one needs a Colab run. Whisper alone
# hears it as "Rise Hester"/"Hestra"/"Hesture"; told the phrase in advance
# (the prompt), it heard "Rise, Hestia" in 20/20 synthetic voices — and
# "Fall, Hestia" / "Mute, Hestia" in 10/10 each with all three in the prompt.
WAKE_PHRASE = "Rise Hestia"
HUSH_PHRASE = "Fall Hestia"   # stop talking, keep listening
MUTE_PHRASE = "Mute Hestia"   # stop listening: the microphone closes, unmute from the tray
WAKE_PROMPT = "Rise, Hestia. Fall, Hestia. Mute, Hestia."
# What you say to her is full of names Whisper hears wrong ("darker", "Hester"):
# naming them in advance fixes most of it.
TURN_PROMPT = WAKE_PROMPT + " Docker, Spotify, VS Code, GitHub, Python, JavaScript."
_NAME = r"\W+(?:hestia|hester|hestra|hesture|hestya|estia)\b\W*"
_WAKE = re.compile(r"^\W*(?:a?rise|rice)" + _NAME + r"(.*)$", re.IGNORECASE | re.DOTALL)
_COMMAND = re.compile(r"^\W*(fall|mute)" + _NAME + r"$", re.IGNORECASE)

RATE = 16_000
FRAME = 1280                  # 80 ms of audio per step
SPEECH_THRESHOLD = 0.5
WAKE_PAUSE_SECONDS = 0.4      # a pause this long ends a stretch of speech to check for the phrase
WAKE_CHECK_SECONDS = 2.0      # only a stretch's first 2 s are transcribed to look for it
PRE_ROLL_FRAMES = 3           # keep 240 ms before speech starts, so "Rise" isn't clipped
END_SILENCE_SECONDS = 0.8     # in a conversation, this long quiet ends your turn
MAX_RECORD_SECONDS = 15
_FRAMES_PER_SECOND = RATE / FRAME


def split_wake(text):
    """What was said after the wake phrase ('' if nothing), or None if it isn't there."""
    match = _WAKE.match(text or "")
    return match.group(1).strip() if match else None


def command_in(text):
    """'hush' for "Fall Hestia", 'mute' for "Mute Hestia", else None."""
    match = _COMMAND.match(text or "")
    return match and {"fall": "hush", "mute": "mute"}[match.group(1).lower()]


def _cuda_libraries():
    """NVIDIA's cuBLAS/cuDNN come as pip packages (requirements.txt); let Windows find their DLLs."""
    import glob
    import sys
    for folder in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        os.add_dll_directory(folder)
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def load_whisper():
    """small.en on the GPU if it works, else base.en on the CPU."""
    from faster_whisper import WhisperModel
    try:
        _cuda_libraries()
        model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="int8_float16", download_root=WHISPER_DIR)
        list(model.transcribe(np.zeros(RATE, np.float32), language="en")[0])  # a missing DLL shows up here
        return model
    except Exception:
        return WhisperModel(WHISPER_FALLBACK, device="cpu", compute_type="int8", download_root=WHISPER_DIR)


def listening_models_present():
    return os.path.exists(VAD_MODEL) and os.path.isdir(WHISPER_DIR)


class Listener:
    """
    Hears "Rise Hestia", then holds a conversation until "Fall Hestia". Fed
    80 ms frames of 16 kHz audio — by the microphone normally, by tests
    directly. Muting closes the microphone stream itself; nothing is ever
    written to disk or sent anywhere.

    A speech detector runs on every frame; each stretch of speech, once it
    ends, is transcribed. Outside a conversation only the three phrases
    count: the first two seconds are checked for them and the rest is
    forgotten. In one (awake() is true: the window says so, and she isn't
    thinking or talking), the whole stretch is your next turn. A stretch
    that began while she was talking is never a turn — it's mostly her own
    voice coming back through the microphone — but the phrases still work,
    so "Rise Hestia" or "Fall Hestia" cut her off.

    Callbacks run on the listener's thread, not the GUI's:
        on_wake()        "Rise Hestia"
        on_text(text)    what was said: after "Rise Hestia," in one breath, or a turn
        on_command(name) "Fall Hestia" ('hush') or "Mute Hestia" ('mute'), said on their own
    """

    def __init__(self, on_wake, on_text, on_command=None, awake=None, vad=None, transcribe=None):
        self.on_wake, self.on_text = on_wake, on_text
        self.on_command = on_command or (lambda name: None)
        self.awake = awake or (lambda: False)
        self._vad = vad
        self._transcribe = transcribe or self._whisper
        self._whisper_model = None
        self._whisper_lock = threading.Lock()
        self._stream_factory = None  # sounddevice.InputStream unless a test sets one
        self._stream = None
        self._frames = queue.Queue()
        self._worker = None
        self.muted = False
        self._reset()

    def _models(self):
        if self._vad is None:
            from openwakeword.vad import VAD
            self._vad = VAD(model_path=VAD_MODEL)

    def _reset(self):
        self._stretch = []        # the stretch of speech being collected
        self._stretch_quiet = 0
        self._stretch_awake = False
        self._recent = []         # the last few quiet frames, for the pre-roll

    # --- one frame at a time -----------------------------------------------
    def process(self, frame):
        self._models()
        speech = self._vad.predict(frame, frame_size=640) >= SPEECH_THRESHOLD
        if speech:
            if not self._stretch:
                self._stretch = list(self._recent)
                self._stretch_awake = self.awake()
            self._stretch.append(frame)
            self._stretch_quiet = 0
        elif self._stretch:
            self._stretch.append(frame)
            self._stretch_quiet += 1
        else:
            self._recent = (self._recent + [frame])[-PRE_ROLL_FRAMES:]
            return
        # Talking to her, a thinking pause is allowed; listening for a phrase, it isn't.
        pause = END_SILENCE_SECONDS if self._stretch_awake else WAKE_PAUSE_SECONDS
        too_long = len(self._stretch) / _FRAMES_PER_SECOND >= MAX_RECORD_SECONDS
        if self._stretch_quiet / _FRAMES_PER_SECOND >= pause or too_long:
            audio = np.concatenate(self._stretch).astype(np.float32) / 32768.0
            awake = self._stretch_awake and self.awake()
            self._reset()
            self._heard(audio, awake)

    def _heard(self, audio, awake):
        if awake:
            # One pass over all of it; the hint still lets the phrases through.
            heard = self._transcribe(audio, prompt=TURN_PROMPT) or ""
        else:
            heard = self._transcribe(audio[:int(WAKE_CHECK_SECONDS * RATE)], prompt=WAKE_PROMPT) or ""
        if command_in(heard):
            self.on_command(command_in(heard))
            return
        request = split_wake(heard)
        if request is not None:
            if not awake and len(audio) > WAKE_CHECK_SECONDS * RATE:
                # Longer than the check: transcribe it all to get the whole request.
                request = split_wake(self._transcribe(audio, prompt=TURN_PROMPT)) or request
            self.on_wake()
        elif awake:
            request = heard.strip()
        else:
            return  # ordinary speech, not to her: nothing kept
        if len(request) >= 2:
            self.on_text(request)

    def _load_whisper(self):
        with self._whisper_lock:  # loaded ahead at start(), and maybe by a frame at the same time
            if self._whisper_model is None:
                self._whisper_model = load_whisper()
        return self._whisper_model

    def _whisper(self, audio, prompt=None):
        self._load_whisper()
        # Cut off speech can send Whisper round in circles ("the heart of the heart
        # of..."), which took up to 6.6 s: cap the words at twice a fast talker's
        # rate, and skip the slow retries.
        limit = int(len(audio) / RATE * 8) + 8
        segments, _ = self._whisper_model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True, initial_prompt=prompt,
            max_new_tokens=limit, temperature=0.0)
        return " ".join(segment.text.strip() for segment in segments)

    # --- the microphone ------------------------------------------------------
    def start(self):
        """Open the microphone (unless muted) and start listening."""
        self._models()
        if self._transcribe == self._whisper:
            # Load now, not on the first "Rise Hestia" (2.5 s on the GPU).
            threading.Thread(target=self._load_whisper, daemon=True).start()
        if self._worker is None:
            self._worker = threading.Thread(target=self._consume, daemon=True, name="hestia-listen")
            self._worker.start()
        if not self.muted:
            self._open()

    def _open(self):
        if self._stream is not None:
            return
        factory = self._stream_factory
        if factory is None:
            import sounddevice
            factory = sounddevice.InputStream
        self._stream = factory(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                               callback=self._on_audio)
        self._stream.start()

    def _close(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._reset()

    def _on_audio(self, data, frames, time_info, status):
        self._frames.put(data[:, 0].copy())  # the audio callback must return fast

    def _consume(self):
        while True:
            frame = self._frames.get()
            if frame is None:
                return
            if self._stream is not None:
                try:
                    self.process(frame)
                except Exception:
                    # One bad frame must not end listening for the rest of the day.
                    self._reset()

    def set_muted(self, muted):
        """Muted means the microphone stream is closed, not merely ignored."""
        self.muted = muted
        if muted:
            self._close()
        else:
            self._open()

    def stop(self):
        self._close()
        self._frames.put(None)
