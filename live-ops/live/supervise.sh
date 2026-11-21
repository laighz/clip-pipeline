#!/bin/bash
# Usage: supervise.sh <channel>
# Keeps channel recorded across stream restarts. Exits (notify) only when a spike is cuttable (CUT) or channel offline > 60 min (GONE).
CH=$1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
S="$ROOT/clip-forge/skills/clipping-streams"
D=~/.hermes/cache/scratch/live
OFF=0
while true; do
  R=$(ls -t ~/clips/raw/live | grep "^$CH-" | head -1)
  ST=$(uv run --quiet "$S/scripts/record_status.py" --rec-id "$R" 2>/dev/null | python3 -c 'import json,sys;d=json.load(sys.stdin);r=d.get("recordings") or [d];print(r[0].get("status","?"))' 2>/dev/null)
  if [ "$ST" != "recording" ]; then
    OUT=$(uv run --quiet "$S/scripts/record_start.py" --channel $CH 2>/dev/null)
    if echo "$OUT" | grep -qE '"status": "(recording|läuft schon)"'; then OFF=0; echo "$(date +%T) restart $CH" >> $D/${CH}_sup.log; sleep 5; continue; fi
    OFF=$((OFF+20)); [ $OFF -ge 3600 ] && { echo "GONE $CH"; exit 0; }
    sleep 20; continue
  fi
  if [ -n "$NOCUT" ]; then sleep 60; continue; fi
  $D/live_loop.sh "$R" > $D/${CH}_loop.out 2>&1
  if grep -q " CUT" $D/${CH}_loop.out; then echo "CUT $R"; cat $D/${R}_pending.json; exit 0; fi
done
