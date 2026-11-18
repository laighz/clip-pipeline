#!/usr/bin/env python3
"""Smart trash bin for live raw segments (~/clips/raw/live/<rec>/seg_NNNN.ts).

Pipeline per run:
 1. Measure free disk + burn rate (GB/h written by all recordings, last 30 min).
    free_h = hours of recording left. RESERVE_H = minimum headroom we want.
 2. Derive retention dynamically:
      ttl   = clamp((free_h - RESERVE_H) / 3, 15 min, 4 h)   # how long analysed segments stay
      grace = clamp((free_h - RESERVE_H) / 6, 10 min, 2 h)   # how long they sit in the trash bin
 3. A segment becomes trash only if ALL hold:
      - not the segment currently being written (and not last 2 of an alive rec)
      - fully analysed: covered by meta.json duration_s and candidates.json is newer than prepared_at
      - older than ttl (by file mtime = end of segment)
      - not protected. Protected = contains (±60 s) a candidate that
          * was evaluated with score >= KEEP_SCORE and never posted (user may want it later), or
          * is not evaluated yet and has a strong raw signal (score >= 5) and is < 2 h old,
          * or is listed in keep.txt (manual pin: "<rec> <seconds>")
        Protected segments are kept up to PROTECT_MAX_H, unless disk is critical.
 4. Trash = move into ~/clips/trash/<rec>/ (same volume, instant, restorable with --restore <rec>).
 5. Purge trash entries older than grace; if free_h < RESERVE_H purge oldest first right away;
    if still critical (free_h < CRIT_H) also trash+purge protected segments, lowest score first.
 6. Remove empty dirs of dead recordings. Log every action to janitor.log.

Usage: janitor.py [--dry-run] [--restore REC]
"""
import json, os, shutil, sys, time, glob

RAW = os.path.expanduser('~/clips/raw/live')
WORK = os.path.expanduser('~/clips/work')
TRASH = os.path.expanduser('~/clips/trash')
LIVE = os.path.expanduser('~/.hermes/cache/scratch/live')
EVALS = f'{LIVE}/evals.jsonl'
LOG = f'{LIVE}/janitor.log'
KEEP_FILE = f'{LIVE}/keep.txt'
RESERVE_H, CRIT_H = 3.0, 1.5
KEEP_SCORE, PROTECT_MAX_H = 50, 24
DRY = '--dry-run' in sys.argv
now = time.time()
clamp = lambda v, lo, hi: max(lo, min(hi, v))


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {'[dry] ' if DRY else ''}{msg}"
    print(line)
    if not DRY:
        open(LOG, 'a').write(line + '\n')


def free_gb():
    return shutil.disk_usage(RAW).free / 1e9


def burn_gbh():
    recent = [f for f in glob.glob(f'{RAW}/*/seg_*.ts') if now - os.path.getmtime(f) < 1800]
    alive = {os.path.dirname(f) for f in recent if now - os.path.getmtime(f) < 120}
    by_ctime = sum(os.path.getsize(f) for f in recent if now - os.stat(f).st_birthtime < 1800)
    return max(by_ctime / 1e9 / 0.5, 2.8 * len(alive), 1.0), alive


def evals():
    out = {}
    if os.path.exists(EVALS):
        for l in open(EVALS):
            try: e = json.loads(l)
            except Exception: continue
            k = (e.get('rec'), e.get('cand'))
            if e.get('decision') == 'post': out[k] = 'posted'
            elif out.get(k) != 'posted': out[k] = max(out.get(k, 0) if isinstance(out.get(k), (int, float)) else 0, e.get('score') or 0)
    return out


def pins():
    p = {}
    if os.path.exists(KEEP_FILE):
        for l in open(KEEP_FILE):
            a = l.split()
            if len(a) >= 2: p.setdefault(a[0], []).append(float(a[1]))
    return p


def segments(rec):
    csv = f'{RAW}/{rec}/segments.csv'
    segs = []
    if os.path.exists(csv):
        for l in open(csv):
            a = l.strip().split(',')
            if len(a) == 3 and os.path.exists(f'{RAW}/{rec}/{a[0]}'):
                segs.append((a[0], float(a[1]), float(a[2])))
    return segs


def restore(rec):
    src = f'{TRASH}/{rec}'
    for f in glob.glob(f'{src}/seg_*.ts'):
        shutil.move(f, f'{RAW}/{rec}/{os.path.basename(f)}')
    log(f'restore {rec}')


if '--restore' in sys.argv:
    restore(sys.argv[sys.argv.index('--restore') + 1]); sys.exit()

fg = free_gb(); burn, alive = burn_gbh(); free_h = fg / burn
ttl = clamp((free_h - RESERVE_H) / 3, 0.25, 4) * 3600
grace = clamp((free_h - RESERVE_H) / 6, 1 / 6, 2) * 3600
ev, pin = evals(), pins()
log(f'free {fg:.1f} GB, burn {burn:.1f} GB/h -> {free_h:.1f} h left; ttl {ttl/60:.0f} min, grace {grace/60:.0f} min')

