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


if __name__ == "__main__":
    unittest.main()
