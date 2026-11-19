"""Offline trend catalog and real upload-mix regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from postclips import music


class MusicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="postclips-music-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.video = self.root / "clip01.mp4"
        self.catalog = self.root / "music-trends.json"
        self.cache = self.root / "upload-cache"
        self.song = self.root / "music.wav"
        self.settings = {"schema_version": 1, "catalog": str(self.catalog), "region": "DE",
                         "timezone": "Europe/Berlin", "max_age_days": 7, "volume": .1,
                         "master_sha256": "a" * 64}

    def _record(self, **changes):
        return {"title": "Fresh Groove", "artist": "Fixture Artist", "source_platform": "facebook",
                "source_url": "https://www.facebook.com/reel/123/", "checked_on": "2026-11-19",
                "region": "DE", "file": self.song.name,
                "cleared_for": ["instagram", "tiktok"], "rights_reference": "Fixture permissions"} | changes

    def _write_catalog(self, *records):
        self.catalog.write_text(json.dumps({"tracks": records}), encoding="utf-8")

    def _track(self):
        # A short track asserts looping across the full three-second master.
        frequency = 440
        rate = 48000
        values = (round(11000 * math.sin(2 * math.pi * frequency * i / rate)) for i in range(rate // 2))
        with wave.open(str(self.song), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(struct.pack("<" + "h" * (rate // 2), *values))

    @staticmethod
    def _ffmpeg(*args):
        return subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *map(str, args)],
                              capture_output=True, check=True, timeout=25).stdout

    def test_music_sidecar_is_opt_in_and_independent_of_the_media(self):
        self.assertIsNone(music.read_settings(self.video))
        sidecar = self.video.with_suffix(".music.json")
        sidecar.write_text(json.dumps(self.settings), encoding="utf-8")
        self.assertEqual(music.read_settings(self.video), self.settings)
        sidecar.write_text(json.dumps(self.settings | {"catalog": "relative.json"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"clip01\.music\.json.*absoluter lokaler Dateipfad"):
            music.read_settings(self.video)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe fehlt")
    def test_select_skips_invalid_fresher_items_and_requires_platform_permissions(self):
        self._track()
        self._write_catalog(self._record(title="Older", checked_on="2026-11-16"),
                            self._record(title="Spoofed", source_url="https://facebook.com.example.invalid/"),
                            self._record(title="Future", checked_on="2026-11-20"),
                            self._record(title="Not cleared", cleared_for=["tiktok"]), self._record())
        selected = music.select(self.settings, ["instagram", "tiktok"], today=date(2026, 11, 19))
        self.assertEqual(selected.title, "Fresh Groove")
        self.assertEqual(selected.checked_on.isoformat(), "2026-11-19")
        self.assertEqual(selected.file, self.song)
        with self.assertRaisesRegex(ValueError, r"Keine passende Trendmusik.*youtube"):
            music.select(self.settings, ["instagram", "youtube"], today=date(2026, 11, 19))
        with self.assertRaisesRegex(ValueError, r"höchstens 7 Tage"):
            music.select(self.settings, ["instagram"], today=date(2026, 11, 28))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe fehlen")
    def test_mix_preserves_the_video_and_ducks_music_under_voice(self):
        self._track()
        self._write_catalog(self._record())
        selected = music.select(self.settings, ["instagram"], today=date(2026, 11, 19))
        voice = r"aevalsrc=if(between(t\,1.10\,1.85)\,0.65*sin(2*PI*690*t)\,0):s=48000:d=3"
        self._ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=96x160:r=30:d=3", "-f", "lavfi", "-i", voice,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", self.video)
        digest = hashlib.sha256(self.video.read_bytes()).hexdigest()
        output = music.mix_for_upload(self.video, selected, self.cache, master_sha256=digest)
        self.assertEqual(output.parent, self.cache)
        self.assertEqual(list(self.root.glob("clip*.mp4")), [self.video])
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(), digest)
        stream_args = ("-map", "0:v:0", "-c", "copy", "-f", "streamhash", "-")
        self.assertEqual(self._ffmpeg("-i", self.video, *stream_args), self._ffmpeg("-i", output, *stream_args))

        pcm = self._ffmpeg("-i", output, "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-")
        samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)

        def amplitude(start, frequency):
            window = samples[round(start * 48000):round(start * 48000) + 12000]
            sine = sum(value * math.sin(2 * math.pi * frequency * i / 48000) for i, value in enumerate(window))
            cosine = sum(value * math.cos(2 * math.pi * frequency * i / 48000) for i, value in enumerate(window))
            return 2 * math.hypot(sine, cosine) / len(window) / 32768

        before_voice = amplitude(.6, 440)
        self.assertGreater(before_voice, .015)
        self.assertLess(amplitude(1.35, 440), before_voice * .35)
        self.assertGreater(amplitude(1.35, 690), .3)
        self.assertGreater(amplitude(2.4, 440), before_voice * .5)

        # A cache hit may probe streams, but must not run another encode.
        with patch.object(music, "_tool", wraps=music._tool) as tools:
            self.assertEqual(music.mix_for_upload(self.video, selected, self.cache, master_sha256=digest), output)
        self.assertEqual([call.args[0] for call in tools.call_args_list], ["ffprobe"])
        quieter = music.mix_for_upload(self.video, replace(selected, volume=.05), self.cache, master_sha256=digest)
        self.assertNotEqual(quieter, output)
        self.video.write_bytes(self.video.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "freigegebene Master wurde geändert"):
            music.mix_for_upload(self.video, selected, self.cache, master_sha256=digest)


if __name__ == "__main__":
    unittest.main()
