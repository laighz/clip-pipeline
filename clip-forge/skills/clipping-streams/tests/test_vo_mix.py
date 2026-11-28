import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'live-ops' / 'live'))
import vo_mix  # pyright: ignore[reportMissingImports]


class VoiceoverMixTest(unittest.TestCase):
    def test_phonetic_override_is_one_complete_utterance(self):
        clip = {'vo': 'Syndicate will auf dem Bike hoch hinaus.',
                'vo_spoken': '  ßindicket  will auf dem Bike hoch hinaus.  '}
        self.assertEqual(vo_mix.voiceover_text(clip), (
            'Syndicate will auf dem Bike hoch hinaus.', 'ßindicket will auf dem Bike hoch hinaus.'))
        self.assertEqual(vo_mix.voiceover_text(clip, 'Anderer Text.'), ('Anderer Text.', 'Anderer Text.'))
        self.assertEqual(vo_mix.voiceover_text({'vo': 'Normaler Text.'}), ('Normaler Text.', 'Normaler Text.'))

    def test_rejects_invalid_override(self):
        for bad in ('', None, ['separate', 'parts']):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                vo_mix.voiceover_text({'vo': 'Normaler Text.', 'vo_spoken': bad})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_original_is_five_percent_under_intro_and_restores_smoothly(self):
        raw = subprocess.check_output([
            'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'aevalsrc=0.25:s=48000:d=1.5',
            '-af', vo_mix.original_volume_filter(.5), '-f', 'f32le', 'pipe:1',
        ])
        samples = [x[0] for x in struct.iter_unpack('<f', raw)]
        def at(sec):
            return samples[round(sec * 48000)] / .25

        self.assertAlmostEqual(at(.01), .05, places=6)
        self.assertAlmostEqual(at(.48), .05, places=6)
        self.assertTrue(.05 < at(.56) < 1)
        self.assertAlmostEqual(at(.7), 1., places=6)
