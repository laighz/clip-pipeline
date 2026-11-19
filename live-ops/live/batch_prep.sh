#!/bin/bash
# Batch prep for all live recordings: prepare, detect, fetch+transcribe top new candidates (max 2 per stream).
# Output: live/batch_summary.txt (per rec: candidate ids + transcript paths)
S="/Users/moritz/Documents/#code/clip-pipeline/clip-forge/skills/clipping-streams"
D=~/.hermes/cache/scratch/live
OUT=$D/batch_summary.txt; : > $OUT
touch $D/batch.lock; trap "rm -f $D/batch.lock" EXIT
RECS="$@"; [ -z "$RECS" ] && RECS=$(for CH in trymacs ohnepixel xrohat montanablack88 darkviperau zarbex kaicenat; do ls -t ~/clips/raw/live | grep "^$CH-" | head -3; done)
for R in $RECS; do
  CH=${R%-*-*}
  LANG=de; case $CH in ohnepixel|darkviperau|kaicenat|caseoh_|zackrawrr|lacy) LANG=en;; esac
  uv run --quiet "$S/scripts/live_prepare.py" --rec-id $R >/dev/null 2>&1 || { echo "$R: kein Segment" >> $OUT; continue; }
  uv run --quiet "$S/scripts/detect_highlights.py" --vod-id $R --top 40 >/dev/null 2>&1
  C=$(python3 - "$R" <<'PY'
import json,os,sys
R=sys.argv[1]
w=os.path.expanduser(f'~/clips/work/{R}/')
cands=json.load(open(w+'candidates.json'))['candidates']
dur=json.load(open(w+'meta.json')).get('duration_s',0)
ev=os.path.expanduser('~/.hermes/cache/scratch/live/evals.jsonl')
seen={(e.get('rec'),e.get('cand')) for e in map(json.loads,open(ev))} if os.path.exists(ev) else set()
# also skip cands already in sections (processed earlier)
secs=set()
sp=w+'sections.json'
if os.path.exists(sp):
    for s in json.load(open(sp))['sections']: secs.update(s.get('candidates',[]))
segs=json.load(open(w+'meta.json')).get('segments',[])
have=lambda t: any(g['start']-30<=t<=g['end']+30 and os.path.exists(g['file']) for g in segs)
ok=[c for c in sorted(cands,key=lambda c:-c['score']) if have(c['moment_s']) and (R,c['id']) not in seen and c['id'] not in secs and c['moment_s']+60<=dur and c['score']>=3]
print(','.join(c['id'] for c in ok[:2]))
PY
)
  [ -z "$C" ] && { echo "$R: -" >> $OUT; continue; }
  uv run --quiet "$S/scripts/fetch_sections.py" --vod-id $R --candidates $C >/dev/null 2>&1
  uv run --quiet "$S/scripts/transcribe.py" --vod-id $R --language $LANG >/dev/null 2>&1
  echo "$R: $C" >> $OUT
done
echo DONE >> $OUT
