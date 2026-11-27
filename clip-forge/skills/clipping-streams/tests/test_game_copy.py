"""Offline CLI tests for the words shown at the start of a GTA clip."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class GameCopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.work = self.home / "work" / "gta-test"
        (self.work / "transcripts").mkdir(parents=True)
        self.write("meta.json", {"duration_s": 32, "streamer": "zarbex", "width": 1920, "height": 1080})
        self.write("source.json", {"twitch_clip_id": "shortclip", "login": "zarbex"})
        self.write("candidates.json", {"candidates": [{"id": "c20", "moment_s": 20, "score": 4}]})
        self.write("sections.json", {"sections": [{"id": "s0-32", "start": 0, "end": 32}]})

    def write(self, name, data):
        (self.work / name).write_text(json.dumps(data), encoding="utf-8")

    def plan(self):
        run = subprocess.run(
            [sys.executable, str(SCRIPTS / "plan_game_clip.py"), "--vod-id", "gta-test"],
            env={**os.environ, "CLIPS_HOME": str(self.home)}, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        return json.loads((self.work / "clips.json").read_text(encoding="utf-8"))["clips"][0]

    def test_scene_specific_quote_in_hook_title_and_captions(self):
        self.write("transcripts/s0-32.json", {"segments": [
            {"s": 1, "e": 3, "text": "Die Oma hat einen Rollator."},
            {"s": 19, "e": 22, "text": "Oma hat mich ausgeraubt!"},
        ]})
        clip = self.plan()
        self.assertEqual(clip["hook"], "Die Oma hat einen Rollator")
        self.assertEqual(clip["title"], "zarbex in GTA 6: „Die Oma hat einen Rollator“ #Shorts")
        self.assertTrue(clip["captions"]["tiktok"].startswith(clip["hook"] + ". Clip: @zarbex"))
        self.assertEqual(clip["vo"], "Gleich kippt die Runde.")
        self.assertEqual(clip["layout"], "stack")

    def test_no_opening_quote_uses_only_known_context(self):
        self.write("source.json", {"login": "zarbex", "title": "Zarbex findet versteckte Millionen"})
        self.write("transcripts/s9-12.json", {"segments": [
            {"s": 1, "e": 2, "text": "Da steht ein goldenes Auto."},
        ]})
        self.write("transcripts/s0-32.json", {"segments": [
            {"s": 1, "e": 2, "text": "Die Oma hat einen Rollator 😱"},
            {"s": 2, "e": 3, "text": "Hol den Schatz @jemand"},
            {"s": 3, "e": 4, "text": "Hier sieht man einen extrem langen und unpassenden Halbsatz"},
            {"s": 21, "e": 23, "text": "Die Oma klaut das Auto!"},
        ]})
        clip = self.plan()
        self.assertEqual(clip["hook"], "zarbex spielt GTA 6")
        self.assertEqual(clip["title"], "zarbex spielt GTA 6 #Shorts")

    def test_unreadable_selected_transcript_uses_neutral_hook(self):
        path = self.work / "transcripts/s0-32.json"
        for invalid in (b'{"segments":', b"\xff"):
            with self.subTest(invalid=invalid):
                path.write_bytes(invalid)
                self.assertEqual(self.plan()["hook"], "zarbex spielt GTA 6")
        path.unlink()
        path.symlink_to(path.name)  # Reading a symlink loop raises OSError.
        self.assertEqual(self.plan()["hook"], "zarbex spielt GTA 6")

    def test_invalid_selected_transcript_schema_uses_neutral_hook(self):
        for invalid in ([], {"segments": {}}, {"segments": 1}, {"segments": [None]}):
            with self.subTest(invalid=invalid):
                self.write("transcripts/s0-32.json", invalid)
                self.assertEqual(self.plan()["hook"], "zarbex spielt GTA 6")

    def test_invalid_segment_records_do_not_hide_valid_quote(self):
        self.write("transcripts/s0-32.json", {"segments": [
            None,
            {"s": "0", "e": 1, "text": "Ein Schatz liegt unter dem Rathaus."},
            {"s": False, "e": 1, "text": "Ein Schatz liegt unter dem Rathaus."},
            {"s": 3, "e": 2, "text": "Ein Schatz liegt unter dem Rathaus."},
            {"s": 1, "e": 3, "text": "Die Oma hat einen Rollator."},
        ]})
        self.assertEqual(self.plan()["hook"], "Die Oma hat einen Rollator")

    def test_question_keeps_its_punctuation_in_caption(self):
        self.write("transcripts/s0-32.json", {"segments": [
            {"s": 1, "e": 3, "text": "Wo steht der rote Lieferwagen?"},
        ]})
        clip = self.plan()
        self.assertEqual(clip["hook"], "Wo steht der rote Lieferwagen?")
        self.assertTrue(clip["captions"]["instagram"].startswith("Wo steht der rote Lieferwagen? Clip:"))


if __name__ == "__main__":
    unittest.main()
