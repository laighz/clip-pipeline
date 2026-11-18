#!/usr/bin/env python3
"""Bewertungs-Gate vor jedem Post (Pflicht im Live-Modus).

Usage:
  evaluate.py <rec_id> <clip_id|-> --cand cX --standalone 0-10 --payoff 0-10 --relevance 0-10
              [--reject GRUND] [--threshold 75] [--spikes PATH] [--gap-min 30] [--jitter-min 5]

Score (0-100) = 40 objektiv + 60 eigene Einschaetzung
  objektiv: detect-Score rel. Session-Max (20), uniq_chatters rel. Max (10), Live-Spike-z (+-45 s) rel. Max (10)
  eigen (je 0-10, x2): standalone = ein Fremder versteht es in 2 s ohne Kontext;
                       payoff = klare Reaktion/Pointe, Ende direkt danach;
                       relevance = grosser Moment (Game-Start, erste Male, Rekord, virales Potenzial)
Entscheidung:
  reject  bei --reject (Gewinnspiel/Keyword-Welle, Settings/Ton-Check, Begruessung, Werbung, Cutscene, Beleidigungen)
  hold    Score < threshold ODER letzter Post juenger als gap (Standard 60 min +- zufaellig 0-5 min, pro Aufruf neu gewuerfelt)
  post    sonst. Im Batch alle Kandidaten bewerten, nur den besten 'post' wirklich posten.
Log: ~/clips/evals.jsonl (eine Zeile pro Bewertung; Eintraege mit decision=post zaehlen fuer den Takt).
"""
import argparse, json, os, random, time

ap = argparse.ArgumentParser()
ap.add_argument('rec'); ap.add_argument('clip')
ap.add_argument('--cand', required=True)
ap.add_argument('--standalone', type=float, required=True)
ap.add_argument('--payoff', type=float, required=True)
ap.add_argument('--relevance', type=float, required=True)
ap.add_argument('--reject', default='')
ap.add_argument('--threshold', type=float, default=75)
ap.add_argument('--spikes', default=None, help='JSONL mit {n,moment,z} der Live-Spikes dieser Aufnahme')
ap.add_argument('--gap-min', type=float, default=60)
ap.add_argument('--jitter-min', type=float, default=5)
a = ap.parse_args()

LOG = os.path.expanduser('~/clips/evals.jsonl')
GAP = a.gap_min * 60 + random.randint(-int(a.jitter_min * 60), int(a.jitter_min * 60))
work = os.path.expanduser(f'~/clips/work/{a.rec}/')
cands = json.load(open(work + 'candidates.json'))['candidates']
c = next((x for x in cands if x['id'] == a.cand), None)
mx_score = max([x.get('score', 0) for x in cands] + [1e-9])
mx_uniq = max([x.get('signals', {}).get('uniq_chatters', 0) for x in cands] + [1])
sp = a.spikes or os.path.expanduser(f'~/.hermes/cache/scratch/live/{a.rec}_spikes.jsonl')
spikes = [json.loads(l) for l in open(sp) if l.strip()] if os.path.exists(sp) else []
mx_z = max([s['z'] for s in spikes] + [1e-9])
m = c['moment_s'] if c else None
z = max([s['z'] for s in spikes if m is not None and abs(s['moment'] - m) <= 45] + [0])

obj = 0.0
if c:
    obj += 20 * min(1, c.get('score', 0) / mx_score)
    obj += 10 * min(1, c.get('signals', {}).get('uniq_chatters', 0) / mx_uniq)
obj += 10 * min(1, z / mx_z)
subj = 2 * (a.standalone + a.payoff + a.relevance)
score = round(obj + subj, 1)

now = time.time()
hist = [json.loads(l) for l in open(LOG) if l.strip()] if os.path.exists(LOG) else []
posted = [h for h in hist if h.get('decision') == 'post']
reasons = []
if a.reject:
    decision = 'reject'; reasons.append(a.reject)
elif score < a.threshold:
    decision = 'hold'; reasons.append(f'score {score} < {a.threshold}')
elif posted and now - max(h['ts'] for h in posted) < GAP:
    decision = 'hold'; reasons.append(f'letzter Post < {round(GAP/60)} min her')
else:
    decision = 'post'

rec = dict(ts=now, gap_s=GAP, rec=a.rec, clip=a.clip, cand=a.cand, streamer=a.rec.rsplit('-', 2)[0],
           score=score, objective=round(obj, 1), subjective=subj, standalone=a.standalone,
           payoff=a.payoff, relevance=a.relevance, decision=decision, reasons=reasons)
os.makedirs(os.path.dirname(LOG), exist_ok=True)
open(LOG, 'a').write(json.dumps(rec) + '\n')
print(json.dumps(rec, ensure_ascii=False))
