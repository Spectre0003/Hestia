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
        self._closed = False
        threading.Thread(target=self._synthesis_loop, daemon=True, name="hestia-tts").start()
        threading.Thread(target=self._playback_loop, daemon=True, name="hestia-audio").start()

    # --- feeding -----------------------------------------------------------
    def feed(self, token):
        for sentence in self._splitter.feed(token):
            self._texts.put((self._generation, sentence))

    def flush(self):
        for sentence in self._splitter.flush():
            self._texts.put((self._generation, sentence))

    def say(self, text):
        """Speak a whole message, e.g. 'I need you to approve that on screen.'"""
        splitter = SentenceSplitter()
        for sentence in splitter.feed(text) + splitter.flush():
            self._texts.put((self._generation, sentence))

    def stop(self):
        """Stop talking now and forget everything queued."""
        self._generation += 1
        self._splitter = SentenceSplitter()
        for q in (self._texts, self._audio):
            while not q.empty():
                try:
                    q.get_nowait()
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
                continue
            try:
                audio, rate = self._synthesize(text)
            except Exception:
                continue  # one bad sentence is skipped, not a broken voice
            if generation == self._generation:
                self._audio.put((generation, audio, rate))

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


def voice_models_present():
    return os.path.exists(KOKORO_MODEL) and os.path.exists(KOKORO_VOICES)
