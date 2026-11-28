import importlib.util
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    assert spec is not None and spec.loader is not None, name
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Ledger(unittest.TestCase):
    def test_direct_publisher_requires_voiceover_receipt_even_with_expected_hash(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'HOME': folder}), \
                patch.object(sys, 'path', [str(ROOT), *sys.path]):
            out = Path(folder) / 'clips/out/example'
            out.mkdir(parents=True)
            media = out / 'clip01.mp4'
            media.write_bytes(b'rendered video')
            (out / 'clip01.txt').write_text('--- X ---\nCaption\n')
            publisher = load('publish_one')
            rev = publisher.media_revision(media)
            with patch.object(sys, 'argv', ['publish_one.py', 'X', 'example', 'clip01', '--expected-revision', rev]), \
                    patch.object(publisher.h, 'connect', side_effect=AssertionError('browser reached')):
                self.assertEqual(publisher.main(), 2)
                media.with_suffix('.vo-ready').write_text(rev + '\n')
                self.assertEqual(publisher.main(), 2)

    def test_legacy_backup_and_stale_voiceover_receipt_never_submit(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            live = home / '.hermes/cache/scratch/live'
            live.mkdir(parents=True)
            (live.parent / 'pw').mkdir()
            out = home / 'clips/out/example'
            out.mkdir(parents=True)
            clip = out / 'clip01.mp4'
            clip.write_bytes(b'current video')
            (out / 'clip01.novo.mp4').write_bytes(b'old backup')
            bin_dir = home / 'bin'
            bin_dir.mkdir()
            uv = bin_dir / 'uv'
            uv.write_text('#!/bin/sh\ntouch "$HOME/uploader-was-called"\nexit 2\n')
            uv.chmod(0o700)
            env = {**os.environ, 'HOME': folder, 'SKIP': 'YT|TT|IG|SC',
                   'POST_BROWSER_LOCK': str(home / 'lock'),
                   'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}
            command = ['bash', str(ROOT / 'post_all.sh'), 'example', 'clip01']
            revision = load('publish_status').media_revision(clip)
            for receipt in (None, revision, 'audio-only-v1:' + '0' * 64):
                if receipt is not None:
                    (out / 'clip01.vo-ready').write_text(receipt + '\n')
                run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertFalse((home / 'uploader-was-called').exists())

    def test_blocked_request_is_not_a_successful_empty_batch(self):
        with tempfile.TemporaryDirectory() as home:
            live = Path(home) / '.hermes/cache/scratch/live'
            live.mkdir(parents=True)
            (live.parent / 'pw').mkdir()
            (live / 'IG_BLOCKED').touch()
            out = Path(home) / 'clips/out/example'
            out.mkdir(parents=True)
            (out / 'clip01.mp4').touch()
            (out / 'clip01.novo.mp4').touch()
            run = subprocess.run(['bash', str(ROOT / 'post_all.sh'), 'example', 'clip01'],
                                 env={**os.environ, 'HOME': home, 'SKIP': 'YT|TT|SC|X',
                                      'POST_BROWSER_LOCK': str(Path(home) / 'lock')},
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)

            self.assertIn('IG: skipped (blocked)', run.stdout)

    def test_replacing_mp4_does_not_inherit_a_verified_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            media = home / 'clips/out/vid/clip01.mp4'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'first media')
            status = load('publish_status')
            first = status.media_revision(media)
            ledger = home / 'posted_clips.jsonl'
            now = int(time.time())
            ledger.write_text(json.dumps(dict(vod='vid', clip='clip01', ts=now, verified=True,
                                              platforms=['TT'], revision=first)) + '\n')
            self.assertEqual(status.confirmed_records(ledger, out_root=media.parents[1]), {'vid': {'clip01': now}})
            media.write_bytes(b'replacement')
            self.assertEqual(status.confirmed_records(ledger, out_root=media.parents[1]), {})
    def test_all_skipped_cannot_start_deletion_timer(self):
        with tempfile.TemporaryDirectory() as home:
            tmp = Path(home)
            live = tmp / '.hermes/cache/scratch/live'
            live.mkdir(parents=True)
            (live.parent / 'pw').mkdir()
            out = tmp / 'clips/out/example'
            out.mkdir(parents=True)
            (out / 'clip01.novo.mp4').touch()
            run = subprocess.run(['bash', str(ROOT / 'post_all.sh'), 'example', 'clip01'],
                                 env={**os.environ, 'HOME': home, 'SKIP': 'YT|TT|X|SC|IG'},
                                 capture_output=True, text=True, timeout=10)
            self.assertFalse((live / 'posted_clips.jsonl').exists(), run.stdout + run.stderr)

    def test_uncertain_upload_is_only_rechecked_on_second_run(self):
        with tempfile.TemporaryDirectory() as home:
            tmp = Path(home)
            live = tmp / '.hermes/cache/scratch/live'
            live.mkdir(parents=True)
            (live.parent / 'pw').mkdir()
            out = tmp / 'clips/out/example'
            out.mkdir(parents=True)
            (out / 'clip01.novo.mp4').touch()
            bin_dir = tmp / 'bin'
            (out / 'clip01.mp4').touch()
            bin_dir.mkdir()
            (out / 'clip01.vo-ready').write_text('audio-only-v1:' + load('publish_status').media_revision(out / 'clip01.mp4') + '\n')
            uv = bin_dir / 'uv'
            uv.write_text('#!/bin/bash\nif [[ "$*" == *--verify-only* ]]; then\n'
                          '  python3 "$POST_TEST_ROOT/publish_status.py" confirm "$HOME/.hermes/cache/scratch/live" example clip01 X || exit 1\n'
                          '  echo "readback only"; exit 10\nfi\n'
                          'PYTHONPATH="$POST_TEST_ROOT" python3 -c \'import publish_status as s; from pathlib import Path; h=Path.home(); s.save_attempt(h/".hermes/cache/scratch/live", "example", "clip01", s.media_revision(h/"clips/out/example/clip01.mp4"), "X", [])\' || exit 1\n'
                          'echo "submission unconfirmed"; exit 2\n')
            uv.chmod(0o700)
            env = {**os.environ, 'HOME': home, 'SKIP': 'TT|YT|SC|IG',
                   'POST_BROWSER_LOCK': str(tmp / 'browser.lock'), 'POST_TEST_ROOT': str(ROOT),
                   'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}
            cmd = ['bash', str(ROOT / 'post_all.sh'), 'example', 'clip01']
            fake = uv.read_text()
            uv.write_text('#!/bin/bash\necho "CDP unavailable before submit"; exit 1\n')
            preflight = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(preflight.returncode, 2, preflight.stdout + preflight.stderr)
            self.assertNotIn('attempted', (live / 'publish_attempts.jsonl').read_text())
            uv.write_text(fake)
            first = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
            self.assertFalse((live / 'posted_clips.jsonl').exists())
            # A pre-policy submission can still be confirmed without authorizing a retry.
            (out / 'clip01.vo-ready').write_text(load('publish_status').media_revision(out / 'clip01.mp4') + '\n')
            second = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn('readback only', second.stdout)
            self.assertNotIn('submission unconfirmed', second.stdout)
            self.assertEqual(json.loads((live / 'posted_clips.jsonl').read_text())['platforms'], ['X'])

    def test_legacy_and_unverified_attempts_are_not_cleanup_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'posted_clips.jsonl'
            now = int(time.time())
            rows = [{'vod': 'vid', 'clip': 'clip01', 'ts': now},
                    {'vod': 'vid', 'clip': 'clip02', 'ts': now, 'verified': False},
                    {'vod': '../vid', 'clip': 'clip03', 'ts': now, 'verified': True, 'platforms': ['TT']},
                    {'vod': 'vid', 'clip': 'clip04', 'ts': now, 'verified': True, 'platforms': ['TT']}]
            path.write_text('\n'.join(map(json.dumps, rows)) + '\n{incomplete\n')
            self.assertEqual(load('publish_status').confirmed_records(path), {})


class Readback(unittest.TestCase):
    def test_unverified_snapchat_tab_is_left_open_for_review(self):
        tree = ast.parse((ROOT / 'publish_one.py').read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        cleanup = next(n for n in ast.walk(main) if isinstance(n, ast.For)
                       and ast.unparse(n.iter) == 'managed.values()')
        for platform, proof, should_close in [('SC', None, False), ('SC', 'verified', True), ('TT', None, True)]:
            with self.subTest(platform=platform, proof=proof):
                class Page:
                    closed = False
                    def close(self):
                        self.closed = True
                page = Page()
                exec(compile(ast.Module(body=[cleanup], type_ignores=[]), '<page cleanup>', 'exec'),
                     {'managed': {platform: page}, 'plat': platform, 'confirmed': proof, 'BrowserError': RuntimeError})
                self.assertEqual(page.closed, should_close)

    def test_tiktok_only_accepts_exact_public_caption_in_post_list(self):
        confirm = load('publish_status').tiktok_row
        caption = 'Dolores hat Hausverbot #gta6'
        def body(text, visibility='Everyone'):
            return '\n'.join(['Posts (Created on)', '00:19', text, 'Nov 23, 10:49 PM', visibility, '31', '2', '0'])
        self.assertTrue(confirm(body(caption), caption))
        self.assertTrue(confirm(body(caption.lower()), caption))
        self.assertFalse(confirm(body(caption, 'Only you'), caption))
        self.assertFalse(confirm(body(caption + ' Remix'), caption))
        self.assertFalse(confirm('Upload\n' + caption, caption))

    def test_story_is_not_spotlight(self):
        confirm = load('publish_status').spotlight_receipt
        self.assertFalse(confirm('/details/public-stories', 'Success! Your Snap is now live!'))
        self.assertFalse(confirm('/details/spotlights', 'Success!'))
        self.assertFalse(confirm('/web-uploader', 'Success! Your Snap is now live!'))
        self.assertTrue(confirm('/details/spotlights', 'Success! Your Snap is now live!'))

    def test_followup_cannot_complete_while_other_platform_is_unconfirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            media = tmp / 'clips/out/example/clip01.mp4'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'clip')
            status = load('publish_status')
            rev = status.media_revision(media)
            def action(verb, *platforms):
                return subprocess.run([sys.executable, str(ROOT / 'publish_status.py'), verb,
                                       folder, 'example', 'clip01', *platforms],
                                      env={**os.environ, 'HOME': folder}, capture_output=True, text=True)
            self.assertEqual(action('target', 'TT').returncode, 0)
            self.assertEqual(action('status', 'TT').stdout.strip(), 'new')
            status.save_attempt(tmp, 'example', 'clip01', rev, 'TT', ['1'])
            self.assertEqual(action('status', 'TT').stdout.strip(), 'pending')
            status.save_attempt(tmp, 'example', 'clip01', rev, 'YT', ['2'])
            self.assertEqual(action('confirm', 'YT').returncode, 0)
            self.assertEqual(action('complete', 'YT').returncode, 2)
            self.assertFalse((tmp / 'posted_clips.jsonl').exists())
            self.assertEqual(action('confirm', 'TT').returncode, 0)
            self.assertEqual(action('complete', 'YT', 'TT').returncode, 0)
            self.assertEqual(action('complete', 'YT', 'TT').returncode, 0)
            rows = (tmp / 'posted_clips.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])['platforms'], ['TT', 'YT'])
            self.assertEqual(action('target', 'SC').returncode, 0)
            self.assertEqual(load('publish_status').confirmed_records(tmp / 'posted_clips.jsonl',
                                  tmp / 'publish_attempts.jsonl', out_root=media.parents[1]), {})

    def test_janitor_dry_run_only_selects_confirmed_old_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            live = tmp / '.hermes/cache/scratch/live'
            live.mkdir(parents=True)
            out = tmp / 'clips/out/example'
            out.mkdir(parents=True)
            for i in range(1, 4):
                (out / f'clip0{i}.mp4').touch()
            now = int(time.time())
            rev = load('publish_status').media_revision(out / 'clip02.mp4')
            rows = [{'vod': 'example', 'clip': 'clip01', 'ts': now - 90000},
                    {'vod': 'example', 'clip': 'clip02', 'ts': now - 90000, 'verified': True, 'platforms': ['TT'], 'revision': rev},
                    {'vod': 'example', 'clip': 'clip03', 'ts': now, 'verified': True, 'platforms': ['TT'], 'revision': rev}]
            (live / 'posted_clips.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n')
            run = subprocess.run([sys.executable, str(ROOT.parent / 'live/janitor.py'), '--dry-run'],
                                 env={**os.environ, 'HOME': folder}, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('delete posted clip file example/clip02.mp4', run.stdout)
            self.assertNotIn('delete posted clip file example/clip01.mp4', run.stdout)
            self.assertNotIn('delete posted clip file example/clip03.mp4', run.stdout)
            self.assertEqual(len(list(out.glob('*.mp4'))), 3)
