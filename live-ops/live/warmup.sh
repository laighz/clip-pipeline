#!/bin/bash
# Einmal am Tag um 13:00 Europe/Berlin den Warmup-Stand schreiben.
# Kein Feed, keine Likes. Die Limits liest post_all.sh aus warmup.json.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/live-ops/live/warmup.py"
while true; do
  secs=$(python3 - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo("Europe/Berlin"))
target = now.replace(hour=13, minute=0, second=0, microsecond=0)
if now >= target:
    target += timedelta(days=1)
print(max(1, int((target - now).total_seconds())))
PY
)
  sleep "$secs"
  python3 "$PY" --write
  sleep 90
done
