"""The TikTok native upload preparation must stay before the publish click."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "post_clips.py"
spec = importlib.util.spec_from_file_location("native_upload_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class NativeEditorTests(unittest.TestCase):
    def test_cli_hands_verified_master_and_levels_to_tiktok_editor_without_posting(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            output = home / "out" / "dish"
            output.mkdir(parents=True)
            master = output / "clip01.mp4"
            master.write_bytes(b"approved master")
            master.with_suffix(".txt").write_text("TITEL: Pasta\n\n--- TIKTOK ---\nDas Rezept.\n", encoding="utf-8")
            master.with_suffix(".json").write_text(json.dumps({
                "duration_s": 20, "trend_music_upload": True, "trend_music_mode": "native",
            }))
            master.with_suffix(".music.json").write_text(json.dumps({
                "schema_version": 2, "mode": "native", "music_volume": .1, "original_volume": 1,
                "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
            }))

            args = ["--home", str(home), "--vod-id", "dish", "--clip", "clip01",
                    "--platforms", "tiktok", "--open-native-editor"]
            stdout = io.StringIO()
            with (patch.object(cli.sys.stdin, "isatty", return_value=True),
                  patch.object(cli, "guide_tiktok_native", return_value=True) as editor,
                  patch.object(cli, "publish") as publish, redirect_stdout(stdout)):
                self.assertEqual(cli.main(args), 0)

            _, job, instructions, _ = editor.call_args.args
            self.assertEqual(job.video, master)
            self.assertEqual(job.texts["tiktok"], "Das Rezept.")
            self.assertTrue(any("100%" in step and "10%" in step for step in instructions))
            self.assertEqual(json.loads(stdout.getvalue())["posted"], 0)
            publish.assert_not_called()
            self.assertFalse(master.with_suffix(".post.json").exists())

    def test_tiktok_editor_prep_uses_edit_button_only(self):
        from postclips.browser import Session

        fake_playwright = SimpleNamespace(TimeoutError=TimeoutError)
        frame = Mock()
        session = Session(Mock(), 10)
        job = Mock()
        with (patch.dict("sys.modules", {"playwright.sync_api": fake_playwright}),
              patch.object(session, "_tiktok_upload", return_value=frame) as draft,
              patch.object(session, "post") as post):
            self.assertTrue(session.open_tiktok_sound_editor(job))
        draft.assert_called_once_with(job)
        self.assertTrue(frame.get_by_role.call_args.kwargs["name"].fullmatch("Edit video"))
        frame.get_by_role.return_value.first.click.assert_called_once()
        frame.locator.assert_not_called()
        post.assert_not_called()
