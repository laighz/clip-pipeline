"""Number words are normalized only in rendered, timed subtitles."""
import json
import re
import sys
import tempfile
import unittest
from itertools import groupby
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from render_clip import build_ass  # pyright: ignore[reportMissingImports]
from _lib import load_words  # pyright: ignore[reportMissingImports]


def render(*words, styled=False, segments=None, **options):
    tokens = [{"w": w, "s": i * .4, "e": i * .4 + .35} for i, w in enumerate(words)]
    clip = {"id": "clip01", "hook": "Drei Autos", "segments": segments or [{"start": 0, "end": 10}]}
    cues = [] if styled else None
    ass, _, count = build_ass(clip, tokens, {"caption_uppercase": False}, None, emoji_cues=cues, **options)
    events = [line.split(",", 9) for line in ass.splitlines() if ",Caption,," in line or ",PhraseCaption,," in line]
    text = [re.sub(r"\{[^}]*\}", "", event[-1]).replace(r"\N", " ") for event in events]
    return " ".join(s for s, _ in groupby(text)), events, count, ass, tokens


class CaptionNumbersTest(unittest.TestCase):
    def test_both_caption_styles_use_digits_but_leave_hook_and_transcript_alone(self):
        for styled in (False, True):
            with self.subTest(styled=styled):
                text, _, count, ass, tokens = render("Ein", "Auto,", "zwei", "Boote,", "eins!", styled=styled)
                self.assertEqual(text, "Ein Auto, 2 Boote, 1!")
                self.assertEqual(count, 5)
                self.assertIn(",,Drei Autos", ass)
                self.assertEqual([t["w"] for t in tokens], ["Ein", "Auto,", "zwei", "Boote,", "eins!"])

    def test_multiword_numbers_keep_punctuation_and_full_highlight(self):
        text, events, count, _, _ = render("Zwei", "Millionen,", "minus", "drei", "Komma", "fünf.")
        self.assertEqual(text, "2000000, -3,5.")
        self.assertEqual(count, 6)
        self.assertEqual(events[0][1:3], ["0:00:00.00", "0:00:00.80"])
        self.assertEqual(events[1][1:3], ["0:00:00.80", "0:00:02.95"])

    def test_punctuation_and_segments_separate_number_phrases(self):
        self.assertEqual(render("Eins,", "zwei,", "drei!", "21", "einsam.")[0], "1, 2, 3! 21 einsam.")
        segments = [{"start": 0, "end": .36}, {"start": .4, "end": .75}]
        self.assertEqual(render("zwei", "Millionen", segments=segments)[0], "2 Millionen")

    def test_long_cardinals_and_ordinals(self):
        self.assertEqual(render("vierundzwanzig", "Autos.", "Am", "dritten", "Tag.")[0], "24 Autos. Am 3. Tag.")
        _, events, _, _, _ = render("Am", "dritten", "Tag.")
        self.assertTrue(all("Tag." in e[-1] for e in events))

    def test_large_scales_need_a_multiplier_but_one_can_be_inflected(self):
        self.assertEqual(render("Millionen", "Fans.", "2", "Millionen.", "Eine", "Million!")[0],
                         "Millionen Fans. 2 Millionen. 1000000!")

    def test_a_pause_does_not_join_separate_numbers(self):
        clip = {"segments": [{"start": 0, "end": 3}]}
        tokens = [{"w": "zwanzig", "s": 0., "e": .2}, {"w": "fünf", "s": 1.2, "e": 1.5}]
        ass, _, _ = build_ass(clip, tokens, {}, None)
        self.assertIn("}20{", ass)
        self.assertIn("}5{", ass)

    def test_english_uses_english_rules(self):
        self.assertEqual(render("One", "car.", "Twenty", "five", "coins.", language="en")[0], "1 car. 25 coins.")
        self.assertEqual(render("One", "second.", "On", "the", "second", "day.", language="en")[0],
                         "1 second. On the 2nd day.")

    def test_loaded_transcript_languages_override_the_streamer_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "transcripts").mkdir()
            for lang, phrase, offset in [("en", ("Twenty", "five!"), 0), ("de", ("Drei!",), 2)]:
                records = [{"w": w, "s": offset + i * .4, "e": offset + i * .4 + .3}
                           for i, w in enumerate(phrase)]
                (work / "transcripts" / f"{lang}.json").write_text(json.dumps({"language": lang, "words": records}))
            clip = {"segments": [{"start": 0, "end": 3}]}
            words = load_words(work, include_language=True)
            ass, _, _ = build_ass(clip, words, {}, None, language="de")
            self.assertIn("}25!{", ass)
            self.assertIn("}3!{", ass)
            self.assertTrue(all("transcript_language" not in w for w in load_words(work)))

    def test_straddling_or_post_cut_word_never_merges_with_pre_cut_number(self):
        clip = {"segments": [{"start": 0, "end": 1}, {"start": 1, "end": 2}]}
        for start in (.98, 1.):
            for styled in (False, True):
                with self.subTest(start=start, styled=styled):
                    words = [{"w": "twenty", "s": .6, "e": .94}, {"w": "five", "s": start, "e": 1.04}]
                    ass, _, _ = build_ass(clip, words, {}, None, language="en",
                                          emoji_cues=[] if styled else None)
                    self.assertNotIn("25", ass.split("[Events]")[-1])

    def test_mixed_digits_and_words_do_not_change_the_numeric_value(self):
        self.assertEqual(render("2", "hundred", "thousand!", language="en")[0], "200000!")
        self.assertEqual(render("Drei", "Komma", "05.", "4", "Komma", "drei!")[0], "3,05. 4,3!")
        self.assertEqual(render("2", "Millionen")[0], "2 Millionen")
        self.assertEqual(render("2", "Millionen", "dreihundert")[0], "2000300")
        self.assertEqual(render("2", "million", "six", "hundred", language="en")[0], "2000600")

    def test_mixed_numbers_accept_large_integers_and_fraction_aliases(self):
        cases = [("en", "two point 5 nought six", "2.506"),
                 ("en", "three point 05 nought 26", "3.05026"),
                 ("en", "3 hundred and 25", "325"),
                 ("de", "1000 Komma fünf", "1000,5"),
                 ("de", "minus 1000 Komma null 05", "-1000,005"),
                 ("en", "3 cars and 25 boats", "3 cars and 25 boats")]
        for lang, phrase, expected in cases:
            for styled in (False, True):
                with self.subTest(phrase=phrase, styled=styled):
                    self.assertEqual(render(*phrase.split(), language=lang, styled=styled)[0], expected)


if __name__ == "__main__":
    unittest.main()
