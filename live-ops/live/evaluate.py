#!/usr/bin/env python3
"""Evaluation gate before posting a clip (scheme v2, 22.11.2026).

Usage:
  evaluate.py <rec_id> <clip_id> --cand <cXXX> --standalone 0-10 --payoff 0-10 --relevance 0-10
              [--hook 0-10] [--visual 0-10] [--novelty 0-10] [--reject REASON]
Prints JSON with score (0-100), decision (post|hold|reject) and reasons. Appends to evals.jsonl.

Score = 30 objective + 70 subjective

  objective is ABSOLUTE (not relative to the best of the batch, so a weak batch no longer gets 30 points for free):
    detect score    / REF_DETECT (15 points, cap 1)
    unique chatters / REF_CHAT   ( 7.5 points, cap 1)
    live spike z    / REF_Z      ( 7.5 points, cap 1)   (without live spikes: --views / REF_VIEWS)

  subjective (my judgement from transcript + stills, 0-10 each). Scale: 5 = a clip I would post on a normal day,
  8+ = stands out, 2 = filler. Weights add up to 7 (so 70 points):
    standalone (x1.0): understandable for a stranger in 2 s without stream context
    payoff     (x1.0): clear reaction/punchline, ends right after it
    relevance  (x1.0): big moment (game start, record, first-time reveal, viral potential)
    hook       (x1.5): something happens in the first 3 s (line, scream, visual) that makes people stay
    visual     (x1.5): the picture alone explains it (physics gag, animal, crash), works without sound
    novelty    (x1.0): first-time / new in GTA 6 (map, mechanics, bugs, launch topics)
  If --hook/--visual/--novelty are missing they default to the mean of standalone/payoff/relevance.
  Rants and long talk get NO deduction: they are rated on the same criteria like everything else.

Gate:
  reject  if --reject given (giveaway, settings/sound, greeting, ads, insults/ban risk)
  post    if score >= threshold (default 80) AND >= GAP since last post. Batch: evaluate all, post only the best.
  hold    otherwise (kept as file, not posted). daily_best.py picks the best held clip of the day.
"""
import argparse, json, os, time, random
GAP = 1200 + random.randint(-300, 300)  # min. Abstand 15-25 min (zufällig), dazu max 3 Posts / 60 min

# Reference values for 100 % of each objective part (absolute, tune with collect_views.py data)
REF_DETECT = 20.0   # detect_highlights score (observed range 3-27)
REF_CHAT = 40       # unique chatters around the moment
REF_Z = 6.0         # live spike z
REF_VIEWS = 5000.0  # Twitch views of the source clip
W = dict(standalone=1.0, payoff=1.0, relevance=1.0, hook=1.5, visual=1.5, novelty=1.0)

D = os.path.expanduser('~/.hermes/cache/scratch/live')

ap = argparse.ArgumentParser()
ap.add_argument('rec'); ap.add_argument('clip')
ap.add_argument('--cand', required=True)
ap.add_argument('--standalone', type=float, required=True)
ap.add_argument('--payoff', type=float, required=True)
ap.add_argument('--relevance', type=float, required=True)
ap.add_argument('--hook', type=float, default=None)
ap.add_argument('--visual', type=float, default=None)
ap.add_argument('--novelty', type=float, default=None)
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
spikes_f = os.path.join(D, f'{a.rec}_spikes.jsonl')
spikes = [json.loads(l) for l in open(spikes_f)] if os.path.exists(spikes_f) else []
m = c['moment_s'] if c else None
near = [s for s in spikes if m is not None and abs(s['moment'] - m) <= 45]
z = max([s['z'] for s in near] + [0])

obj = 0.0
if c:
    obj += 15 * min(1, c.get('score', 0) / REF_DETECT)
    obj += 7.5 * min(1, c.get('signals', {}).get('uniq_chatters', 0) / REF_CHAT)
if spikes:
    obj += 7.5 * min(1, z / REF_Z)
elif a.views > 0:
    obj += 7.5 * min(1, a.views / REF_VIEWS)

base = (a.standalone + a.payoff + a.relevance) / 3
vals = dict(standalone=a.standalone, payoff=a.payoff, relevance=a.relevance,
            hook=base if a.hook is None else a.hook,
            visual=base if a.visual is None else a.visual,
            novelty=base if a.novelty is None else a.novelty)
subj = sum(W[k] * vals[k] for k in W)
score = round(obj + subj, 1)

streamer = a.rec.rsplit('-', 2)[0]
if a.threshold is None: a.threshold = 80  # >=80 posten, 70-79 sichern (User 19.11.)
now = time.time()
hist = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
posted = [h for h in hist if h.get('decision') == 'post']

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

rec = dict(v=2, gap_s=GAP, ts=now, rec=a.rec, clip=a.clip, cand=a.cand, streamer=streamer, score=score,
           objective=round(obj, 1), subjective=round(subj, 1), standalone=a.standalone, payoff=a.payoff,
           relevance=a.relevance, hook=vals['hook'], visual=vals['visual'], novelty=vals['novelty'],
           decision=decision, reasons=reasons)
os.makedirs(os.path.dirname(LOG) or '.', exist_ok=True)
open(LOG, 'a').write(json.dumps(rec) + '\n')
print(json.dumps(rec, ensure_ascii=False))
