"""Run a single uploader and verify publication in the dedicated Chrome profile."""
import argparse
import os
from pathlib import Path
import re
import runpy
import sys
import time
from urllib.parse import urlsplit

import h
from publish_status import append, history_for, media_revision, normalize, save_attempt, tiktok_row, valid_key, voiceover_ready
from playwright.sync_api import Error as BrowserError

YT_STUDIO = 'https://studio.youtube.com/channel/UCwCaCEXf-nQ0SbLcGi2Bv5Q/videos/short'
TT_STUDIO = 'https://www.tiktok.com/tiktokstudio/content'
X_PROFILE = 'https://x.com/livespiked26'
PLATFORMS = {'YT': ('ytu.py', 'YOUTUBE'), 'TT': ('ttu.py', 'TIKTOK'),
             'SC': ('scu.py', 'SNAPCHAT'), 'X': ('x2.py', 'X'), 'IG': ('igu.py', 'INSTAGRAM')}


def caption(text, key):
    marker = f'--- {key} ---'
    if marker not in text and key in {'SNAPCHAT', 'X'}:
        marker = '--- TIKTOK ---'
    return text.split(marker, 1)[1].split('---', 1)[0].strip()


def platform_url(platform):
    return {'YT': YT_STUDIO, 'TT': TT_STUDIO, 'X': X_PROFILE}.get(platform)


TT_ROWS = '[data-tt="components_RowLayout_FlexRow"]'
TT_VIDEO = 'a[href^="/@livespiked/video/"], a[href^="https://www.tiktok.com/@livespiked/video/"]'
TT_ID = re.compile(r'^https://www\.tiktok\.com/@livespiked/video/(\d{15,24})(?:[/?#].*)?$')


def post_ids(page, platform):
    selectors = {'TT': TT_VIDEO, 'YT': 'a[href*="/video/"][href*="/edit"]',
                 'X': 'article a:has(time)'}
    links = page.locator(selectors[platform]).evaluate_all('(els)=>els.map(e=>e.href)')
    if platform == 'TT':
        return {m.group(1) for link in links if (m := TT_ID.fullmatch(link))}
    if platform == 'YT':
        return {m.group(1) for link in links if (m := re.search(r'/video/([A-Za-z0-9_-]{11})/edit', link))}
    return {m.group(1) for link in links if (m := re.fullmatch(r'https://x\.com/livespiked26/status/(\d+)', link, re.I))}


def inspect_public(page, platform, desc, title, known=None):
    previous = set(known or ())
    if platform == 'TT':
        if urlsplit(page.url).netloc != 'www.tiktok.com' or urlsplit(page.url).path.rstrip('/') != '/tiktokstudio/content':
            return None
        candidates = set()
        for row in page.locator(TT_ROWS).filter(has_text=re.compile(re.escape(desc), re.I)).all():
            if not tiktok_row(row.inner_text(timeout=4000), desc):
                continue
            for href in row.locator(TT_VIDEO).evaluate_all('(els)=>els.map(e=>e.href)'):
                m = TT_ID.fullmatch(href)
                if m and m[1] not in previous and (known is None or
                          (previous and int(m[1]) > max(map(int, previous)))):
                    candidates.add(m[1])
        if len(candidates) == 1:
            return f'https://www.tiktok.com/@livespiked/video/{next(iter(candidates))}'
    elif platform == 'YT':
        rows = page.locator('ytcp-video-row, ytcp-video-section-row')
        for row in rows.filter(has_text=title).all():
            text = row.inner_text(timeout=4000).splitlines()
            if not {normalize(title), normalize(desc)} <= set(map(normalize, text)):
                continue
            values = set(map(normalize, text))
            if not {'Public', 'Öffentlich'}.intersection(values) or not {'Published', 'Veröffentlicht'}.intersection(values):
                continue
            for link in row.locator('a[href*="/video/"][href*="/edit"]').evaluate_all('(els)=>els.map(e=>e.href)'):
                m = re.search(r'/video/([A-Za-z0-9_-]{11})/edit', link)
                if m and m[1] not in previous:
                    return f'https://youtube.com/shorts/{m[1]}'
    elif platform == 'X':
        if urlsplit(page.url).path.rstrip('/').lower() != '/livespiked26':
            return None
        for article in page.locator('article').all()[:18]:
            text = article.get_by_test_id('tweetText')
            if not text.count() or normalize(text.first.text_content(timeout=4000) or '') != normalize(desc):
                continue
            if not article.locator('video, [data-testid="videoPlayer"], [data-testid="videoComponent"]').count():
                continue
            for link in article.locator('a:has(time)').evaluate_all('(els)=>els.map(e=>e.href)'):
                if (re.fullmatch(r'https://x\.com/livespiked26/status/\d+', link, re.I)
                        and link.rsplit('/', 1)[1] not in previous):
                    return link
    return None


def youtube_limit(pages):
    for page in pages:
        if urlsplit(page.url).hostname != 'studio.youtube.com':
            continue
        try:
            text = page.locator('body').inner_text(timeout=3000)
        except BrowserError:
            continue
        if re.search(r'daily upload limit reached|upload limit reached|tägliches upload.limit erreicht', text, re.I):
            return True
    return False


