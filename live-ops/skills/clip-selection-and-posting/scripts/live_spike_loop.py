"""Stille Spike-Sammlung fuer eine Live-Aufnahme (Weg C).

Ruft record_status.py --wait-spike in einer Schleife auf, haengt neue Spikes an
~/.hermes/cache/scratch/<rec_id>_spikes.jsonl und schreibt den letzten Status nach
<rec_id>_status.json. Standard: endet erst, wenn die Aufnahme nicht mehr 'recording' ist.

Optionen:
  --max-hours H       nach H Stunden record_stop.py fuer den Kanal aufrufen
                      ("stoppen wenn er aufhoert, spaetestens in H Stunden").
  --exit-on-ready     Live-Modus ("aktiv schneiden und sofort posten"): endet auch,
                      sobald ein noch nicht verarbeiteter Spike fertig aufgenommen ist
                      (recorded_until_s >= moment + 60). Ausgabe: READY <rec_id> + JSON-Liste.
                      Verarbeitete Spike-n in <rec_id>_done.txt (eine Zahl pro Zeile)
                      eintragen, dann das Skript neu starten (mit der rec_id aus READY).
  --follow-restarts   Streamer startet den Stream neu (Test-Starts, Abbrueche): bei END
                      alle 20 s record_start.py fuer den Kanal versuchen und mit der neuen
                      rec_id still weitermachen (Zeile RESTART nach <kanal>_restarts.log).
                      Endet erst mit GONE, wenn der Kanal --offline-min (Standard 60) Minuten
                      offline ist. So gibt es pro Neustart keinen Weckruf.

Start als EIN Hintergrundprozess mit notify (pro Kanal einer):
  python3 live_spike_loop.py <rec_id> [--max-hours 2] [--exit-on-ready] [--follow-restarts]
"""
import json, os, subprocess, sys, time

R = sys.argv[1]
argv = sys.argv
max_h = float(argv[argv.index('--max-hours') + 1]) if '--max-hours' in argv else None
offline_min = float(argv[argv.index('--offline-min') + 1]) if '--offline-min' in argv else 60
exit_on_ready = '--exit-on-ready' in argv
follow = '--follow-restarts' in argv
channel = R.rsplit('-', 2)[0]
H = os.path.expanduser('~/.hermes/cache/scratch/')
SKILL = '/Users/moritz/Documents/#code/clip-pipeline/clip-forge/skills/clipping-streams'
start = time.time()
stopped = False


def paths(rec):
    return H + f'{rec}_spikes.jsonl', H + f'{rec}_status.json', H + f'{rec}_done.txt'


F, ST, DONE = paths(R)
for p in (F, DONE):
    open(p, 'a').close()


def ready_spikes(recorded_until):
    done = {l.strip() for l in open(DONE) if l.strip()}
    sp = [json.loads(l) for l in open(F) if l.strip()]
    return [s for s in sp if str(s['n']) not in done and s['moment'] + 60 <= recorded_until]


def try_restart():
    """record_start bis zum Erfolg oder offline_min; gibt neue rec_id oder None."""
    t0 = time.time()
    while time.time() - t0 < offline_min * 60:
        p = subprocess.run(['uv', 'run', '--quiet', f'{SKILL}/scripts/record_start.py', '--channel', channel],
                           capture_output=True, text=True)
        for l in p.stdout.splitlines():
            if l.strip().startswith('{'):
                d = json.loads(l)
                # 'läuft schon' = Worker lief bereits (Status kam nur verzögert); gleiche rec_id weiterverwenden
                if d.get('status') in ('recording', 'läuft schon') and d.get('rec_id'):
                    return d['rec_id']
        time.sleep(20)
    return None


while True:
    if max_h and not stopped and time.time() - start > max_h * 3600:
        subprocess.run(['uv', 'run', '--quiet', f'{SKILL}/scripts/record_stop.py', '--channel', channel],
                       capture_output=True, text=True)
        stopped = True
    ns = [json.loads(l)['n'] for l in open(F) if l.strip()]
    n = max(ns) if ns else 0
    timeout = 540
    if max_h and not stopped:
        timeout = max(30, min(540, int(max_h * 3600 - (time.time() - start))))
    proc = subprocess.run(
        ['uv', 'run', '--quiet', f'{SKILL}/scripts/record_status.py', '--rec-id', R,
         '--wait-spike', '--after', str(n), '--timeout', str(timeout)],
        capture_output=True, text=True)
    lines = [l for l in proc.stdout.splitlines() if l.strip().startswith('{')]
    if not lines:
        time.sleep(30)
        continue
    d = json.loads(lines[-1])
    with open(ST, 'w') as f:
        json.dump(d, f)
    with open(F, 'a') as f:
        for s in d.get('new_spikes', []):
            f.write(json.dumps({'n': s['n'], 'moment': s['moment'], 'z': s['z'], 'uniq': s.get('uniq')}) + '\n')
    if exit_on_ready:
        rdy = ready_spikes(d.get('recorded_until_s') or 0)
        if rdy:
            print('READY', R, json.dumps(rdy))
            break
    if d.get('status') != 'recording' or d.get('ended'):
        if follow and not stopped:
            new = try_restart()
            if new:
                with open(H + f'{channel}_restarts.log', 'a') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} RESTART {R} -> {new}\n')
                R = new
                F, ST, DONE = paths(R)
                for p in (F, DONE):
                    open(p, 'a').close()
                continue
            print('GONE', channel, 'last', R)
            break
        print('END', R, d.get('status'), 'recorded_until_s', d.get('recorded_until_s'))
        break
