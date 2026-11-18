#!/bin/bash
# Usage: live_loop.sh <rec_id>
# Collects spikes silently; exits (=> notify) when a spike is cuttable (recorded_until > moment+60) or recording ended.
R=$1
S="/Users/moritz/Documents/#code/clip-pipeline/clip-forge/skills/clipping-streams"
D=~/.hermes/cache/scratch/live
F=$D/${R}_spikes.jsonl; DONE=$D/${R}_done.txt; ST=$D/${R}_status.json
touch "$F" "$DONE"
while true; do
  N=$(python3 -c "import json;L=[json.loads(l)['n'] for l in open('$F') if l.strip()];print(max(L) if L else 0)")
  OUT=$(uv run --quiet "$S/scripts/record_status.py" --rec-id $R --wait-spike --after $N --timeout 540 2>/dev/null)
  echo "$OUT" > "$ST"
  RES=$(python3 - "$F" "$DONE" <<'PY'
import json,sys
F,DONE=sys.argv[1],sys.argv[2]
d=json.load(open(sys.argv[0] if False else '/dev/stdin')) if False else None
PY
)
  python3 - "$F" "$DONE" "$ST" > $D/${R}_pending.json <<'PY'
import json,sys
F,DONE,ST=sys.argv[1:4]
d=json.load(open(ST))
with open(F,'a') as f:
    for s in d.get('new_spikes',[]): f.write(json.dumps({"n":s["n"],"moment":s["moment"],"z":s["z"],"uniq":s.get("uniq")})+"\n")
sp=[json.loads(l) for l in open(F) if l.strip()]
done={l.strip() for l in open(DONE) if l.strip()}
ru=d.get('recorded_until_s',0) or 0
pend=[s for s in sp if str(s['n']) not in done and s['moment']+60<=ru]
print(json.dumps({"status":d.get("status"),"ended":d.get("ended",False),"recorded_until_s":ru,"pending":pend}))
PY
  P=$(python3 -c "import json;d=json.load(open('$D/${R}_pending.json'));print('END' if (d['status']!='recording' or d['ended']) else ('CUT' if d['pending'] else 'WAIT'))")
  if [ "$P" != "WAIT" ]; then echo "$R $P"; cat $D/${R}_pending.json; exit 0; fi
done