def readback(ctx, platform, desc, title, budget, *, preflight=False, known=None):
    url = platform_url(platform)
    if not url:
        return None
    deadline = time.monotonic() + budget
    page = ctx.new_page()

    try:
        while time.monotonic() < deadline:
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=15000)
                if platform == 'TT':
                    page.locator(TT_ROWS).filter(has=page.locator(TT_VIDEO)).first.wait_for(timeout=12000)
                else:
                    marker = 'ytcp-video-row, ytcp-video-section-row' if platform == 'YT' else 'article'
                    page.locator(marker).first.wait_for(timeout=6000)
                proof = inspect_public(page, platform, desc, title, known=known)
                if preflight:
                    ids = post_ids(page, platform)
                    if not ids:
                        raise RuntimeError(f'{platform}: cannot establish existing post IDs before submitting')
                    if proof:
                        raise RuntimeError(f'{platform}: caption already present at {proof}; no upload or cleanup authorization')
                    return sorted(ids)
                if proof:
                    return proof
            except BrowserError as exc:
                print(f'Readback {platform}: {exc}', file=sys.stderr)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                page.wait_for_timeout(min(remaining, 4)*1000)
    finally:
        page.close()
    if preflight:
        raise RuntimeError(f'{platform}: publication list could not be checked before upload')
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', choices=PLATFORMS)
    parser.add_argument('vod')
    parser.add_argument('clip')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--expected-revision')
    args = parser.parse_args()
    if not valid_key(args.vod, args.clip):
        parser.error('Invalid clip identifier')
    plat = args.platform
    script, key = PLATFORMS[plat]
    clip_file = Path.home() / 'clips/out' / args.vod / args.clip
    try:
        text = clip_file.with_suffix('.txt').read_text()
        desc = caption(text, key)
        title = text.split('TITEL:', 1)[1].splitlines()[0].strip() if plat == 'YT' else ''
    except (OSError, IndexError) as exc:
        parser.error(f'Cannot read the clip caption or title: {exc}')
    if not normalize(desc) or (plat == 'YT' and not title):
        parser.error('Empty caption or title')
    media = clip_file.with_suffix('.mp4')
    try:
        revision = media_revision(media)
    except OSError as exc:
        parser.error(f'Media unavailable: {exc}')
    live = Path.home() / '.hermes/cache/scratch/live'
    if args.expected_revision is not None and revision != args.expected_revision:
        print('Render changed after preparation; no upload attempted')
        return 2
    attempts = [r for r in history_for(live, args.vod, args.clip, revision)
                if r['platform'] == plat and r.get('state') == 'attempted']
    verify_only = args.verify_only or bool(attempts)
    if not verify_only and not voiceover_ready(media, revision):
        print('Voice-over and subtitles not verified for this MP4; no upload attempted')
        return 2
    p, b, ctx = h.connect()
    proofs = []
    managed = {}
    h.managed_session = (p, b, ctx, managed, proofs)
    trace_started = False
    confirmed = None
    submitted = bool(attempts)
    known = attempts[-1].get('known') if submitted else None

    def mark():
        nonlocal submitted
        if submitted:
            raise RuntimeError('Refusing to submit twice')
        if media_revision(media) != revision or not voiceover_ready(media, revision):
            raise RuntimeError('Media or voice-over confirmation changed before submission')
        save_attempt(live, args.vod, args.clip, revision, plat, known)
        submitted = True

    h.before_submit = mark
    try:
        if not verify_only:
            try:
                ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
                trace_started = True
            except BrowserError as exc:
                print(f'Trace unavailable: {exc}', file=sys.stderr)
        if verify_only and not submitted:
            proof = readback(ctx, plat, desc, title, 25)
            print(f'Found public post (not tied to this render): {proof}' if proof else 'No matching public post')
            return 2
        if not verify_only:
            known = readback(ctx, plat, desc, title, 15, preflight=True)
            sys.argv = [script, args.vod, args.clip]
            try:
                runpy.run_path(str(Path(__file__).with_name(script)), run_name='__main__')
                if plat == 'X':
                    runpy.run_path(str(Path(__file__).with_name('x3.py')), run_name='__main__')
            except (Exception, SystemExit) as exc:
                print(f'{plat} uploader: {exc}', file=sys.stderr)
        if submitted:
            if platform_url(plat) and isinstance(known, list) and known:
                confirmed = readback(ctx, plat, desc, title, 25 if verify_only else 55, known=known)
            elif not verify_only and not platform_url(plat):
                confirmed = next((proof for platform, proof in proofs if platform == plat), None)
        if not confirmed:
            if plat == 'YT' and youtube_limit(managed.values()):
                print('YouTube reports its upload limit was reached.')
                return 11
            print('UNCERTAIN: submission not verified; do not retry blindly' if submitted else
                  'No publication clicked; pre-submit failure')
            return 2
        if media_revision(media) != revision:
            confirmed = None
            print('Media changed during posting; no proof recorded for the current render')
            return 2
        append(live / 'publish_attempts.jsonl', dict(vod=args.vod, clip=args.clip, revision=revision,
               platform=plat, ts=int(time.time()), state='confirmed', proof=confirmed))
        print(f'VERIFIED: {confirmed}')
        return 10 if verify_only else 0
    finally:
        if trace_started:
            try:
                if confirmed:
                    ctx.tracing.stop()
                else:
                    folder = Path(h.OUT) / 'diagnostics'
                    folder.mkdir(mode=0o700, exist_ok=True)
                    os.chmod(folder, 0o700)
                    trace = folder / f'{args.vod}-{args.clip}-{plat}-{time.time_ns()}.zip'
                    ctx.tracing.stop(path=str(trace))
                    trace.chmod(0o600)
                    print(f'Trace: {trace}')
            except (OSError, BrowserError) as exc:
                print(f'Trace could not be saved: {exc}', file=sys.stderr)
        for page in managed.values():
            if plat == 'SC' and not confirmed:
                print('Snapchat tab left open for inspection; check the profile before any manual retry.')
                continue
            try:
                page.close()
            except BrowserError:
                pass
        h.managed_session = None
        h.before_submit = None
        p.stop()


if __name__ == '__main__':
    raise SystemExit(main())
