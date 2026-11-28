"""The subtitle's spoken words remain intact when a keyword gets an icon."""
import hashlib
import json
import math
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
from render_clip import build_ass, build_cmd  # pyright: ignore[reportMissingImports]
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
    @unittest.skipUnless(shutil.which('ffmpeg') and find_spec('PIL'), 'FFmpeg and Pillow required')
    def test_reviewed_pop_out_is_visible_on_the_first_frame_at_its_anchor(self):
        from PIL import Image

        # Source word at 10.20 maps to precisely frame 6 of the cut at 30 fps.
        words = [{'w': 'bike', 's': 10.2, 'e': 10.45}]
        clip = {'id': 'clip01', 'segments': [{'start': 10, 'end': 11}],
                'emoji_popouts': [{'segment': 0, 'start': 10.2, 'word': 'bike', 'emoji': '🏍️'}]}
        config = {'width': 270, 'height': 480, 'fps': 30}
        cues = []
        build_ass(clip, words, config, None, emoji_cues=cues)
        self.assertAlmostEqual(cues[0].start, .2)
        width, height, _, _ = caption_emoji.overlay_geometry(config)
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'alpha.mov'
            caption_emoji.render_overlay('ffmpeg', cues, config, out, out.parent,
                                         sprites={'🏍️': Image.new('RGBA', (100, 100), (255, 60, 0, 255))})
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out), '-frames:v', '8',
                                           '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'])
        frame_bytes = width * height * 4
        self.assertEqual(max(raw[5 * frame_bytes + 3:6 * frame_bytes:4]), 0)
        self.assertEqual(max(raw[6 * frame_bytes + 3:7 * frame_bytes:4]), 255)

    @unittest.skipUnless(shutil.which('ffmpeg') and find_spec('PIL'), 'FFmpeg and Pillow required')
    def test_short_reviewed_cue_at_low_fps_is_opaque_on_its_first_frame(self):
        from PIL import Image

        config = {'width': 270, 'height': 480, 'fps': 10}
        cue = caption_emoji.Cue('🏍️', .101, .222, 540, 0, 1, onset_visible=True)
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'alpha.mov'
            caption_emoji.render_overlay('ffmpeg', [cue], config, out, out.parent,
                                         sprites={cue.glyph: Image.new('RGBA', (100, 100), (255, 0, 0, 255))})
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out), '-pix_fmt', 'rgba',
                                           '-f', 'rawvideo', 'pipe:1'])
        w, h, _, _ = caption_emoji.overlay_geometry(config)
        stride = w * h * 4
        self.assertEqual(max(raw[stride + 3:2 * stride:4]), 0)
        self.assertEqual(max(raw[2 * stride + 3:3 * stride:4]), 255)

    @unittest.skipUnless(shutil.which('ffmpeg') and find_spec('PIL'), 'FFmpeg and Pillow required')
    def test_reviewed_emoji_in_final_mp4_at_frame_after_a_jump_cut(self):
        from PIL import Image

        w, h, fps = 270, 480, 30
        config = {'width': w, 'height': h, 'fps': fps, 'loudnorm': 'I=-16:TP=-1.5:LRA=11'}
        # First cut ends between video frames. The first output frame at or after
        # the word in segment 2 must be frame 12, including with frame-rounded cuts.
        clip = {'segments': [{'start': 0, 'end': .24}, {'start': 1, 'end': 1.6}],
                'emoji_popouts': [{'segment': 1, 'start': 1.16, 'word': 'bike', 'emoji': '🏍️'}]}
        cues = []
        build_ass(clip, [{'w': 'bike', 's': 1.16, 'e': 1.46}], config, None, emoji_cues=cues)
        self.assertEqual(math.ceil(cues[0].start * fps - 1e-9), 12)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            source, overlay, ass, out = [p / n for n in ('source.mp4', 'overlay.mov', 'text.ass', 'out.mp4')]
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c=black:s={w}x{h}:r={fps}:d=2',
                            '-f', 'lavfi', '-i', 'sine=f=500:r=48000:d=2', '-c:v', 'libx264', '-c:a', 'aac',
                            str(source)], check=True)
            ass.write_text(build_ass(clip, [], config, None)[0], encoding='utf-8')
            caption_emoji.render_overlay('ffmpeg', cues, config, overlay, p,
                                         sprites={'🏍️': Image.new('RGBA', (100, 100), (255, 0, 0, 255))})
            sections = [{'start': 0, 'end': 2, 'file_offset': 0, 'file': str(source)}]
            subprocess.run(build_cmd('ffmpeg', clip, sections, ass, out, 'crop', config, None, None,
                                     'libx264', overlay), check=True, capture_output=True)
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out), '-frames:v', '14',
                                           '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
        stride = w * h * 3
        red = [max(raw[i * stride:(i + 1) * stride:3]) for i in (11, 12, 13)]
        self.assertLess(red[0], 30, red)
        self.assertGreater(red[1], 180, red)

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

    def test_reviewed_cache_rebuilds_when_short_word_becomes_long_caption(self):
        import _lib  # pyright: ignore[reportMissingImports]

        plan = json.loads((self.work / "clips.json").read_text())["clips"][0]
        words = [{"w": "eins", "s": .1, "e": .22}, {"w": "Komma", "s": .22, "e": .36},
                 {"w": "12345678901234567890", "s": .36, "e": .82}]
        transcript = self.work / "transcripts" / "s0-1.json"
        data = json.loads(transcript.read_text())
        transcript.write_text(json.dumps({**data, "words": words, "language": "de"}))
        loaded = _lib.load_words(self.work, include_language=True)
        review = caption_emoji_plan.review_path(self.work, "clip01")
        review.write_text(json.dumps({"revision": caption_emoji_plan.revision(plan, loaded),
                                      "emoji_popouts": [{"segment": 0, "start": .1, "word": "eins", "emoji": "🚗"}]}))
        first = self.render(30, "--reviewed-emoji-only")["clips"][0]
        self.assertEqual(first["caption_emojis"], 1)
        self.assertIn("1,12345678901234567890", (self.render_dir / "clip01.ass").read_text())
        sidecar = Path(first["out"]).with_suffix(".json")
        before = json.loads(sidecar.read_text())
        # The old renderer keyed the reviewed token 'eins', not the merged caption.
        old_inputs = [1, before["render_input_sha256"], True, None, True, "reviewed-emoji-onset-v1"]
        old_key = hashlib.sha256(json.dumps(old_inputs).encode("utf-8")).hexdigest()
        sidecar.write_text(json.dumps({**before, "render_cache_key": old_key}))
        refreshed = self.render(30, "--reviewed-emoji-only")["clips"][0]
        self.assertEqual(refreshed["status"], "gerendert")
        self.assertEqual(refreshed["caption_emojis"], 1)
        self.assertEqual(self.render(30, "--reviewed-emoji-only")["clips"][0]["status"], "schon da")

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
