import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from render_clip import build_ass  # pyright: ignore[reportMissingImports]


def captions(ass):
    def seconds(t):
        h, m, s = map(float, t.split(':'))
        return 3600 * h + 60 * m + s
    return [(seconds(c[1]), seconds(c[2]), re.sub(r'\{[^}]*\}', '', c[9]).replace(r'\N', ' '))
            for line in ass.splitlines() if line.startswith('Dialogue: 0,')
            for c in [line.split(',', 9)]]


class VoiceoverCaptionsTest(unittest.TestCase):
    def test_only_audible_source_is_captioned_even_when_voiceover_crosses_cut(self):
        clip = {'id': 'clip01', 'segments': [{'start': 10, 'end': 11}, {'start': 40, 'end': 48}]}
        words = [{'s': 10.15, 'e': 10.5, 'w': 'Quiet.'},
                 {'s': 40.2, 'e': 40.55, 'w': 'Also-quiet.'},
                 {'s': 40.9, 'e': 41.4, 'w': 'Straddles.'},
                 {'s': 41.6, 'e': 42.2, 'w': 'Audible.'}]
        cues, duration, count = build_ass(clip, words, {'caption_uppercase': False}, None,
                                          duck_until=2.1)
        self.assertEqual(captions(cues), [(2.6, 3.8, 'Audible.')])
        self.assertEqual(count, 1)
        self.assertEqual(duration, 9.)

    def test_no_voiceover_keeps_source_captions(self):
        clip = {'id': 'clip01', 'segments': [{'start': 10, 'end': 13}]}
        word = {'s': 10.1, 'e': 10.6, 'w': 'Original.'}
        cues, _, count = build_ass(clip, [word], {}, None)
        self.assertEqual(captions(cues), [(.1, 1.2, 'Original.')])
        self.assertEqual(count, 1)

    def test_voiceover_window_does_not_remove_opening_hook(self):
        clip = {'id': 'clip01', 'segments': [{'start': 0, 'end': 3}]}
        cues, _, _ = build_ass({**clip, 'hook': 'Opening hook'}, [], {}, None, duck_until=2.1)
        self.assertIn('Opening hook', cues)
        self.assertFalse(captions(cues))


if __name__ == '__main__':
    unittest.main()
