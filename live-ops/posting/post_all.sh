#!/bin/bash
# Usage: post_all.sh <vod> <clip>
# Posts staggered: platforms in random order, 10-60 min random gap between platforms.
# Global browser lock so overlapping jobs never drive the browser at the same time.
V=$1
C=$2
cd ~/.hermes/cache/scratch/pw || exit 1
LOCK=/tmp/livespiked_browser.lock
LIVE=~/.hermes/cache/scratch/live

lock() {
  while ! mkdir "$LOCK" 2>/dev/null; do
    sleep 20
  done
}
unlock() {
  rmdir "$LOCK" 2>/dev/null
}
run() {
  uv run --quiet --with playwright python "$@" 2>&1 | tail -3 | tr '\n' ' '
  echo
}

if [ -z "$SKIP" ]; then
  SKIP=NONE
fi
if [ -z "$GAPMIN" ]; then
  GAPMIN=600
fi
if [ -z "$GAPSPAN" ]; then
  GAPSPAN=1800
fi

if [ ! -f ~/clips/out/$V/$C.novo.mp4 ]; then
  python3 "$LIVE/vo_mix.py" "$V" "$C" >> "$LIVE/vo.log" 2>&1 || echo "VO: fehlgeschlagen, poste ohne"
fi

FIRST=1
order=$(printf "YT\nTT\nX\nSC\nIG\n" | sort -R | grep -vxE "$SKIP")
for P in $order; do
  if [ "$P" = "YT" ]; then
    B=$LIVE/YT_BLOCKED
    if [ -f "$B" ]; then
      until_ts=$(cat "$B")
      now=$(date +%s)
      if [ "$now" -lt "$until_ts" ]; then
        echo "YT: skipped (Upload-Limit-Pause)"
        continue
      fi
    fi
    now=$(date +%s)
    n24=$(awk -v n="$now" '$1 > n - 86400' "$LIVE/yt_posts.log" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$n24" -ge 10 ]; then
      echo "YT: skipped (max 10 Shorts / 24 h)"
      continue
    fi
  fi
  if [ "$P" = "IG" ]; then
    if [ -f "$LIVE/IG_BLOCKED" ]; then
      echo "IG: skipped (blocked)"
      continue
    fi
    now=$(date +%s)
    n24=$(awk -v n="$now" '$1 > n - 86400' "$LIVE/ig_posts.log" 2>/dev/null | wc -l | tr -d ' ')
    last=$(tail -1 "$LIVE/ig_posts.log" 2>/dev/null || echo 0)
    force=$LIVE/IG_FORCE
    if [ -f "$force" ] && grep -qx "$V $C" "$force"; then
      grep -vx "$V $C" "$force" > "$force.tmp" && mv "$force.tmp" "$force"
      echo "IG: Limit vom User freigegeben"
    else
      if [ "$n24" -ge 3 ]; then
        echo "IG: skipped (max 3 Reels / 24 h)"
        continue
      fi
      if [ $((now - last)) -lt 14400 ]; then
        echo "IG: skipped (min 4 h Abstand)"
        continue
      fi
    fi
  fi
  if [ "$FIRST" != 1 ]; then
    sleep $((GAPMIN + RANDOM % GAPSPAN))
  fi
  FIRST=0
  lock
  case $P in
    YT)
      Y=$(run ytu.py "$V" "$C")
      echo "$Y" | grep -q "shorts/" || Y="retry: $(run ytu.py "$V" "$C")"
      if echo "$Y" | grep -q "shorts/"; then
        date +%s >> "$LIVE/yt_posts.log"
      fi
      if echo "$Y" | grep -qi "limit"; then
        echo $(($(date +%s) + 86400)) > "$LIVE/YT_BLOCKED"
      fi
      echo "YT $(date +%H:%M): $Y"
      ;;
    TT)
      echo "TT $(date +%H:%M): $(run ttu.py "$V" "$C")"
      ;;
    IG)
      R=$(run igu.py "$V" "$C")
      if echo "$R" | grep -q "shared"; then
        date +%s >> "$LIVE/ig_posts.log"
      fi
      echo "IG $(date +%H:%M): $R"
      ;;
    X)
      xout=$(run x2.py "$V" "$C")
      x3=$(run x3.py)
      echo "X  $(date +%H:%M): $xout $x3"
      ;;
    SC)
      echo "SC $(date +%H:%M): $(run scu.py "$V" "$C")"
      ;;
  esac
  unlock
done
