import threading
import time
import unittest

import support  # noqa: F401

import voice  # noqa: E402


class SpeechTextTest(unittest.TestCase):
    def test_speech_text_drops_code_and_links(self):
        spoken = voice.speech_text("See https://example.org/a and **bold** with `inline` and [a page](https://x.org).")
        self.assertNotIn("http", spoken)
        self.assertNotIn("*", spoken)
        self.assertNotIn("`", spoken)
        for kept in ("bold", "inline", "a page"):
            self.assertIn(kept, spoken)

    def test_markdown_structure_is_flattened(self):
        spoken = voice.speech_text("## Steps\n- first thing\n- second thing\n1. third")
        self.assertNotIn("#", spoken)
        self.assertNotIn("- ", spoken)
        self.assertIn("first thing", spoken)


class SentenceSplitterTest(unittest.TestCase):
    def feed_all(self, tokens):
        splitter = voice.SentenceSplitter()
        out = []
        for token in tokens:
            out.extend(splitter.feed(token))
        out.extend(splitter.flush())
        return out

    def test_sentences_come_out_as_soon_as_they_end(self):
        splitter = voice.SentenceSplitter()
        self.assertEqual(splitter.feed("Hello there. How a"), ["Hello there."])
        self.assertEqual(splitter.feed("re you? I'm fi"), ["How are you?"])
        self.assertEqual(splitter.flush(), ["I'm fi"])  # whatever is left at the end

    def test_numbers_and_abbreviations_are_not_sentence_ends(self):
        self.assertEqual(self.feed_all(["It costs 3.5 dollars, e.g. a coffee. Done."]),
                         ["It costs 3.5 dollars, e.g. a coffee.", "Done."])

    def test_code_blocks_are_never_spoken(self):
        out = self.feed_all(["Run this:\n``", "`python\nprint('secret')\n", "```\nThat's it."])
        joined = " ".join(out)
        self.assertNotIn("print", joined)
        self.assertIn("Run this:", joined)
        self.assertIn("code on screen", joined)
        self.assertIn("That's it.", joined)

    def test_an_unfinished_code_block_is_not_spoken_either(self):
        self.assertNotIn("rm -rf", " ".join(self.feed_all(["Try:\n```\nrm -rf build\n"])))


class SpeakerTest(unittest.TestCase):
    def make(self, play_seconds=0.0):
        played, made = [], []

        def synthesize(text):
            made.append(text)
            return text, 24000

        def play(audio, rate):
            time.sleep(play_seconds)
            played.append(audio)

        stopped = threading.Event()
        speaker = voice.Speaker(synthesize=synthesize, play=play, stop_audio=stopped.set)
        self.addCleanup(speaker.close)
        return speaker, made, played, stopped

    def wait_for(self, condition, seconds=3):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not condition():
            time.sleep(0.01)
        return condition()

    def test_sentences_are_spoken_in_order(self):
        speaker, made, played, _ = self.make()
        for token in ("First one. Sec", "ond one. Third"):
            speaker.feed(token)
        speaker.flush()
        self.assertTrue(self.wait_for(lambda: len(played) == 3))
        self.assertEqual(played, ["First one.", "Second one.", "Third"])

    def test_stop_cuts_her_off_and_drops_what_was_queued(self):
        speaker, made, played, stopped = self.make(play_seconds=0.3)
        speaker.feed("One. Two. Three. Four. ")
        self.assertTrue(self.wait_for(lambda: len(made) >= 1))
        speaker.stop()
        time.sleep(0.8)
        self.assertTrue(stopped.is_set())
        self.assertLess(len(played), 4)

    def test_say_speaks_a_whole_message(self):
        speaker, _, played, _ = self.make()
        speaker.say("I need you to approve that on screen.")
        self.assertTrue(self.wait_for(lambda: played == ["I need you to approve that on screen."]))


import numpy as np  # noqa: E402

SILENT = np.zeros(voice.FRAME, dtype=np.int16)


class FakeVAD:
    """Says 'speech' for frames whose first sample is non-zero."""

    def predict(self, frame, frame_size=640):
        return 0.9 if frame[0] else 0.0


