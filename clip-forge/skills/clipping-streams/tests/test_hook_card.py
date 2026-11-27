"""Opening card: appearance, wrapping and removal at the configured time."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@unittest.skipUnless(find_spec("PIL"), "Pillow is needed to draw the title card")
class HookCardTest(unittest.TestCase):
    def test_white_rounded_card_with_black_text(self):
        from hook_card import make_card  # pyright: ignore[reportMissingImports]

        card = make_card("How to add a hook to your TikTok video")
        self.assertLessEqual(card.width, 900)
        self.assertGreater(card.height, 130)  # Two lines.
        self.assertEqual(card.getpixel((0, 0))[3], 0)
        self.assertEqual(card.getpixel((card.width // 2, 6)), (255, 255, 255, 255))
        self.assertIn((0, 0, 0, 255), {color for _, color in card.getcolors(4096) or []})

    def test_emoji_is_optional_and_only_one_is_used(self):
        from hook_card import title_parts  # pyright: ignore[reportMissingImports]

        self.assertEqual(title_parts("🔥 Das Auto brennt 🚗"), ("Das Auto brennt", "🔥"))
        self.assertEqual(title_parts("Das Auto brennt", emojis=False), ("Das Auto brennt", None))
        self.assertEqual(title_parts("Was ist hier passiert?"), ("Was ist hier passiert?", None))

    def test_title_has_bold_stems(self):
        from hook_card import make_card  # pyright: ignore[reportMissingImports]

        card = make_card("H")
        row = card.height // 2 - 10  # Above the crossbar: measure just the vertical stems.
        ink = sum(card.getpixel((x, row)) == (0, 0, 0, 255) for x in range(card.width))
        self.assertGreaterEqual(ink, 10)

    def test_bundled_font_does_not_draw_missing_glyphs(self):
        from hook_card import make_card  # pyright: ignore[reportMissingImports]

        with patch.object(Path, "is_file", return_value=False):
            for text in ("Alert 警報", "漢字", "日本"):
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, "glyphs"):
                    make_card(text)


@unittest.skipUnless(find_spec("yaml"), "PyYAML is needed to build ASS")
class HookAssTest(unittest.TestCase):
    def test_empty_hook_does_not_burn_upload_title(self):
        from render_clip import build_ass  # pyright: ignore[reportMissingImports]

        for hook_fields in ({}, {"hook": ""}, {"hook": " \t"}):
            with self.subTest(hook_fields=hook_fields):
                ass, _, _ = build_ass({"title": "Upload title", "segments": [{"start": 0, "end": 2}],
                                       **hook_fields}, [], {}, None)
                self.assertNotIn(",Hook,,", ass)


@unittest.skipUnless(find_spec("PIL") and find_spec("yaml") and shutil.which("ffmpeg"),
                     "Render test needs Pillow, PyYAML and ffmpeg")
class HookCardRenderTest(unittest.TestCase):
    def test_opening_hook_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            work = home / "work" / "hook-test"
            work.mkdir(parents=True)
            source = home / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=270x480:r=30",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264",
                            "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=20)
            clip = {"id": "clip01", "hook": "Das war knapp", "title": "Nicht dieser Upload-Titel",
                    "segments": [{"start": 0, "end": 2}]}
            clips = [clip, {**clip, "id": "clip02", "hook": ""}, {**clip, "id": "clip03", "hook": "警報"}]
            (work / "clips.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")
            (work / "sections.json").write_text(json.dumps({"sections": [{"id": "s0-2", "start": 0, "end": 2,
                "file_offset": 0, "file": str(source)}]}), encoding="utf-8")
            (home / "streamers.yaml").write_text("render: {width: 270, height: 480, layout: center, hook_duration_s: 1, caption_emojis: false}\n")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "render_clip.py"), "--vod-id", "hook-test", "--encoder", "x264"],
                                  env={**os.environ, "CLIPS_HOME": tmp}, check=True, capture_output=True, text=True, timeout=30)
            result = json.loads(proc.stdout)
            outputs = {clip["id"]: clip["out"] for clip in result["clips"]}

            def pixel_at(output, at):
                return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(at), "-i", output, "-frames:v", "1",
                                                "-vf", "format=rgb24,crop=1:1:135:132", "-f", "rawvideo", "-"], timeout=15)

            with self.subTest("card is visible on the first frame and disappears on time"):
                self.assertGreater(min(pixel_at(outputs["clip01"], 0)), 235)
                self.assertLess(max(pixel_at(outputs["clip01"], 1.2)), 20)
                self.assertNotIn(",Hook,,", (work / "render" / "clip01.ass").read_text(encoding="utf-8"))
            with self.subTest("empty hook leaves the opening frame clean"):
                self.assertLess(max(pixel_at(outputs["clip02"], 0)), 20)
            with self.subTest("unsupported glyphs pass the original hook to ASS"):
                self.assertIn(",Hook,,0,0,0,,警報", (work / "render" / "clip03.ass").read_text(encoding="utf-8"))
                self.assertTrue(any(w.startswith("clip03:") and "ASS-Hook" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
