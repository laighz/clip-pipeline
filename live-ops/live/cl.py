import json, os, re, subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "clip-forge" / "skills" / "clipping-streams" / "scripts"


def _ensure_path():
    extras = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    for item in reversed(extras):
        if item not in parts:
            parts.insert(0, item)
    os.environ["PATH"] = os.pathsep.join(parts)


_ensure_path()


def run(args, timeout=600):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def uv_script(name, *args, timeout=600):
    return run(["uv", "run", "--quiet", str(SCRIPTS / name), *args], timeout=timeout)

def W(rec):
    return os.path.expanduser(f'~/clips/work/{rec}/')

def words(rec, sec, a, b):
    d = json.load(open(W(rec) + f'transcripts/{sec}.json'))
    print(sec, ' '.join(f"{w['w']}@{w['s']:.1f}-{w['e']:.1f}" for w in d['words'] if a <= w['s'] <= b))

def cap(ch, y, t, i, game='gta6', x=None, sc=None):
    """Distinct text + hashtags per platform (anti-spam). x/sc default to variants of t/y."""
    import re
    y, t, i = (re.sub(r'\s*Clip: @\w+ auf Twitch\.?', '', v).strip() for v in (y, t, i))
    G = game or ''
    return {"youtube": f"{y} Clip: @{ch} auf Twitch #Shorts #{G} #{ch}",
            "tiktok": f"{t} Clip: @{ch} auf Twitch #{G} #{ch} #gaming",
            "instagram": f"{i} Clip: @{ch} auf Twitch #{G} #{ch} #reels",
            "x": f"{x or y} (via @{ch} auf Twitch) #{G.upper()}",
            "snapchat": f"{sc or i} #{G} #{ch}"}

def add_clips(rec, new):
    p = W(rec) + 'clips.json'
    d = json.load(open(p)) if os.path.exists(p) else {"vod_id": rec, "clips": []}
    ids = {c['id'] for c in new}
    d['clips'] = [c for c in d['clips'] if c['id'] not in ids] + new
    json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)
    _code, out, err = uv_script("validate_clips.py", "--vod-id", rec)
    text = out + err
    return "\n".join(
        line for line in text.splitlines()
        if re.search(r"warn|error", line, re.I) and not line.startswith("{")
    )

def next_id(rec):
    p = W(rec) + 'clips.json'
    if not os.path.exists(p): return 1
    return max([int(c['id'][4:]) for c in json.load(open(p))['clips']] + [0]) + 1

def facecam(rec, clip, extra=''):
    args = ["--vod-id", rec, "--clip", clip]
    if extra:
        args.extend(extra.split())
    _code, out, _err = uv_script("detect_facecam.py", *args, timeout=300)
    o = out
    try:
        j = json.loads(o.strip().splitlines()[-1])
        return j['status'], j.get('faces'), j.get('facecam'), j.get('snapshot') or j.get('frame')
    except Exception:
        return 'err', o[-300:], None, None

def set_field(rec, clip, **kw):
    p = W(rec) + 'clips.json'; d = json.load(open(p))
    for c in d['clips']:
        if c['id'] == clip: c.update(kw)
    json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)

def render(rec, clips):
    _code, out, err = uv_script("render_clip.py", "--vod-id", rec, "--clip", ",".join(clips), "--force", timeout=600)
    o = "\n".join(line for line in (out + err).splitlines() if "INFO" in line)
    d = json.load(open(W(rec) + 'clips.json'))
    for c in d['clips']:
        if c['id'] not in clips: continue
        tp = os.path.expanduser(f"~/clips/out/{rec}/{c['id']}.txt")
        if not os.path.exists(tp): continue
        t = open(tp).read()
        caps = c.get('captions') or {}
        for k, blk in (('x', 'X'), ('snapchat', 'SNAPCHAT')):
            if caps.get(k) and f'--- {blk} ---' not in t:
                t += f"\n--- {blk} ---\n{caps[k]}\n"
        open(tp, 'w').write(t)
    return o