SPEECH = np.full(voice.FRAME, 1000, dtype=np.int16)
PAUSE = [SILENT] * 8          # 0.64 s: longer than the wake pause, shorter than giving up


class SplitWakeTest(unittest.TestCase):
    def test_the_phrase_and_what_follows(self):
        self.assertEqual(voice.split_wake("Rise, Hestia. What time is it?"), "What time is it?")
        self.assertEqual(voice.split_wake(" rise hester, open notepad"), "open notepad")
        self.assertEqual(voice.split_wake("Rise Hestia."), "")
        self.assertEqual(voice.split_wake("Arise, Hestia!"), "")

    def test_other_speech_is_not_the_phrase(self):
        for text in ("I like Hestia", "The sun will rise", "Rise Hestiaville", "Hestia, rise", "", None):
            self.assertIsNone(voice.split_wake(text), text)


class ListenerTest(unittest.TestCase):
    def make(self, *transcripts):
        """Each transcribe call returns the next transcript (the last one repeats)."""
        self.events, self.calls = [], []
        replies = list(transcripts) or ["what time is it"]

        def transcribe(audio, prompt=None):
            self.calls.append((len(audio), prompt))
            return replies.pop(0) if len(replies) > 1 else replies[0]

        return voice.Listener(
            on_wake=lambda: self.events.append("wake"),
            on_text=lambda text: self.events.append(("text", text)),
            on_nothing=lambda: self.events.append("nothing"),
            vad=FakeVAD(), transcribe=transcribe, background=lambda work: work())

    def feed(self, listener, *frames):
        for frame in frames:
            listener.process(frame)

    def test_silence_costs_nothing(self):
        listener = self.make("Rise, Hestia.")
        self.feed(listener, *[SILENT] * 200)
        self.assertEqual((self.events, self.calls), ([], []))

    def test_phrase_then_pause_then_question(self):
        listener = self.make("Rise, Hestia.", "what time is it")
        self.feed(listener, *[SPEECH] * 10, *PAUSE, *[SPEECH] * 10, *[SILENT] * 12)
        self.assertEqual(self.events, ["wake", ("text", "what time is it")])
        self.assertEqual(self.calls[0][1], voice.WAKE_PROMPT)  # the check was hinted
        self.assertIsNone(self.calls[1][1])                    # the request was not

    def test_phrase_and_question_in_one_breath(self):
        listener = self.make("Rise, Hestia. What time is it?")
        self.feed(listener, *[SPEECH] * 20, *PAUSE)
        self.assertEqual(self.events, ["wake", ("text", "What time is it?")])
        self.assertFalse(listener.recording)

    def test_a_long_stretch_is_transcribed_whole_to_get_the_request(self):
        listener = self.make("Rise, Hestia. What", "Rise, Hestia. What time is it right now?")
        self.feed(listener, *[SPEECH] * 40, *PAUSE)  # 3.2 s: longer than the check
        self.assertEqual(self.events, ["wake", ("text", "What time is it right now?")])
        self.assertEqual(self.calls[0][0], int(voice.WAKE_CHECK_SECONDS * voice.RATE))

    def test_other_speech_is_ignored(self):
        listener = self.make("I was reading about Hestia yesterday.")
        self.feed(listener, *[SPEECH] * 20, *PAUSE, *[SPEECH] * 20, *PAUSE)
        self.assertEqual(self.events, [])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(listener.recording)

    def test_the_start_of_speech_is_kept(self):
        listener = self.make("nothing")
        self.feed(listener, *[SILENT] * 10, *[SPEECH] * 5, *PAUSE)
        expected = (voice.PRE_ROLL_FRAMES + 5 + 5) * voice.FRAME  # pre-roll, speech, 0.4 s pause
        self.assertEqual(self.calls[0][0], expected)

    def test_wake_but_nothing_said_gives_up(self):
        listener = self.make("Rise, Hestia.")
        self.feed(listener, *[SPEECH] * 10, *PAUSE, *[SILENT] * int(voice.WAIT_FOR_SPEECH_SECONDS * 12.5 + 5))
        self.assertEqual(self.events, ["wake", "nothing"])
        self.assertEqual(len(self.calls), 1)

    def test_long_speech_is_cut_off(self):
        listener = self.make("Rise, Hestia.", "what time is it")
        self.feed(listener, *[SPEECH] * 10, *PAUSE, *[SPEECH] * int(voice.MAX_RECORD_SECONDS * 12.5 + 5))
        self.assertEqual(self.events, ["wake", ("text", "what time is it")])

    def test_wake_phrase_repeated_is_stripped_and_empty_text_is_nothing(self):
        listener = self.make("Rise, Hestia.", "Rise Hestia, what time is it?")
        self.feed(listener, *[SPEECH] * 10, *PAUSE, *[SPEECH] * 5, *[SILENT] * 12)
        self.assertEqual(self.events[-1], ("text", "what time is it?"))
        listener = self.make("Rise, Hestia.", "  ")
        self.feed(listener, *[SPEECH] * 10, *PAUSE, *[SPEECH] * 5, *[SILENT] * 12)
        self.assertEqual(self.events[-1], "nothing")

    def test_a_frame_that_fails_doesnt_stop_listening(self):
        listener = self.make()
        calls = []

        def flaky(frame):
            calls.append(frame)
            if len(calls) == 1:
                raise RuntimeError("bad frame")

        listener.process = flaky
        listener._stream = object()  # as if the microphone were open
        import threading
        worker = threading.Thread(target=listener._consume, daemon=True)
        worker.start()
        for _ in range(3):
            listener._frames.put(SILENT)
        listener._frames.put(None)
        worker.join(timeout=3)
        self.assertEqual(len(calls), 3, "the listening thread died on the first bad frame")

    def test_mute_closes_the_microphone(self):
        opened = []

        class FakeStream:
            def __init__(self, **kwargs):
                opened.append(self)
                self.active = False

            def start(self):
                self.active = True

            def close(self):
                self.active = False

        listener = self.make()
        listener._stream_factory = FakeStream
        listener.start()
        self.assertTrue(opened[-1].active)
        listener.set_muted(True)
        self.assertFalse(opened[-1].active)
        listener.set_muted(False)
        self.assertEqual(len(opened), 2)
        self.assertTrue(opened[-1].active)
        listener.stop()


