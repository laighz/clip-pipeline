"""The subtitle's spoken words remain intact when a keyword gets an icon."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.util import find_spec
from itertools import groupby
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_clip import build_ass  # pyright: ignore[reportMissingImports]
from caption_emoji import motion  # pyright: ignore[reportMissingImports]
import caption_emoji  # pyright: ignore[reportMissingImports]
import caption_emoji_plan  # pyright: ignore[reportMissingImports]


def render_words(*words, **config):
    records = [{"w": word, "s": i * .3, "e": (i + 1) * .3} for i, word in enumerate(words)]
    cues = []
    ass, duration, count = build_ass({"hook": "Test", "segments": [{"start": 0, "end": 5}]},
                                     records, {"caption_emoji_mode": "legacy", **config}, None, emoji_cues=cues)
    return ass, duration, count, cues


def caption_groups(ass):
    texts = [re.sub(r"\{[^}]*\}", "", line.split(",", 9)[-1]) for line in ass.splitlines()
             if ",PhraseCaption,," in line or ",Caption,," in line]
    return [text for text, _ in groupby(texts)]


class CaptionEmojiTest(unittest.TestCase):
    def test_keyword_adds_graphic_without_changing_words(self):
        ass, duration, count, cues = render_words("Das", "Auto,", "brennt!")
        self.assertEqual((duration, count), (5, 3))
        caption = [line for line in ass.splitlines() if ",PhraseCaption,," in line]
        self.assertTrue(caption)
        for line in caption:
            text = re.sub(r"\{[^}]*\}", "", line.split(",", 9)[-1])
            self.assertEqual(text.replace(r"\N", " "), "DAS AUTO, BRENNT!")
        self.assertEqual([c.glyph for c in cues], ["🚗", "🔥"])
        self.assertAlmostEqual(cues[1].start, .60)
        self.assertEqual(cues[0].start, cues[1].start)
        self.assertEqual(cues[0].end, cues[1].end)
        self.assertNotEqual(cues[0].x, cues[1].x)
        self.assertNotEqual(cues[0].side, cues[1].side)
        self.assertNotIn("🔥", ass)

    def test_exact_words_only(self):
        self.assertEqual(render_words("Automatisch", "geht", "das")[3], [])

    def test_disable_keeps_plain_subtitles(self):
        ass, _, _, cues = render_words("Auto", caption_emojis=False)
        self.assertIn("Auto", ass)
        self.assertIn(",Caption,,", ass)
        self.assertEqual(cues, [])

    def test_repeated_icon_is_not_shown_in_adjacent_groups(self):
        self.assertEqual([c.glyph for c in render_words("Polizei!", "Polizei!")[3]], ["🚨"])

    def test_caption_can_use_two_lines(self):
        ass, _, count, cues = render_words("Da", "kommt", "die", "Polizei!", "Das", "Auto")
        self.assertEqual(count, 6)
        self.assertIn(r"\N", ass)
        self.assertEqual(cues[0].lines, 2)
        self.assertLessEqual(cues[0].end, 1.2)

    def test_group_limit_does_not_split_a_prepositional_phrase(self):
        words = "Wir sind vorhin mit der Polizei geflüchtet.".split()
        ass, _, count, cues = render_words(*words)
        self.assertEqual(count, len(words))
        self.assertEqual([g.replace(r"\N", " ") for g in caption_groups(ass)],
                         ["WIR SIND VORHIN", "MIT DER POLIZEI GEFLÜCHTET."])
        self.assertAlmostEqual(cues[0].start, 1.5)

    def test_two_line_caption_keeps_the_preposition_with_its_phrase(self):
        ass, _, _, _ = render_words(*"Da wirklich mit der Polizei!".split())
        self.assertEqual(caption_groups(ass), [r"DA WIRKLICH\NMIT DER POLIZEI!"])

    def test_protected_phrase_does_not_override_a_pause_or_sentence_end(self):
        words = [{"w": "mit", "s": 0., "e": .3}, {"w": "der", "s": 1., "e": 1.3},
                 {"w": "Polizei!", "s": 1.3, "e": 1.6}, {"w": "Weiter!", "s": 1.6, "e": 1.9}]
        ass, _, count = build_ass({"hook": "Test", "segments": [{"start": 0, "end": 3}]},
                                  words, {}, None, emoji_cues=[])
        self.assertEqual(count, 4)
        self.assertEqual(caption_groups(ass), ["MIT", "DER POLIZEI!", "WEITER!"])

    def test_tight_word_limit_keeps_all_words_in_order(self):
        words = "Da vorne mit der Polizei".split()
        ass, _, _, cues = render_words(*words, caption_emojis=False, words_per_caption=2)
        groups = caption_groups(ass)
        self.assertTrue(all(len(g.split()) <= 2 for g in groups))
        self.assertEqual(" ".join(groups).split(), words)
        self.assertEqual(cues, [])

    def test_pop_out_varies_between_all_corners_but_rerender_is_stable(self):
        def pop(i):
            cues = []
            clip = {"id": f"clip{i}", "hook": "Test", "segments": [{"start": 0, "end": 3}]}
            build_ass(clip, [{"w": "Auto", "s": .2, "e": .5}], {"caption_emoji_mode": "legacy"}, None, emoji_cues=cues)
            return cues[0]

        self.assertEqual(pop(10), pop(10))
        corners = {(c.side, c.x > 540) for c in map(pop, range(32))}
        self.assertEqual(corners, {("above", True), ("above", False), ("below", True), ("below", False)})

    def test_longer_text_moves_origin_towards_the_caption_corners(self):
        short = render_words("Auto")[3][0]
        long = render_words("Dieses", "rote", "Auto")[3][0]
        self.assertGreater(abs(long.x - 540), abs(short.x - 540))

    def test_pop_out_and_burst_do_not_repeat_a_corner_in_the_next_animation(self):
        phrases = ("Auto!", "Na gut.", "Polizei!", "Video", "Geld!", "Handy", "Unfall!", "Karte!", "lacht!")
        words = [{"w": w, "s": i * .3, "e": (i + 1) * .3} for i, w in enumerate(phrases)]
        for i in range(32):
            with self.subTest(clip=i):
                clip = {"id": f"clip{i}", "hook": "Test", "segments": [{"start": 0, "end": 4}]}
                cues = []
                build_ass(clip, words, {"caption_emoji_mode": "legacy"}, None, emoji_cues=cues)
                corners = [{(c.side, c.x > 540) for c in event} for _, event in groupby(cues, key=lambda c: c.start)]
                self.assertEqual([len(event) for event in corners], [1, 1, 2, 2, 1, 1])
                for previous, current in zip(corners, corners[1:]):
                    self.assertTrue(previous.isdisjoint(current), (previous, current))

    def test_pop_out_does_not_repeat_the_corner_across_a_clip_cut(self):
        words = [{"w": "Auto", "s": 1.2, "e": 1.5}, {"w": "Feuer!", "s": 10.3, "e": 10.6}]
        for i in range(32):
            clip = {"id": f"clip{i}", "hook": "Test", "segments": [{"start": 10, "end": 11}, {"start": 1, "end": 3}]}
            cues = []
            build_ass(clip, words, {"caption_emoji_mode": "legacy"}, None, emoji_cues=cues)
            self.assertNotEqual((cues[0].side, cues[0].x > 540), (cues[1].side, cues[1].x > 540))

    def test_icon_cues_follow_clip_order_not_vod_time(self):
        cues = []
        clip = {"hook": "Test", "segments": [{"start": 10, "end": 11}, {"start": 1, "end": 3}]}
        words = [{"w": "Auto", "s": 1.2, "e": 1.5}, {"w": "Feuer!", "s": 10.3, "e": 10.6}]
        build_ass(clip, words, {"caption_emoji_mode": "legacy"}, None, emoji_cues=cues)
        self.assertEqual([c.glyph for c in cues], ["🔥", "🚗"])
        self.assertAlmostEqual(cues[0].start, .3)
        self.assertAlmostEqual(cues[1].start, 1.2)

    def test_motion_starts_invisible_and_remains_quick(self):
        self.assertEqual(motion(0, .7)[1:], (0., 0.))
        self.assertEqual(motion(.09, .7)[1], 1.)
        self.assertLess(motion(.66, .7)[1], .5)
        self.assertEqual(motion(.7, .7)[1:], (0., 0.))

    def test_spring_overshoots_then_settles_while_still_drifting_out(self):
        scale = [motion(i / 1000, .7)[0] for i in range(701)]
        self.assertGreater(max(scale), 1.03)
        self.assertLess(max(scale), 1.10)
        self.assertAlmostEqual(motion(.53, .7)[0], 1., places=2)
        self.assertGreater(motion(.64, .7)[2], motion(.53, .7)[2])

    def test_quick_words_still_get_a_complete_pop(self):
        duration = .12
        for i in range(121):
            s, opacity, travel = motion(i / 1000, duration)
            self.assertTrue(0 <= s <= 1.10 and 0 <= opacity <= 1 and 0 <= travel <= 1.10)
        self.assertEqual(motion(.06, duration)[1], 1.)
        self.assertGreater(motion(.06, duration)[0], .95)

    def test_emoji_settles_at_110_pixels_in_a_1080p_render(self):
        cue = caption_emoji.pop_out_cues(["🔥"], 0, .7, ["FEUER!"], "size", set())[0]
        pose = caption_emoji.sprite_pose(cue, .4, 1., 1., 0, 0, (100, 200))
        self.assertIsNotNone(pose)
        assert pose is not None
        self.assertEqual(pose[2:4], (55, 110))

    @unittest.skipUnless(find_spec("PIL"), "Pillow is needed for sprite compositing")
    def test_moving_sprite_leaves_a_soft_trail_without_dark_edges(self):
        from PIL import Image

        sprite = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        layer, origin = caption_emoji.sample_sprite(sprite, [(0, 0, 10, 10, 255), (4, 2, 10, 10, 255)])
        self.assertEqual(origin, (0, 0))
        self.assertEqual(layer.size, (14, 12))
        self.assertEqual(layer.getpixel((6, 4)), (255, 0, 0, 255))
        self.assertEqual(layer.getpixel((1, 1)), (255, 0, 0, 127))

    @unittest.skipUnless(find_spec("PIL"), "Pillow is needed for sprite compositing")
    def test_still_sprite_stays_sharp_and_hidden_samples_do_not_add_pixels(self):
        from PIL import Image

        sprite = Image.new("RGBA", (6, 6), (60, 120, 220, 255))
        pose = (5, 7, 6, 6, 255)
        layer, origin = caption_emoji.sample_sprite(sprite, [pose] * 5)
        self.assertEqual(origin, (5, 7))
        self.assertEqual(layer.tobytes(), sprite.tobytes())
        partial, _ = caption_emoji.sample_sprite(sprite, [None, None, pose])
        self.assertLessEqual(partial.getpixel((2, 2))[3], 85)


@unittest.skipUnless(caption_emoji.apple_ready() and find_spec("PIL") and find_spec("yaml")
                     and shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Render regressions need macOS, Swift, Pillow, PyYAML, ffmpeg and ffprobe")
class CaptionEmojiRenderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.work = self.home / "work" / "emoji-test"
        self.render_dir = self.work / "render"
        (self.work / "transcripts").mkdir(parents=True)
        source = self.home / "source.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=144x256:r=30",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264",
                        "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=15)
        clip = {"id": "clip01", "hook": "Test", "segments": [{"start": 0, "end": 1}]}
        (self.work / "clips.json").write_text(json.dumps({"clips": [clip]}), encoding="utf-8")
        (self.work / "sections.json").write_text(json.dumps({"sections": [
            {"id": "s0-1", "start": 0, "end": 1, "file_offset": 0, "file": str(source)},
        ]}), encoding="utf-8")
        words = [{"w": "Auto", "s": .1, "e": .4}]
        (self.work / "transcripts" / "s0-1.json").write_text(json.dumps({"words": words}), encoding="utf-8")
        review = caption_emoji_plan.review_path(self.work, "clip01")
        review.parent.mkdir()
        review.write_text(json.dumps({"revision": caption_emoji_plan.revision(clip, words),
                                      "emoji_popouts": [{"segment": 0, "start": .1, "word": "Auto", "emoji": "🚗"}]}))

    def render(self, fps, *flags, mode="reviewed"):
        (self.home / "streamers.yaml").write_text(
            f"render:\n  width: 144\n  height: 256\n  layout: center\n  fps: {fps}\n  caption_emoji_mode: {mode}\n",
            encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "scripts" / "render_clip.py"
        run = subprocess.run([sys.executable, str(script), "--vod-id", "emoji-test", "--encoder", "x264", *flags],
                             env={**os.environ, "CLIPS_HOME": str(self.home)}, capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        result = json.loads(run.stdout)
        self.assertTrue(Path(result["clips"][0]["out"]).is_file())
        return result

    def test_rational_fps_renders_emojis_at_the_configured_rate(self):
        result = self.render("30000/1001")
        clip = result["clips"][0]
        self.assertEqual(clip["caption_emojis"], 1)
        rate = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                        "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", clip["out"]],
                                       text=True, timeout=15)
        self.assertEqual(rate.strip(), "30000/1001")

    def test_fps_unsupported_by_emoji_renderer_falls_back_with_diagnostic(self):
        result = self.render("ntsc")  # FFmpeg accepts this alias; the optional renderer needn't.
        self.assertEqual(result["clips"][0]["caption_emojis"], 0)
        self.assertTrue(any("render.fps" in w and "ntsc" in w for w in result["warnings"]), result["warnings"])
        self.assertIn(",Caption,,", (self.render_dir / "clip01.ass").read_text(encoding="utf-8"))

    def test_recut_keeps_anchor_but_drops_stale_review(self):
        self.assertEqual(self.render(30, "--reviewed-emoji-only")["clips"][0]["caption_emojis"], 1)
        self.assertEqual(self.render(30, "--reviewed-emoji-only")["clips"][0]["status"], "schon da")
        plan = self.work / "clips.json"
        data = json.loads(plan.read_text())
        data["clips"][0]["segments"][0]["start"] = .05
        plan.write_text(json.dumps(data))
        result = self.render(30, "--reviewed-emoji-only")
        self.assertEqual(result["clips"][0]["caption_emojis"], 0)
        self.assertTrue(any("seit dem Emoji-Review geändert" in w for w in result["warnings"]))

    def test_explicit_empty_review_invalidates_legacy_emoji_render(self):
        review = caption_emoji_plan.review_path(self.work, "clip01")
        receipt = json.loads(review.read_text())
        review.unlink()
        self.assertEqual(self.render(30, mode="legacy")["clips"][0]["caption_emojis"], 1)
        receipt["emoji_popouts"] = []
        review.write_text(json.dumps(receipt))
        clip = self.render(30, mode="legacy")["clips"][0]
        self.assertEqual((clip["status"], clip["caption_emojis"]), ("gerendert", 0))

    def test_directory_at_overlay_path_is_left_untouched(self):
        collision = self.render_dir / "clip01.emoji.mov"
        collision.mkdir(parents=True)
        marker = collision / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        result = self.render(30)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(result["clips"][0]["caption_emojis"], 0)
        self.assertIn(",Caption,,", (self.render_dir / "clip01.ass").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
