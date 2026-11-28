"""Real local preview CLI; no video download, render, or posting fixtures."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/preview_candidates.py'


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
class CandidatePreviews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.folder = Path(temp.name)
        cls.media = cls.folder / 'local video.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x90:r=30:d=3',
                        '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=30:d=3', '-filter_complex',
                        '[0:v][1:v]concat=n=2:v=1:a=0', '-c:v', 'libx264', str(cls.media)],
                       check=True, capture_output=True, timeout=15)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=self.folder)
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.work = self.home / 'work' / 'test-vod'
        self.work.mkdir(parents=True)
        self.candidates = [
            {'id': 'c103', 'score': 9, 'section': [100, 106], 'moment_s': 103, 'reasons': ['chat-spike']},
            {'id': 'c303', 'score': 12, 'section': [300, 306], 'moment_s': 303, 'reasons': ['laut']},
            {'id': 'c102', 'score': 5, 'section': [100, 106], 'moment_s': 102},
        ]
        self.write('candidates', {'candidates': self.candidates})
        self.section = {'id': 's100-106', 'file': str(self.media), 'file_offset': 100, 'start': 100, 'end': 106}
        self.write('sections', {'sections': [self.section]})
        self.before = {p.name: p.read_bytes() for p in self.work.iterdir()}

    def write(self, name, value):
        (self.work / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def run_cli(self, *flags, vod='test-vod'):
        proc = subprocess.run([sys.executable, str(SCRIPT), '--vod-id', vod, *flags],
                              env={**os.environ, 'CLIPS_HOME': str(self.home)}, text=True,
                              capture_output=True, timeout=40, check=False)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def test_shortlist_is_ranked_partial_and_uses_source_offset(self):
        rc, result = self.run_cli('--limit', '2')
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([row['id'] for row in result['candidates']], ['c303', 'c103'])
        self.assertEqual(result['candidates'][0]['status'], 'missing_media')
        row = result['candidates'][1]
        self.assertEqual(row['status'], 'previewed')
        self.assertEqual(row['reasons'], ['chat-spike'])
        path = Path(result['contact_sheet'])
        self.assertTrue(path.is_relative_to((self.work / 'previews').resolve()))
        with Image.open(path) as sheet:
            rgb = sheet.convert('RGB')
            self.assertEqual(rgb.width, 960)
            colors = [rgb.getpixel((160, 130)), rgb.getpixel((800, 130))]
            self.assertGreater(colors[0][0], 175, colors)
            self.assertGreater(colors[1][2], 175, colors)
        self.assertFalse((self.home / 'out').exists())
        self.assertEqual({n: (self.work / n).read_bytes() for n in self.before}, self.before)

    def test_dry_run_returns_explicit_selection_in_order_without_writing(self):
        rc, result = self.run_cli('--candidates', 'c102,c103', '--dry-run')
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'planned')
        self.assertEqual([r['id'] for r in result['candidates']], ['c102', 'c103'])
        self.assertFalse((self.work / 'previews').exists())

    def test_unavailable_only_is_not_reported_as_successful_preview(self):
        rc, result = self.run_cli('--candidates', 'c303')
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['status'], 'no_previews')
        self.assertIsNone(result['contact_sheet'])
        self.assertFalse((self.work / 'previews').exists())

    def test_rejects_unknown_candidate_and_path_escape(self):
        rc, _ = self.run_cli('--candidates', 'c999')
        self.assertNotEqual(rc, 0)
        rc, _ = self.run_cli(vod='../outside')
        self.assertNotEqual(rc, 0)

    def test_failed_frame_extraction_never_writes_a_success_artifact(self):
        invalid = self.home / 'bad.mp4'
        invalid.write_bytes(b'no video')
        self.write('sections', {'sections': [dict(self.section, file=str(invalid))]})
        rc, result = self.run_cli('--candidates', 'c103')
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['candidates'][0]['status'], 'frame_error')
        self.assertIsNone(result['contact_sheet'])

    def test_local_source_fallback_respects_actual_meta_duration(self):
        (self.work / 'sections.json').unlink()
        self.write('meta', {'source': 'local', 'input': str(self.media), 'duration_s': 6})
        self.write('candidates', {'candidates': [{'id': 'c3', 'score': 9, 'section': [0, 8], 'moment_s': 3}]})
        rc, result = self.run_cli()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['candidates'][0]['frame_times_s'], [0, 3, 5.9])

    def test_local_section_without_file_duration_uses_meta_duration(self):
        self.write('meta', {'source': 'local', 'input': str(self.media), 'duration_s': 6})
        self.write('sections', {'source': 'local', 'sections': [dict(
            self.section, id='s0-7', start=0, end=7, file_offset=0, mode='local', status='lokal')]})
        self.write('candidates', {'candidates': [{'id': 'c4', 'score': 9, 'section': [0, 7], 'moment_s': 4}]})
        rc, result = self.run_cli()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['candidates'][0]['frame_times_s'], [1, 4, 5.9])

    def test_stale_section_revision_is_not_used(self):
        self.write('sections', {'sections': [dict(self.section, media_revision={'size': 0})]})
        rc, result = self.run_cli('--candidates', 'c103')
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['candidates'][0]['status'], 'missing_media')

    def test_clamps_reaction_at_actual_section_end(self):
        self.write('sections', {'sections': [dict(self.section, end=108, file_duration=6)]})
        self.write('candidates', {'candidates': [{'id': 'c104', 'score': 9, 'section': [100, 108], 'moment_s': 104}]})
        rc, result = self.run_cli()
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['candidates'][0]['frame_times_s'], [101, 104, 105.9])

    def test_center_at_end_is_not_moved_to_an_earlier_frame(self):
        self.write('sections', {'sections': [dict(self.section, file_duration=6)]})
        self.write('candidates', {'candidates': [{'id': 'c106', 'score': 9, 'section': [100, 106], 'moment_s': 106}]})
        rc, result = self.run_cli()
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['candidates'][0]['status'], 'missing_media')

    def test_invalid_manifest_shape_returns_json_error(self):
        for invalid in ([], {'sections': None}, {'sections': [None]}):
            with self.subTest(invalid=invalid):
                self.write('sections', invalid)
                rc, result = self.run_cli()
                self.assertEqual(rc, 2, result)
                self.assertIn('sections.json', result['error'])
