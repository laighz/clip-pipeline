"""The offline emoji catalog is available without enabling ambiguous triggers."""
import shutil
import sys
import tempfile
import unittest
import zlib
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from render_clip import build_ass  # pyright: ignore[reportMissingImports]


def render(*tokens, unavailable_glyphs=None, candidate_glyphs=None, **rconf):
    words = [{"w": t, "s": i * .25, "e": i * .25 + .23} for i, t in enumerate(tokens)]
    cues = []
    options = {k: v for k, v in {"unavailable_glyphs": unavailable_glyphs,
                                  "candidate_glyphs": candidate_glyphs}.items() if v is not None}
    ass, _, _ = build_ass({"id": "x", "segments": [{"start": 0, "end": 4}]}, words,
                         {"caption_emoji_mode": "legacy", **rconf}, None,
                         emoji_cues=cues, **options)
    return ass, cues


class EmojiCatalogTest(unittest.TestCase):
    def test_all_catalog_entries_are_searchable(self):
        catalog = import_module("emoji_catalog")
        rows = catalog.entries()
        self.assertEqual(len(rows), 3972)
        self.assertEqual(len({row["emoji"] for row in rows}), 3972)
        for value, expected in [("👍🏽", "👍🏽"), ("Biber", "🦫"), ("World Map", "🗺️")]:
            hits = catalog.search(value)
            self.assertEqual(hits[0]["emoji"], expected)
            self.assertTrue(hits[0]["use_clip_de"] and hits[0]["use_alternative_de"])

    def test_catalog_curated_match_runs_at_last_word_even_across_caption_groups(self):
        ass, cues = render("Lass", "uns", "schau", "genau", "hin!", emoji_words_per_caption=2)
        self.assertEqual([(c.glyph, c.start) for c in cues], [("👀", 1.0)])
        self.assertNotIn("👀", ass)
        self.assertIn("HIN!", ass)

    def test_curated_new_motifs_and_trigger_safety(self):
        self.assertEqual([c.glyph for c in render("Hier", "kommt", "der", "Lachflash!")[1]], ["😂"])
        self.assertEqual([c.glyph for c in render("Überraschung", "Jumpscare!")[1]], ["😱"])
        self.assertEqual(render("Fünf", "Sterne", "und", "eine", "Nachricht")[1], [])
        self.assertEqual(render("Schau", "genau", "hin", caption_emojis=False)[1], [])

    def test_full_catalog_uses_neutral_exact_concrete_names_not_bare_keywords(self):
        self.assertEqual(render("Roter", "Panda!")[1], [])
        self.assertEqual([c.glyph for c in render("See", "Otter!")[1]], [])
        self.assertEqual([c.glyph for c in render("Wir", "sehen", "ein", "Alpaka!")[1]], [])
        self.assertEqual([c.glyph for c in render("Fliegende", "Untertasse!")[1]], ["🛸"])
        self.assertEqual(render("Fliegende", "Untertasse!", caption_emoji_catalog=False)[1], [])
        self.assertEqual(render("Flagge", "Japan", "mittlere", "Hautfarbe")[1], [])

    def test_phrase_does_not_cross_sentences(self):
        _, cues = render("Schau!", "Genau", "hin.")
        self.assertEqual([(c.glyph, c.start) for c in cues], [("👀", 0.)])
        self.assertEqual([(c.glyph, c.start) for c in render("Schau", ".Genau", "hin.")[1]], [("👀", 0.)])

    def test_flags_are_lookup_only_even_with_curated_triggers(self):
        for enabled in (True, False):
            self.assertEqual(render("Ziellinie", "und", "finish", "line", caption_emoji_catalog=enabled)[1], [])

    def test_unsupported_glyph_consumes_neither_a_slot_nor_burst_timing(self):
        candidates = set()
        _, cues = render("Saure", "Gurke", "Auto", "Geld", unavailable_glyphs={"🫝"}, candidate_glyphs=candidates)
        self.assertEqual(candidates, {"🫝", "🚗", "💵"})
        self.assertEqual([c.glyph for c in cues], ["🚗", "💵"])
        _, cues = render("Auto", "saure", "Gurke", unavailable_glyphs={"🫝"})
        self.assertEqual([(c.glyph, c.start) for c in cues], [("🚗", 0.)])

    def test_missing_longest_phrase_falls_back_to_the_supported_shorter_match(self):
        candidates = set()
        _, cues = render("Auto", "Rickshaw", candidate_glyphs=candidates)
        self.assertEqual([c.glyph for c in cues], ["🛺"])
        self.assertEqual(candidates, {"🛺", "🚗"})  # Includes the alternate for Apple preflight.
        _, cues = render("Auto", "Rickshaw", unavailable_glyphs={"🛺"})
        self.assertEqual([(c.glyph, c.start) for c in cues], [("🚗", 0.)])

    def test_phrase_does_not_cross_cut_pause_or_transcript_language(self):
        catalog = import_module("emoji_catalog")
        for start, end, bounds, lang in [(.4, .6, (.1, 1.), "de"),
                                         (1., 1.2, (0., 2.), "de"),
                                         (.4, .6, (0., 2.), "en")]:
            with self.subTest(start=start, bounds=bounds, lang=lang):
                words = [{"w": "Fliegende", "s": .0, "e": .3},
                         {"w": "Untertasse", "s": start, "e": end, "transcript_language": lang}]
                self.assertEqual(list(catalog.hits(words, {}, catalog_enabled=True,
                                                   bounds=bounds, default_language="de")), [])

    def test_missing_catalog_retains_curated_triggers(self):
        catalog = import_module("emoji_catalog")
        catalog.trigger_index.cache_clear()
        try:
            for failure in (FileNotFoundError, zlib.error):
                with patch.object(catalog, "exact_names", side_effect=failure), self.assertLogs("emoji_catalog"):
                    self.assertEqual([c.glyph for c in render("Lachflash!")[1]], ["😂"])
                catalog.trigger_index.cache_clear()
        finally:
            catalog.trigger_index.cache_clear()

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "requires Apple Color Emoji")
    def test_unsupported_glyph_does_not_break_apple_sprite_batch(self):
        ce = import_module("caption_emoji")
        with tempfile.TemporaryDirectory() as tmp:
            sprites = ce.load_apple_sprites(["🦫", "\U0010ffff"], SCRIPTS.parent / "assets", Path(tmp))
        self.assertEqual(set(sprites), {"🦫"})

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift") and shutil.which("ffmpeg"),
                         "requires FFmpeg and Apple Color Emoji")
    def test_overlay_keeps_supported_motifs_when_one_glyph_is_missing(self):
        ce = import_module("caption_emoji")
        cues = [ce.Cue("🦫", .0, .4, 540, 0, 1), ce.Cue("\U0010ffff", .2, .5, 540, 3, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "overlay.mov"
            kept = ce.render_overlay("ffmpeg", cues, {"width": 270, "height": 480, "fps": 30},
                                     out, SCRIPTS.parent / "assets")
            self.assertGreater(out.stat().st_size, 0)
        self.assertEqual(kept, cues[:1])