_STILLS = """
import sys
from pathlib import Path
import cv2
rec, clips, ts, out = sys.argv[1], sys.argv[2].split(","), sys.argv[3].split(","), sys.argv[4]
base = Path.home() / "clips" / "out" / rec
frames = []
for clip in clips:
    video = cv2.VideoCapture(str(base / f"{clip}.mp4"))
    for stamp in ts:
        video.set(cv2.CAP_PROP_POS_MSEC, float(stamp) * 1000)
        ok, frame = video.read()
        if ok:
            frames.append(cv2.resize(frame, (270, 480)))
    video.release()
if frames:
    cv2.imwrite(out, cv2.hconcat(frames))
"""


def stills(rec, clips, out=None, ts=(2, 10)):
    scratch = Path.home() / ".hermes" / "cache" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    target = Path(out) if out else scratch / "stills.jpg"
    script = scratch / "_st.py"
    script.write_text(_STILLS)
    run(["uv", "run", "--quiet", "--with", "opencv-python-headless", "python", str(script),
         rec, ",".join(clips), ",".join(str(stamp) for stamp in ts), str(target)], timeout=120)
    return str(target)

def show(pairs, n=600):
    for r, cs in pairs.items():
        secs = json.load(open(W(r) + 'sections.json'))['sections']
        cands = {c['id']: c for c in json.load(open(W(r) + 'candidates.json'))['candidates']}
        for c in cs:
            s = next((x for x in secs if c in x['candidates']), None)
            if not s or not os.path.exists(W(r) + f"transcripts/{s['id']}.txt"):
                print(f"## {r.rsplit('-',2)[0]} {c}: kein Transkript (Segment fehlt)"); continue
            txt = open(W(r) + f"transcripts/{s['id']}.txt").read().split('\n', 1)[1]
            print(f"## {r.rsplit('-',2)[0]} {c} sc={cands[c]['score']}")
            print(' | '.join(l.split('] ', 1)[-1][:80] for l in txt.splitlines())[:n])

def pairs_from_summary():
    p = {}
    for l in open(os.path.expanduser('~/.hermes/cache/scratch/live/batch_summary.txt')):
        if ':' in l and not l.strip().endswith('-') and 'kein' not in l:
            r, cs = l.split(':', 1); p[r.strip()] = [c for c in cs.strip().split(',') if c]
    return p


def unrated():
    """Candidates of the current batch_summary that have no evals.jsonl entry yet: [(rec, cand)]."""
    rated = set()
    ev = os.path.expanduser('~/.hermes/cache/scratch/live/evals.jsonl')
    if os.path.exists(ev):
        for l in open(ev):
            try: e = json.loads(l); rated.add((e.get('rec'), e.get('cand')))
            except Exception: pass
    out = []
    for l in open(os.path.expanduser('~/.hermes/cache/scratch/live/batch_summary.txt')):
        if ':' not in l: continue
        r, cs = l.split(':', 1); r = r.strip(); cs = cs.strip()
        if cs in ('-', 'kein Segment', ''): continue
        out += [(r, c) for c in cs.split(',') if c and (r, c) not in rated]
    return out


def rate_rest(s=1, p=1, r=1, note='gelesen, ohne Moment'):
    """Rate every still unrated candidate with a low default. ONLY after show() was read: user rule 20.11.
    'Alle Kandidaten einer Runde bewerten, erst dann darf der Janitor löschen.'"""
    import subprocess
    n = 0
    for rec, c in unrated():
        subprocess.run(['python3', os.path.expanduser('~/.hermes/cache/scratch/live/evaluate.py'), rec, '-', '--cand', c,
                        '--standalone', str(s), '--payoff', str(p), '--relevance', str(r), '--reject', note],
                       capture_output=True, text=True)
        n += 1
    return n
