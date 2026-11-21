#!/bin/bash
# Alle zwei Stunden ein GTA-VI-Clip. Höchstens ein Post pro Durchlauf.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
while true; do
  python3 "$ROOT/live-ops/live/game_clip_cycle.py" || true
  sleep 7200
done