# Streamer offline > OFFLINE_MIN: delete ALL raw of that channel (user rule). Supervisors keep running.
OFFLINE_MIN = 30
chan_last = {}
for d in glob.glob(f'{RAW}/*'):
    fs = glob.glob(f'{d}/seg_*.ts') + glob.glob(f'{d}/*.jsonl')
    if fs: ch = os.path.basename(d).rsplit('-', 2)[0]; chan_last[ch] = max(chan_last.get(ch, 0), *map(os.path.getmtime, fs))
for ch, t in chan_last.items():
    if now - t > OFFLINE_MIN * 60 and not any(os.path.basename(a).startswith(ch + '-') for a in alive):
        for d in glob.glob(f'{RAW}/{ch}-*') + glob.glob(f'{TRASH}/{ch}-*'):
            log(f'offline {ch} > {OFFLINE_MIN} min: delete {os.path.basename(d)}')
            if not DRY: shutil.rmtree(d, ignore_errors=True)

trashable, protected = [], []
for rec in sorted(os.listdir(RAW)):
    d = f'{RAW}/{rec}'
    if not os.path.isdir(d): continue
    segs = segments(rec)
    if not segs: continue
    is_alive = d in alive
    try:
        meta = json.load(open(f'{WORK}/{rec}/meta.json'))
        analysed_until = meta.get('duration_s', 0)
        cand_t = os.path.getmtime(f'{WORK}/{rec}/candidates.json')
        prep_t = time.mktime(time.strptime(meta['prepared_at'][:19], '%Y-%m-%dT%H:%M:%S'))
        if cand_t < prep_t: analysed_until = 0
        cands = json.load(open(f'{WORK}/{rec}/candidates.json'))['candidates']
    except Exception:
        analysed_until, cands = 0, []
    skip_last = 2 if is_alive else 0
    for i, (f, s, e) in enumerate(segs):
        if skip_last and i >= len(segs) - skip_last: continue
        p = f'{d}/{f}'
        age = now - os.path.getmtime(p)
        if e > analysed_until + 1:
            if is_alive or age < 6 * 3600: continue      # not analysed yet -> keep
        best = 0; reason = ''
        for c in cands:
            m = c['moment_s']
            if not (s - 60 <= m <= e + 60): continue
            st = ev.get((rec, c['id']))
            if st == 'posted': continue
            if isinstance(st, (int, float)) and st >= KEEP_SCORE and st > best: best, reason = st, f"{c['id']} eval {st:.0f}"
            if st is None and c['score'] >= 5 and age < 7200 and 40 > best: best, reason = 40, f"{c['id']} pending raw {c['score']:.1f}"
        for t in pin.get(rec, []):
            if s - 60 <= t <= e + 60: best, reason = 999, f'pin {t:.0f}s'
        item = (age, p, rec, best, reason)
        if best and age < PROTECT_MAX_H * 3600:
            protected.append(item)
        elif age >= ttl:
            trashable.append(item)


def to_trash(item, why):
    age, p, rec, best, reason = item
    dst = f'{TRASH}/{rec}'
    log(f'trash {rec}/{os.path.basename(p)} ({os.path.getsize(p)/1e9:.2f} GB, {age/3600:.1f} h, {why})')
    if not DRY:
        os.makedirs(dst, exist_ok=True)
        shutil.move(p, f'{dst}/{os.path.basename(p)}')
        os.utime(f'{dst}/{os.path.basename(p)}', None)   # mtime = time put in bin


for it in sorted(trashable, reverse=True):
    to_trash(it, 'analysed, past ttl')


def purge(force_until_h=None, why='grace over'):
    items = sorted(glob.glob(f'{TRASH}/*/seg_*.ts'), key=os.path.getmtime)
    for f in items:
        too_old = now - os.path.getmtime(f) >= grace
        need = force_until_h is not None and free_gb() / burn < force_until_h
        if too_old or need:
            log(f'purge {os.path.relpath(f, TRASH)} ({os.path.getsize(f)/1e9:.2f} GB, {why if too_old else "low disk"})')
            if not DRY: os.remove(f)


purge(force_until_h=RESERVE_H)
if free_gb() / burn < CRIT_H:
    for it in sorted(protected, key=lambda x: x[3]):
        if free_gb() / burn >= CRIT_H: break
        if it[3] >= 999: continue                          # never auto-delete pins
        to_trash(it, f'CRITICAL disk, was protected: {it[4]}'); purge(force_until_h=CRIT_H, why='critical')

for d in glob.glob(f'{TRASH}/*') + glob.glob(f'{RAW}/*'):
    if os.path.isdir(d) and d not in alive and not glob.glob(f'{d}/seg_*.ts') and now - os.path.getmtime(d) > 3600:
        if d.startswith(TRASH) or not os.path.exists(f'{WORK}/{os.path.basename(d)}/clips.json'):
            log(f'rmdir {d}')
            if not DRY: shutil.rmtree(d)

log(f'done: free {free_gb():.1f} GB, protected {len(protected)} seg(s): ' + ', '.join(f"{os.path.basename(os.path.dirname(x[1]))}/{os.path.basename(x[1])}[{x[4]}]" for x in protected[:6]))
