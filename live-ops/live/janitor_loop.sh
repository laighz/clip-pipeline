#!/bin/bash
# Every 10 min: refresh the best-of-day protection list, then run the janitor (both from this repo folder).
HERE="$(cd "$(dirname "$0")" && pwd)"
while true; do
  python3 "$HERE/daily_best.py" --save >/dev/null 2>&1
  python3 "$HERE/janitor.py" >/dev/null 2>&1
  sleep 600
done