@unittest.skipUnless(voice.listening_models_present() and voice.voice_models_present(),
                     "voice models not downloaded")
class RealListeningTest(unittest.TestCase):
    def listen_to(self, *sentences):
        from scipy.signal import resample_poly
        from kokoro_onnx import Kokoro
        kokoro = Kokoro(voice.KOKORO_MODEL, voice.KOKORO_VOICES)
        pcm = [np.zeros(16000, np.int16)]
        for sentence in sentences:
            audio, _ = kokoro.create(sentence, voice=voice.VOICE)
            pcm += [(resample_poly(audio, 2, 3) * 32767).astype(np.int16),  # 24 kHz -> 16 kHz, like the mic
                    np.zeros(16000, np.int16)]
        pcm = np.concatenate(pcm + [np.zeros(16000, np.int16)])
        events = []
        listener = voice.Listener(on_wake=lambda: events.append("wake"),
                                  on_text=lambda t: events.append(t),
                                  on_nothing=lambda: events.append("nothing"),
                                  background=lambda work: work())
        for i in range(0, len(pcm) - voice.FRAME + 1, voice.FRAME):
            listener.process(pcm[i:i + voice.FRAME])
        return events

    def test_wake_phrase_and_question_in_one_breath(self):
        events = self.listen_to("Rise Hestia, what time is it right now?")
        self.assertEqual(events[0], "wake")
        self.assertIn("time", events[-1].lower())

    def test_wake_phrase_then_question(self):
        events = self.listen_to("Rise Hestia.", "What time is it right now?")
        self.assertEqual(events[0], "wake")
        self.assertIn("time", events[-1].lower())

    def test_other_speech_does_not_wake_her(self):
        self.assertEqual(self.listen_to("I was reading about the goddess Hestia yesterday."), [])


if __name__ == "__main__":
    unittest.main()
