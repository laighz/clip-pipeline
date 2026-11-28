"""Local emoji suggestions extend the vocabulary without authorizing overlays."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import emoji_catalog  # pyright: ignore[reportMissingImports]
from render_clip import build_ass  # pyright: ignore[reportMissingImports]
from review_caption_emoji import review  # pyright: ignore[reportMissingImports]


class EmojiSuggestionsTest(unittest.TestCase):
    def test_compound_vocabulary_works_across_catalog_motifs(self):
        for text, glyph in (("Weihnachtsbaumkönig.", "🎄"), ("WEIHNACHTSBAUM-KÖNIGIN!", "🎄"),
                            ("Elektromotorrad", "🏍️"), ("Skateboardrampe", "🛹"),
                            ("Feuerlo\u0308scherhalterung", "🧯"), ("Autohaus", "🚗"),
                            ("Auto-Werkstatt", "🚗"), ("Biberbau", "🦫")):
            with self.subTest(text=text):
                self.assertEqual(emoji_catalog.suggest_word(text)[0], glyph)
        self.assertIn("🏍️", emoji_catalog.suggest_word("motorbike!"))
        self.assertIn("🎮", emoji_catalog.suggest_word("Videospiel"))

    def test_short_accidental_fragments_are_not_compounds(self):
        for word, glyph in (("automatisch", "🚗"), ("Kameraden", "🎥"), ("Eiskalt", "🧊"),
                            ("Panikattacke", "🦈")):
            with self.subTest(word=word):
                self.assertNotIn(glyph, emoji_catalog.suggest_word(word))
        self.assertEqual(emoji_catalog.suggest_word("in"), [])

    def test_suggestion_attaches_to_the_real_occurrence_without_enabling_it(self):
        clip = {"id": "clip01", "segments": [{"start": 0, "end": 8}]}
        words = [{"w": "Weihnachtsbaumkönig.", "s": 3.25, "e": 4.29}]
        with tempfile.TemporaryDirectory() as dir_name:
            work = Path(dir_name)
            (work / "transcripts").mkdir()
            (work / "transcripts" / "s0-8.json").write_text(json.dumps({"words": words}), encoding="utf-8")
            (work / "clips.json").write_text(json.dumps({"clips": [clip]}), encoding="utf-8")
            context = review(work, "clip01", describe=True)
            self.assertEqual(context["emoji_suggestions"], [{"id": 0, "emojis": ["🎄"]}])
            self.assertFalse((work / "emoji-reviews" / "clip01.json").exists())
            cues = []
            build_ass(clip, words, {}, None, emoji_cues=cues)
            self.assertEqual(cues, [])
            decision = {"revision": context["revision"], "choices": [{"id": 0, "emoji": "🎄"}]}
            receipt = review(work, "clip01", decision=json.dumps(decision))
            self.assertEqual(receipt["emoji_popouts"], [{"segment": 0, "start": 3.25,
                                                       "word": "Weihnachtsbaumkönig.", "emoji": "🎄"}])
            ass, _, _ = build_ass(clip | receipt, words, {}, None, emoji_cues=cues)
            self.assertEqual([(c.start, c.glyph) for c in cues], [(3.25, "🎄")])
            self.assertIn("WEIHNACHTSBAUMKÖNIG.", ass)
            with patch("review_caption_emoji.emoji_catalog.suggest_word", side_effect=FileNotFoundError):
                result = review(work, "clip01", describe=True)
            self.assertEqual(result["revision"], context["revision"])
            self.assertEqual(result["words"], context["words"])
            self.assertEqual(result["emoji_suggestions"], [])
