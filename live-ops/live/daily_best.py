#!/usr/bin/env python3
"""Best unposted clips of the day + protection list for the janitor (user rules 22.11.2026).

Usage: daily_best.py [--top 3] [--good 60] [--date YYYY-MM-DD] [--save]
List = the TOP best non-rejected evaluations of the day (default 3), plus every further one with score >= --good
("unless there are very many good ones"). Already posted clips are skipped.
--save writes live/daily_best.json (one list per date, overwritten on every run). The janitor never deletes the raw
segments around those moments (pins them) for 48 h after the end of that day.
"""
import argparse, datetime, json, os

LIVE = os.path.expanduser('~/.hermes/cache/scratch/live')
ap = argparse.ArgumentParser()
ap.add_argument('--top', type=int, default=3)
ap.add_argument('--good', type=float, default=60)
ap.add_argument('--date', default=datetime.date.today().isoformat())
ap.add_argument('--save', action='store_true')
a = ap.parse_args()

ev = [json.loads(l) for l in open(f'{LIVE}/evals.jsonl') if l.strip()]
posted = set()
p = f'{LIVE}/posted_clips.jsonl'
if os.path.exists(p):
    posted = {(r['vod'], r['clip']) for r in (json.loads(l) for l in open(p) if l.strip())}

best = {}
for e in ev:
    if e.get('decision') == 'reject' or e.get('score') is None: continue
    if datetime.date.fromtimestamp(e['ts']).isoformat() != a.date: continue
    if (e['rec'], e.get('clip')) in posted: continue
    cand = str(e.get('cand') or '')
    if not cand[1:].isdigit(): continue
    k = (e['rec'], cand)
    if k not in best or e['score'] > best[k]['score']: best[k] = e

rows = []
for (rec, cand), e in best.items():
    t = int(cand[1:])
    raw_ok = False
    mp = os.path.expanduser(f'~/clips/work/{rec}/meta.json')
    if os.path.exists(mp):
        for g in json.load(open(mp)).get('segments', []):
            if g['start'] - 30 <= t <= g['end'] + 30 and os.path.exists(g['file']): raw_ok = True
    rows.append(dict(score=e['score'], streamer=e['streamer'], rec=rec, cand=cand, moment_s=t, raw_still_there=raw_ok))
rows.sort(key=lambda r: -r['score'])
chosen = [r for i, r in enumerate(rows) if i < a.top or r['score'] >= a.good]

for r in chosen: print(json.dumps(r, ensure_ascii=False))
if not chosen: print('nothing evaluated on', a.date)
if a.save:
    f = f'{LIVE}/daily_best.json'
    d = json.load(open(f)) if os.path.exists(f) else {}
    d[a.date] = chosen
    for k in sorted(d)[:-5]: d.pop(k)          # keep the last 5 days only
    json.dump(d, open(f, 'w'), indent=1)
