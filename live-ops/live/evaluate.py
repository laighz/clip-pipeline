#!/usr/bin/env python3
"""Evaluation gate before posting a clip.

Usage:
  evaluate.py <rec_id> <clip_id> --cand <cXXX> --standalone 0-10 --payoff 0-10 --relevance 0-10 [--reject REASON]
Prints JSON with score (0-100), decision (post|hold|reject) and reasons. Appends to evals.jsonl.

Score = 40 objective + 60 subjective
  objective: detect score relative to session max (20), unique chatters relative to max (10), live spike z relative to max (10)
  subjective (my judgement from transcript + stills, 0-10 each, weighted 2x):
    standalone: understandable for a stranger in 2 s without stream context
    payoff:     clear reaction/punchline, ends right after it
    relevance:  big moment (game start, record, first-time reveal, viral potential)
Gate:
  reject  if --reject given (giveaway, settings/sound, greeting, ads, insults/ban risk)
  post    if score >= 75 AND >= 30 min since last post (overall). Batch: evaluate all, post only the best.
  hold    otherwise (kept as file, not posted)
"""
import argparse, json, os, time, random
GAP = 1200 + random.randint(-300, 300)  # min. Abstand 15-25 min (zufällig), dazu max 3 Posts / 60 min

D = os.path.expanduser('~/.hermes/cache/scratch/live')

ap = argparse.ArgumentParser()
ap.add_argument('rec'); ap.add_argument('clip')
ap.add_argument('--cand', required=True)
ap.add_argument('--standalone', type=float, required=True)
ap.add_argument('--payoff', type=float, required=True)
ap.add_argument('--relevance', type=float, required=True)
ap.add_argument('--reject', default='')
ap.add_argument('--threshold', type=float, default=None)
ap.add_argument('--views', type=float, default=0,
                help='Twitch-Views des Quellclips; ersetzt den Spike-Anteil, wenn es keinen Live-Spike gibt')
ap.add_argument('--work', default='', help='Arbeitsordner statt ~/clips/work/<rec>')
ap.add_argument('--log', default='', help='evals.jsonl statt dem Live-Log')
a = ap.parse_args()
LOG = a.log or os.path.join(D, 'evals.jsonl')

work = a.work or os.path.expanduser(f'~/clips/work/{a.rec}/')
cands = json.load(open(work + 'candidates.json'))['candidates']
c = next((x for x in cands if x['id'] == a.cand), None)
mx_score = max([x.get('score', 0) for x in cands] + [1e-9])
mx_uniq = max([x.get('signals', {}).get('uniq_chatters', 0) for x in cands] + [1])
spikes_f = os.path.join(D, f'{a.rec}_spikes.jsonl')
spikes = [json.loads(l) for l in open(spikes_f)] if os.path.exists(spikes_f) else []
mx_z = max([s['z'] for s in spikes] + [1e-9])
m = c['moment_s'] if c else None
near = [s for s in spikes if m is not None and abs(s['moment'] - m) <= 45]
z = max([s['z'] for s in near] + [0])

obj = 0.0
if c:
    obj += 20 * min(1, c.get('score', 0) / mx_score)
    obj += 10 * min(1, c.get('signals', {}).get('uniq_chatters', 0) / mx_uniq)
if spikes:
    obj += 10 * min(1, z / mx_z)
elif a.views > 0:
    obj += 10
subj = 2 * (a.standalone + a.payoff + a.relevance)
score = round(obj + subj, 1)

streamer = a.rec.rsplit('-', 2)[0]
if a.threshold is None: a.threshold = 80  # >=80 posten, 70-79 sichern (User 19.11.)
now = time.time()
hist = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
posted = [h for h in hist if h.get('decision') == 'post']
last_streamer = [h for h in posted if h['streamer'] == streamer and now - h['ts'] < 3600]
last_hour = [h for h in posted if now - h['ts'] < 3600]

reasons = []
if a.reject:
    decision = 'reject'; reasons.append(a.reject)
elif score < a.threshold:
    decision = 'hold'; reasons.append(f'score {score} < {a.threshold}')
elif len([h for h in posted if now - h['ts'] < 3600]) >= 3:
    decision = 'hold'; reasons.append('schon 3 Posts in den letzten 60 min')
elif posted and now - max(h['ts'] for h in posted) < GAP:
    decision = 'hold'; reasons.append(f'letzter Post < {GAP//60} min her (zufälliger Abstand)')
else:
    decision = 'post'

rec = dict(gap_s=GAP, ts=now, rec=a.rec, clip=a.clip, cand=a.cand, streamer=streamer, score=score,
           objective=round(obj, 1), subjective=subj, standalone=a.standalone, payoff=a.payoff,
           relevance=a.relevance, decision=decision, reasons=reasons)
os.makedirs(os.path.dirname(LOG) or '.', exist_ok=True)
open(LOG, 'a').write(json.dumps(rec) + '\n')
print(json.dumps(rec, ensure_ascii=False))
