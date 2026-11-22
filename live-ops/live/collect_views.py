#!/usr/bin/env python3
"""Collect views/likes/comments of posted clips from YouTube Studio and TikTok Studio (via the CDP Chrome on :9333).

Usage: collect_views.py            # snapshot -> live/views.jsonl and clips.db table `results`
Matches rows to clips by title/caption (work/*/clips.json). Unmatched rows are still written to views.jsonl.
Snapchat is not scraped (its spotlight list has no captions); add by hand if needed.
Run it ~24 h after posting (hours_after is computed from the date the platform shows) and again after 72 h.
"""
import datetime, glob, json, os, re, sqlite3, sys, time
from playwright.sync_api import sync_playwright

LIVE = os.path.expanduser('~/.hermes/cache/scratch/live')
YT = 'https://studio.youtube.com/channel/UCwCaCEXf-nQ0SbLcGi2Bv5Q/videos/short'
TT = 'https://www.tiktok.com/tiktokstudio/content'


def norm(s):
    return re.sub(r'[^a-z0-9äöüß]+', ' ', s.lower()).strip()[:45]


def clip_index():
    idx = {}
    for f in glob.glob(os.path.expanduser('~/clips/work/*/clips.json')):
        vod = f.split('/')[-2]
        try: d = json.load(open(f))
        except Exception: continue
        for c in d.get('clips', []):
            keys = [c.get('title', '')] + list((c.get('captions') or {}).values())
            for k in keys:
                if k: idx[norm(k)] = (vod, c['id'])
    return idx


def num(s):
    s = s.strip().upper()
    mult = 1
    if s.endswith('K'): s, mult = s[:-1], 1000
    elif s.endswith('M'): s, mult = s[:-1], 1000000
    if re.fullmatch(r'\d{1,3}(,\d{3})+', s): s = s.replace(',', '')
    return int(float(s.replace(',', '.')) * mult) if re.fullmatch(r'\d+([.,]\d+)?', s) else None


def parse_dt(s, fmt_list):
    s = s.replace('\u202f', ' ')
    for fmt in fmt_list:
        try: return datetime.datetime.strptime(s, fmt)
        except ValueError: pass
    return None


def tiktok_rows(text):
    t = [l.strip() for l in text.split('\n')]
    out = []
    for i, l in enumerate(t):
        if 'Clip: @' in l and i + 5 < len(t):
            dt = parse_dt(t[i + 1], ['%b %d, %I:%M %p']) if i + 1 < len(t) else None
            v, li, co = num(t[i + 3]), num(t[i + 4]), num(t[i + 5])
            if dt: dt = dt.replace(year=datetime.date.today().year)
            out.append(dict(platform='tiktok', text=l, views=v, likes=li, comments=co, posted=dt.isoformat() if dt else None))
    return out


def youtube_rows(text):
    t = [l.strip() for l in text.split('\n')]
    out = []
    for i, l in enumerate(t):
        if l.endswith('#Shorts') and i + 7 < len(t) and t[i + 5] in ('Published', 'Veröffentlicht'):
            dt = parse_dt(t[i + 4], ['%b %d, %Y'])
            out.append(dict(platform='youtube', text=l, views=num(t[i + 6]), likes=None,
                            comments=num(t[i + 7]),
                            posted=dt.isoformat() if dt else None))
    return out


def main():
    idx = clip_index()
    rows = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp('http://localhost:9333')
        ctx = b.contexts[0]
        for url, fn in ((TT, tiktok_rows), (YT, youtube_rows)):
            pg = ctx.new_page()
            try:
                pg.goto(url, wait_until='domcontentloaded'); pg.wait_for_timeout(8000)
                rows += fn(pg.inner_text('body'))
            finally:
                pg.close()
    now = datetime.datetime.now()
    con = sqlite3.connect(os.path.expanduser('~/clips/clips.db'))
    con.execute('CREATE TABLE IF NOT EXISTS results (vod_id TEXT, clip_id TEXT, platform TEXT, hours_after INTEGER, views INTEGER, '
                'stayed_pct REAL, avg_watch_s REAL, shares INTEGER, follows INTEGER, comments INTEGER, recorded_at TEXT, '
                'PRIMARY KEY (vod_id, clip_id, platform, hours_after))')
    cols = {row[1] for row in con.execute('PRAGMA table_info(results)')}
    if 'comments' not in cols: con.execute('ALTER TABLE results ADD COLUMN comments INTEGER')
    matched = 0
    with open(f'{LIVE}/views.jsonl', 'a') as fh:
        for r in rows:
            key = norm(r['text'])
            m = next((v for k, v in idx.items() if k and (key.startswith(k[:30]) or k.startswith(key[:30]))), None)
            hours = None
            if r['posted']:
                hours = int((now - datetime.datetime.fromisoformat(r['posted'])).total_seconds() // 3600)
            r.update(ts=time.time(), vod=m[0] if m else None, clip=m[1] if m else None, hours_after=hours)
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
            if m and hours is not None:
                matched += 1
                con.execute('INSERT OR REPLACE INTO results (vod_id, clip_id, platform, hours_after, views, comments, recorded_at) '
                            'VALUES (?,?,?,?,?,?,?)', (m[0], m[1], r['platform'], hours, r['views'], r['comments'], now.isoformat()))
    con.commit(); con.close()
    print(json.dumps(dict(rows=len(rows), matched_to_clips=matched, file=f'{LIVE}/views.jsonl')))


if __name__ == '__main__':
    main()
