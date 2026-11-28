import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'live-ops' / 'live'))
import vo_mix  # pyright: ignore[reportMissingImports]


class VoiceoverMixTest(unittest.TestCase):
    def test_protected_opening_must_start_after_duck_and_fade(self):
        with self.assertRaisesRegex(ValueError, 'Originalsatz'):
            vo_mix.check_protected_opening({'vo_protect_from': 2.3}, 2.204, 17.75)
        vo_mix.check_protected_opening({'vo_protect_from': 2.5}, 2.204, 17.75)
        vo_mix.check_protected_opening({}, 2.204, 17.75)

    def test_protected_opening_must_be_a_time_inside_the_clip(self):
        for bad in (True, None, '2.5', float('nan'), -1, 17.75):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'vo_protect_from'):
                vo_mix.check_protected_opening({'vo_protect_from': bad}, 2.204, 17.75)

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

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_protected_word_at_fade_boundary_has_full_gain_in_both_channels(self):
        duck_until = 2.204
        onset = 2.325
        vo_mix.check_protected_opening({'vo_protect_from': onset}, duck_until, 2.5)
        raw = subprocess.check_output([
            'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'aevalsrc=0.25|0.5:s=48000:d=2.5',
            '-af', vo_mix.original_volume_filter(duck_until), '-f', 'f32le', 'pipe:1',
        ])
        self.assertEqual(struct.unpack_from('<ff', raw, round(onset * 48000) * 8), (.25, .5))
