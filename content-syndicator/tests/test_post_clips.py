"""Offline-Tests: Captions stehen in der TXT neben der MP4."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from postclips.browser import publish
from postclips.library import (
    PLATFORM_ORDER,
    discover_clips,
    load_sidecar,
    plan_posts,
    prepare_upload_music,
    record_db,
    run_posts,
    save_sidecar,
)
from postclips.text import platform_texts, read_caption_file


SAMPLE = """TITEL: Er kann es nicht fassen #Shorts

--- YOUTUBE ---
Der Moment, als es kippt. Clip: @kanal auf Twitch #Shorts #twitch

--- TIKTOK ---
Der Moment, als es kippt. Clip: @kanal auf Twitch #fyp
https://twitch.tv/kanal

--- INSTAGRAM ---
Der Moment, als es kippt. Clip: @kanal auf Twitch #reels

--- KOMMENTAR-SKRIPT (Voice-over) ---
Kurz vorher war alles ruhig. Dann kippt es.
"""


class CaptionTests(unittest.TestCase):
    def test_sections_come_from_the_txt(self) -> None:
        raw = read_caption_file(SAMPLE)
        self.assertEqual(raw["title"], "Er kann es nicht fassen #Shorts")
        self.assertEqual(
            raw["youtube"],
            "Der Moment, als es kippt. Clip: @kanal auf Twitch #Shorts #twitch",
        )
        self.assertIn("#fyp", raw["tiktok"])
        self.assertIn("#reels", raw["instagram"])
        self.assertNotIn("voice", raw)
        self.assertNotIn("Kurz vorher", json.dumps(raw))

    def test_plain_txt_is_the_caption(self) -> None:
        raw = read_caption_file("Nur dieser Satz. Clip: @kanal auf Twitch\n")
        self.assertEqual(raw["youtube"], "Nur dieser Satz. Clip: @kanal auf Twitch")
        self.assertEqual(raw["tiktok"], raw["youtube"])
        self.assertEqual(raw["instagram"], raw["youtube"])
        self.assertEqual(raw["title"], "Nur dieser Satz. Clip: @kanal auf Twitch")

    def test_multiline_section_keeps_line_breaks(self) -> None:
        text = "TITEL: T\n\n--- TIKTOK ---\nZeile eins\nZeile zwei\n\n--- YOUTUBE ---\nYT\n"
        raw = read_caption_file(text)
        self.assertEqual(raw["tiktok"], "Zeile eins\nZeile zwei")
        self.assertEqual(raw["youtube"], "YT")

    def test_limits_reuse_the_written_caption(self) -> None:
        texts = platform_texts(read_caption_file(SAMPLE))
        self.assertEqual(texts["youtube_title"], "Er kann es nicht fassen #Shorts")
        self.assertTrue(texts["youtube_description"].startswith("Der Moment, als es kippt."))
        self.assertEqual(texts["tiktok"], read_caption_file(SAMPLE)["tiktok"])
        self.assertEqual(texts["instagram"], read_caption_file(SAMPLE)["instagram"])
        self.assertNotIn("http", texts["x"])
        self.assertLessEqual(len(texts["x"]), 280)
        self.assertLessEqual(len(texts["snapchat"]), 160)
        self.assertTrue(texts["snapchat"].startswith("Der Moment"))
        self.assertNotIn("Kurz vorher", texts["x"])
        self.assertNotIn("Kurz vorher", texts["snapchat"])

    def test_long_title_is_cut_at_100(self) -> None:
        title = "Wort " * 40
        texts = platform_texts({"title": title.strip(), "youtube": "y", "tiktok": "t", "instagram": "i"})
        self.assertLessEqual(len(texts["youtube_title"]), 100)
        self.assertTrue(texts["youtube_title"].startswith("Wort"))


class DiscoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.out = self.home / "out" / "vod1"
        self.out.mkdir(parents=True)
        (self.out / "clip01.mp4").write_bytes(b"fake")
        (self.out / "clip01.txt").write_text(SAMPLE, encoding="utf-8")
        (self.out / "clip02.mp4").write_bytes(b"fake")
        (self.out / "clip02.txt").write_text("Fertige Caption ohne Abschnitte\n", encoding="utf-8")
        (self.out / "clip02.json").write_text(json.dumps({"duration_s": 75}), encoding="utf-8")
        (self.out / "notes.txt").write_text("kein clip", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pairs_mp4_with_its_txt(self) -> None:
        clips = discover_clips(self.home, "vod1")
        self.assertEqual([c.clip_id for c in clips], ["clip01", "clip02"])
        self.assertIn("#Shorts", clips[0].texts["youtube_description"])
        self.assertEqual(clips[1].texts["tiktok"], "Fertige Caption ohne Abschnitte")
        self.assertEqual(clips[1].duration_s, 75)
        self.assertIsNone(clips[0].duration_s)

    def test_marked_music_master_cannot_upload_without_music_sidecar(self) -> None:
        (self.out / "clip01.json").write_text(
            json.dumps({"duration_s": 12, "trend_music_upload": True}), encoding="utf-8")
        clips = discover_clips(self.home, "vod1", {"clip01"})
        with self.assertRaisesRegex(ValueError, "clip01.music.json fehlt"):
            prepare_upload_music(clips, ["youtube"])
        poster = Mock()
        self.assertEqual(run_posts(clips, ["youtube"], poster).failed, 1)
        poster.assert_not_called()

    def test_snapchat_skipped_when_longer_than_60s(self) -> None:
        clips = discover_clips(self.home, "vod1")
        jobs = plan_posts(clips, PLATFORM_ORDER)
        snap = [j for j in jobs if j.clip_id == "clip02" and j.platform == "snapchat"]
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0].action, "skip")
        self.assertIn("60", snap[0].reason)
        posted = [j for j in jobs if j.clip_id == "clip02" and j.action == "post"]
        self.assertEqual([j.platform for j in posted], ["youtube", "tiktok", "instagram", "x"])

    def test_missing_txt_is_an_input_error(self) -> None:
        (self.out / "clip03.mp4").write_bytes(b"x")
        with self.assertRaises(ValueError):
            discover_clips(self.home, "vod1")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.out = self.home / "out" / "vod1"
        self.out.mkdir(parents=True)
        (self.out / "clip01.mp4").write_bytes(b"fake")
        (self.out / "clip01.txt").write_text(SAMPLE, encoding="utf-8")
        (self.out / "clip02.mp4").write_bytes(b"fake")
        (self.out / "clip02.txt").write_text(SAMPLE, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_trend_track_is_mixed_once_for_pending_browser_uploads(self) -> None:
        master = self.out / "clip01.mp4"
        master.with_suffix(".music.json").write_text("{}", encoding="utf-8")
        save_sidecar(self.out / "clip01.post.json", {"posts": {"youtube": {"status": "posted"}}})
        settings = {"master_sha256": "0123456789"}
        track = SimpleNamespace(title="Song", artist="Artist", source_platform="tiktok",
                                checked_on=date(2026, 11, 19))
        mixed = self.home / "state" / "upload-music" / "mixed.mp4"
        playwright = MagicMock()
        runtime = playwright.sync_playwright.return_value.__enter__.return_value
        context = Mock(pages=[Mock()])
        runtime.chromium.launch_persistent_context.return_value = context
        with (patch.dict(sys.modules, {"playwright.sync_api": playwright}),
              patch("postclips.browser.find_browser", return_value=None),
              patch("postclips.library.music.read_settings", return_value=settings) as read,
              patch("postclips.library.music.select", return_value=track) as select,
              patch("postclips.library.music.mix_for_upload", return_value=mixed) as mix,
              patch("postclips.browser.Session.post") as post):
            results = publish(self.home, "vod1", ["youtube", "tiktok", "instagram"], None, True, 0)
        read.assert_called_once_with(master)
        select.assert_called_once_with(settings, ["tiktok", "instagram"])
        mix.assert_called_once_with(master, track, mixed.parent, master_sha256=settings["master_sha256"])
        sent = [(call.args[0].clip_id, call.args[0].platform, call.args[0].video) for call in post.call_args_list]
        self.assertEqual(sent[:2], [("clip01", "tiktok", mixed), ("clip01", "instagram", mixed)])
        self.assertEqual([entry[2] for entry in sent[2:]], [self.out / "clip02.mp4"] * 3)
        self.assertEqual((results.posted, results.skipped), (5, 1))
        self.assertEqual(results.jobs[1]["video"], str(master))
        self.assertEqual(results.jobs[1]["trend_music"]["title"], "Song")
        self.assertEqual(master.read_bytes(), b"fake")

    def test_missing_cleared_track_stops_before_browser(self) -> None:
        master = self.out / "clip01.mp4"
        master.with_suffix(".music.json").write_text("{}", encoding="utf-8")
        playwright = Mock()
        with (patch.dict(sys.modules, {"playwright.sync_api": playwright}),
              patch("postclips.library.music.read_settings", return_value={}),
              patch("postclips.library.music.select", side_effect=ValueError("Kein aktueller Track")),
              patch("postclips.library.music.mix_for_upload") as mix,
              self.assertRaisesRegex(ValueError, "Kein aktueller Track")):
            publish(self.home, "vod1", ["youtube"], None, True, 0)
        playwright.sync_playwright.assert_not_called()
        mix.assert_not_called()
        self.assertFalse((self.out / "clip01.post.json").exists())

    def test_finished_clip_does_not_need_trend_selection(self) -> None:
        master = self.out / "clip01.mp4"
        master.with_suffix(".music.json").write_text("{}", encoding="utf-8")
        save_sidecar(self.out / "clip01.post.json", {"posts": {"youtube": {"status": "posted"}}})
        clips = discover_clips(self.home, "vod1")
        with patch("postclips.library.music.read_settings") as read:
            self.assertEqual(prepare_upload_music(clips, ["youtube"]), {})
        read.assert_not_called()

    def test_order_skip_and_no_wait(self) -> None:
        clips = discover_clips(self.home, "vod1")
        calls: list[tuple[str, str]] = []
        fail_once = {"tiktok": True}

        def poster(job) -> None:
            if job.platform == "tiktok" and job.clip_id == "clip01" and fail_once["tiktok"]:
                fail_once["tiktok"] = False
                raise RuntimeError("Seite hat sich geändert")
            calls.append((job.clip_id, job.platform))

        started = time.monotonic()
        result = run_posts(clips, PLATFORM_ORDER, poster)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(
            calls,
            [
                ("clip01", "youtube"),
                ("clip01", "instagram"),
                ("clip01", "snapchat"),
                ("clip01", "x"),
                ("clip02", "youtube"),
                ("clip02", "tiktok"),
                ("clip02", "instagram"),
                ("clip02", "snapchat"),
                ("clip02", "x"),
            ],
        )
        side = load_sidecar(self.out / "clip01.post.json")
        self.assertEqual(side["posts"]["youtube"]["status"], "posted")
        self.assertEqual(side["posts"]["tiktok"]["status"], "failed")
        self.assertIn("geändert", side["posts"]["tiktok"]["error"])
        self.assertEqual(result.failed, 1)

        calls.clear()
        again = run_posts(clips, PLATFORM_ORDER, poster)
        self.assertEqual(calls, [("clip01", "tiktok")])
        self.assertEqual(again.failed, 0)
        self.assertEqual(load_sidecar(self.out / "clip01.post.json")["posts"]["tiktok"]["status"], "posted")

    def test_db_row_when_database_exists(self) -> None:
        db = self.home / "clips.db"
        sqlite3.connect(db).close()
        record_db(db, "vod1", "clip01", "youtube", "posted", "")
        con = sqlite3.connect(db)
        row = con.execute("SELECT status FROM posts WHERE clip_id='clip01' AND platform='youtube'").fetchone()
        con.close()
        self.assertEqual(row[0], "posted")
        record_db(self.home / "missing.db", "vod1", "clip01", "x", "posted", "")
        self.assertFalse((self.home / "missing.db").exists())

    def test_dry_run_prints_the_txt_and_writes_no_sidecar(self) -> None:
        import importlib.util
        import json as json_mod
        from io import StringIO
        from contextlib import redirect_stdout

        spec = importlib.util.spec_from_file_location(
            "post_clips_cli",
            Path(__file__).resolve().parents[1] / "scripts" / "post_clips.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = module.main(["--vod-id", "vod1", "--home", str(self.home)])
        self.assertEqual(code, 0)
        payload = json_mod.loads(buffer.getvalue())
        self.assertTrue(payload["dry_run"])
        blob = json_mod.dumps(payload, ensure_ascii=False)
        self.assertIn("Der Moment, als es kippt.", blob)
        self.assertNotIn("Kurz vorher", blob)
        self.assertFalse((self.out / "clip01.post.json").exists())
        youtube = [row for row in payload["clips"] if row["clip_id"] == "clip01" and row["platform"] == "youtube"]
        self.assertEqual(youtube[0]["text"], "Er kann es nicht fassen #Shorts")

        master = self.out / "clip01.mp4"
        master.with_suffix(".music.json").write_text("{}", encoding="utf-8")
        track = SimpleNamespace(title="Song", artist="Artist", source_platform="youtube",
                                checked_on=date(2026, 11, 19))
        buffer = StringIO()
        with (patch("postclips.library.music.read_settings", return_value={}),
              patch("postclips.library.music.select", return_value=track),
              patch("postclips.library.music.mix_for_upload") as mix,
              patch.object(module, "publish") as live,
              redirect_stdout(buffer)):
            self.assertEqual(module.main(["--vod-id", "vod1", "--home", str(self.home),
                                          "--platforms", "youtube"]), 0)
        jobs = json_mod.loads(buffer.getvalue())["clips"]
        self.assertEqual(jobs[0]["trend_music"]["title"], "Song")
        self.assertNotIn("trend_music", jobs[1])
        self.assertFalse((self.out / "clip01.post.json").exists())
        mix.assert_not_called()
        live.assert_not_called()

    def test_sidecar_roundtrip(self) -> None:
        path = self.out / "clip01.post.json"
        save_sidecar(path, {"vod_id": "vod1", "clip_id": "clip01", "posts": {}})
        self.assertEqual(load_sidecar(path)["vod_id"], "vod1")


if __name__ == "__main__":
    unittest.main()
