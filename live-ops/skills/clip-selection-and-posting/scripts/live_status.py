"""Kurze /status-Uebersicht fuer eine Live-Aufnahme.
Usage: python3 live_status.py <rec_id>
Liest <rec_id>_spikes.jsonl und <rec_id>_status.json (von live_spike_loop.py).
Score = z relativ zum staerksten Spike (=100). Vorlaeufig; final zaehlt candidates.json.
"""
import json, os, shutil, sys

R = sys.argv[1]
H = os.path.expanduser('~/.hermes/cache/scratch/')
sp = [json.loads(l) for l in open(H + f'{R}_spikes.jsonl') if l.strip()]
try:
    st = json.load(open(H + f'{R}_status.json'))
except Exception:
    st = {}
rec = os.path.expanduser(f'~/clips/raw/live/{R}')
gb = sum(os.path.getsize(os.path.join(rec, f)) for f in os.listdir(rec)) / 1e9 if os.path.isdir(rec) else 0
print(f"Status: {st.get('status', '?')} | aufgenommen bis {st.get('recorded_until_s', 0)/60:.0f} min | {gb:.1f} GB | Spikes: {len(sp)}")
if sp:
    top = sorted(sp, key=lambda s: -s['z'])[:5]
    mx = top[0]['z']
    print('Top 5 (vorlaeufig):', ', '.join(f"min {s['moment']/60:.0f} ({round(100*s['z']/mx)})" for s in top))
