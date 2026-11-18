import json, os, subprocess
S = '"/Users/moritz/Documents/#code/clip-pipeline/clip-forge/skills/clipping-streams"'

def sh(cmd, timeout=600):
    env = dict(os.environ, PATH='/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', '') + ':' + os.path.expanduser('~/.local/bin'))
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
    return r.stdout + r.stderr

def W(rec):
    return os.path.expanduser(f'~/clips/work/{rec}/')

def words(rec, sec, a, b):
    d = json.load(open(W(rec) + f'transcripts/{sec}.json'))
    print(sec, ' '.join(f"{w['w']}@{w['s']:.1f}-{w['e']:.1f}" for w in d['words'] if a <= w['s'] <= b))

def cap(ch, y, t, i, game='gta6', x=None, sc=None):
    """Distinct text + hashtags per platform (anti-spam). x/sc default to variants of t/y."""
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
    return sh(f'uv run --quiet {S}/scripts/validate_clips.py --vod-id {rec} 2>&1 | grep -iE "warn|error" | grep -v "^{{"')

def next_id(rec):
    p = W(rec) + 'clips.json'
    if not os.path.exists(p): return 1
    return max([int(c['id'][4:]) for c in json.load(open(p))['clips']] + [0]) + 1

def facecam(rec, clip, extra=''):
    o = sh(f'uv run --quiet {S}/scripts/detect_facecam.py --vod-id {rec} --clip {clip} {extra} 2>/dev/null', 300)
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
    o = sh(f'uv run --quiet {S}/scripts/render_clip.py --vod-id {rec} --clip {",".join(clips)} --force 2>&1 | grep INFO', 600)
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

def stills(rec, clips, out='/Users/moritz/.hermes/cache/scratch/stills.jpg', ts=(2, 10)):
    code = f"""
import cv2
fr=[]
for c in {list(clips)!r}:
    v=cv2.VideoCapture('/Users/moritz/clips/out/{rec}/'+c+'.mp4')
    for t in {list(ts)!r}:
        v.set(cv2.CAP_PROP_POS_MSEC,t*1000); ok,f=v.read()
        if ok: fr.append(cv2.resize(f,(270,480)))
cv2.imwrite('{out}',cv2.hconcat(fr))
"""
    open('/Users/moritz/.hermes/cache/scratch/_st.py', 'w').write(code)
    sh('cd ~/.hermes/cache/scratch && uv run --quiet --with opencv-python-headless python _st.py', 120)
    return out

def show(pairs, n=600):
    for r, cs in pairs.items():
        secs = json.load(open(W(r) + 'sections.json'))['sections']
        cands = {c['id']: c for c in json.load(open(W(r) + 'candidates.json'))['candidates']}
        for c in cs:
            s = next(x for x in secs if c in x['candidates'])
            txt = open(W(r) + f"transcripts/{s['id']}.txt").read().split('\n', 1)[1]
            print(f"## {r.rsplit('-',2)[0]} {c} sc={cands[c]['score']}")
            print(' | '.join(l.split('] ', 1)[-1][:80] for l in txt.splitlines())[:n])

def pairs_from_summary():
    p = {}
    for l in open(os.path.expanduser('~/.hermes/cache/scratch/live/batch_summary.txt')):
        if ':' in l and not l.strip().endswith('-') and 'kein' not in l:
            r, cs = l.split(':', 1); p[r.strip()] = [c for c in cs.strip().split(',') if c]
    return p
