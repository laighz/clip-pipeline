"""Local publication evidence, keyed by the actual MP4 revision."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

PLATFORMS = {'YT', 'TT', 'SC', 'IG', 'X'}
TOKEN = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,159}')
CLIP = re.compile(r'clip[0-9]+')
DIGEST = re.compile(r'[0-9a-f]{64}')


def normalize(text):
    return ' '.join(text.split())


def tiktok_row(body, caption):
    rows = [x.strip() for x in body.splitlines() if x.strip()]
    return bool(caption) and any(
        normalize(row).casefold() == normalize(caption).casefold()
        and bool(re.fullmatch(r'\d{1,3}:\d{2}(?::\d{2})?', rows[i - 1]))
        and rows[i + 2] in {'Everyone', 'Public', 'Alle', 'Öffentlich'}
        for i, row in enumerate(rows) if 0 < i < len(rows) - 2
    )


def spotlight_receipt(url, text):
    return (urlsplit(url).path.rstrip('/').endswith('/details/spotlights')
            and any(l.strip() == 'Success! Your Snap is now live!' for l in text.splitlines()))


def valid_key(vod, clip):
    return (isinstance(vod, str) and isinstance(clip, str)
            and bool(TOKEN.fullmatch(vod)) and bool(CLIP.fullmatch(clip)))


def file_stamp(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def media_revision(media):
    if media.is_symlink() or media.parent.is_symlink():
        raise OSError('symlinked media')
    sha = hashlib.sha256()
    with media.open('rb') as f:
        before = file_stamp(os.fstat(f.fileno()))
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            sha.update(chunk)
    if before != file_stamp(media.stat()):
        raise OSError('media changed while reading')
    return sha.hexdigest()


def rows(path):
    try:
        with path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and valid_key(row.get('vod'), row.get('clip')):
                    yield row
    except FileNotFoundError:
        return


def confirmed_records(path, attempts=None, *, out_root=None):
    out_root = out_root or Path.home() / 'clips/out'
    targets, confirmed = set(), set()
    if attempts is not None:
        for r in rows(attempts):
            p = r.get('platform')
            if not isinstance(p, str) or p not in PLATFORMS:
                continue
            key = (r['vod'], r['clip'], r.get('revision'), p)
            if not isinstance(key[2], str):
                continue
            targets.add(key)
            if r.get('state') == 'confirmed':
                confirmed.add(key)
    unresolved = {key[:3] for key in targets - confirmed}
    out = {}
    revisions = {}
    for r in rows(path):
        p, ts, rev = r.get('platforms'), r.get('ts'), r.get('revision')
        key = (r['vod'], r['clip'])
        if (r.get('verified') is not True or not isinstance(p, list) or not p
                or not all(isinstance(x, str) and x in PLATFORMS for x in p)
                or type(ts) is not int or ts <= 0 or not isinstance(rev, str)
                or not DIGEST.fullmatch(rev) or (*key, rev) in unresolved):
            continue
        if key not in revisions:
            try:
                revisions[key] = media_revision(out_root / key[0] / (key[1] + '.mp4'))
            except OSError:
                revisions[key] = None
        if revisions[key] != rev:
            continue
        clips = out.setdefault(key[0], {})
        clips[key[1]] = max(ts, clips.get(key[1], 0))
    return out


def append(path, row):
    with path.open('a') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def history_for(live, vod, clip, revision):
    return [r for r in rows(live / 'publish_attempts.jsonl')
            if (r['vod'], r['clip'], r.get('revision')) == (vod, clip, revision)
            and isinstance(r.get('platform'), str) and r['platform'] in PLATFORMS]


def save_attempt(live, vod, clip, revision, platform, known):
    append(live / 'publish_attempts.jsonl', dict(vod=vod, clip=clip, revision=revision,
           platform=platform, ts=time.time_ns() // 10**9, state='attempted', known=known))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['status', 'target', 'confirm', 'complete'])
    p.add_argument('live', type=Path)
    p.add_argument('vod')
    p.add_argument('clip')
    p.add_argument('platforms', nargs='+', choices=sorted(PLATFORMS))
    a = p.parse_args()
    if not valid_key(a.vod, a.clip) or (a.action != 'complete' and len(a.platforms) != 1):
        p.error('invalid clip or platform count')
    out_root = Path.home() / 'clips/out'
    try:
        revision = media_revision(out_root / a.vod / (a.clip + '.mp4'))
    except OSError as exc:
        print(f'Media unavailable: {exc}')
        return 2
    path = a.live / 'publish_attempts.jsonl'
    history = history_for(a.live, a.vod, a.clip, revision)
    done = {r['platform']: r['ts'] for r in history if r.get('state') == 'confirmed'
            and type(r.get('ts')) is int}
    if a.action == 'complete':
        platforms = set(a.platforms) | {r['platform'] for r in history}
        if not platforms <= done.keys():
            return 2
        ts = max(done[x] for x in platforms)
        ledger = a.live / 'posted_clips.jsonl'
        if not any(r.get('revision') == revision and r['vod'] == a.vod and r['clip'] == a.clip
                   and r.get('platforms') == sorted(platforms) and r.get('ts') == ts
                   and r.get('verified') is True for r in rows(ledger)):
            append(ledger, dict(vod=a.vod, clip=a.clip, ts=ts, revision=revision,
                                verified=True, platforms=sorted(platforms)))
        return 0
    plat = a.platforms[0]
    prior = [r for r in history if r['platform'] == plat]
    attempted = any(r.get('state') == 'attempted' for r in prior)
    if a.action == 'status':
        print('confirmed' if plat in done else 'pending' if attempted else 'new')
    elif a.action == 'confirm' and not attempted:
        return 2
    elif (a.action == 'target' and not prior) or (a.action == 'confirm' and plat not in done):
        append(path, dict(vod=a.vod, clip=a.clip, revision=revision, platform=plat, ts=int(time.time()),
                          state='target' if a.action == 'target' else 'confirmed'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
