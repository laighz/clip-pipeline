#!/bin/bash
# Usage: post_all.sh <vod> <clip>
# Posts staggered: platforms in random order, 10-60 min random gap between platforms.
# Global browser lock so overlapping jobs never drive the browser at the same time.
V=${1:-}
C=${2:-}
[[ "$V" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$ && "$C" =~ ^clip[0-9]+$ ]] || exit 64
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WARM="$ROOT/live-ops/live/warmup.py"
POST="$ROOT/live-ops/posting"
cd ~/.hermes/cache/scratch/pw || exit 1
LOCK=${POST_BROWSER_LOCK:-/tmp/livespiked_browser.lock}
LIVE=~/.hermes/cache/scratch/live
locked=0

warm_ok() {
  if [ ! -f "$WARM" ]; then
    return 0
  fi
  python3 "$WARM" --allow "$1" --log "$2" --live "$LIVE"
}

lock() {
  local waits=0
  while ! mkdir "$LOCK" 2>/dev/null; do
    (( waits++ < 60 )) || return 1
    sleep 5
  done
  locked=1
}
unlock() {
  if [ "$locked" = 1 ]; then
    rmdir "$LOCK" 2>/dev/null
    locked=0
  fi
}
trap unlock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
status() { python3 "$POST/publish_status.py" "$1" "$LIVE" "$V" "$C" "${@:2}"; }

if [ -z "$SKIP" ]; then
  SKIP=NONE
fi
if [ -z "$GAPMIN" ]; then
  GAPMIN=600
fi
if [ -z "$GAPSPAN" ]; then
  GAPSPAN=1800
fi

FIRST=1
order=$(printf "YT\nTT\nX\nSC\nIG\n" | sort -R | grep -vxE "$SKIP")
[ -n "$order" ] || exit 0
lock || exit 1
if [ ! -f "$HOME/clips/out/$V/$C.mp4" ]; then
  echo 'Missing rendered MP4; no upload attempted'
  exit 2
fi
first_platform=${order%%$'\n'*}
if [ ! -e "$HOME/clips/out/$V/$C.novo.mp4" ] && [ ! -e "$HOME/clips/out/$V/$C.vo-ready" ]; then
  status has-submission "$first_platform"
  prior=$?
  if [ "$prior" = 1 ]; then
    /bin/zsh -c 'CLIPS_HOME="$HOME/clips" python3 "$@"' _ "$ROOT/live-ops/live/vo_mix.py" "$V" "$C" >> "$LIVE/vo.log" 2>&1 || {
      echo 'Voice-over failed; no upload attempted (see vo.log)'; exit 2;
    }
  elif [ "$prior" != 0 ]; then
    exit 2
  fi
fi
prepared_revision=$(status vo-ready "$first_platform") || prepared_revision=''
for P in $order; do status target "$P" || exit 2; done
unlock
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
      if ! warm_ok IG "$LIVE/ig_posts.log"; then
        echo "IG: skipped (warmup)"
        continue
      fi
    fi
  fi
  if [ "$P" = "TT" ]; then
    if [ -f "$LIVE/TT_BLOCKED" ]; then
      echo "TT: skipped (blocked)"
      continue
    fi
    if ! warm_ok TT "$LIVE/tt_posts.log"; then
      echo "TT: skipped (warmup)"
      continue
    fi
  fi
  if [ "$FIRST" != 1 ]; then
    sleep $((GAPMIN + RANDOM % GAPSPAN))
  fi
  FIRST=0
  lock || { echo "$P: browser lock unavailable"; exit 1; }
  state=$(status status "$P") || exit 1
  opts=()
  case "$state" in
    confirmed) echo "$P: already verified"; unlock; continue ;;
    pending) opts=(--verify-only) ;;
    new)
      if [ -z "$prepared_revision" ] || [ "$(status vo-ready "$P")" != "$prepared_revision" ]; then
        echo "$P: voice-over and subtitles not verified for this MP4; no upload attempted"
        unlock; continue
      fi
      opts=(--expected-revision "$prepared_revision") ;;
    *) exit 1 ;;
  esac
  echo "$P $(date +%H:%M): $state"
  uv run --quiet --with playwright python "$POST/publish_one.py" "$P" "$V" "$C" "${opts[@]}"
  rc=$?
  if [ "$rc" = 0 ] || [ "$rc" = 10 ]; then
    if [ "$(status status "$P")" != confirmed ]; then
      echo "$P: verified result has no durable proof for the current render"
    else
      case "$P" in
        TT) date +%s >> "$LIVE/tt_posts.log" ;;
        YT) date +%s >> "$LIVE/yt_posts.log" ;;
        IG) date +%s >> "$LIVE/ig_posts.log" ;;
      esac
    fi
  else
    if [ "$P" = YT ] && [ "$rc" = 11 ]; then
      echo $(($(date +%s) + 86400)) > "$LIVE/YT_BLOCKED"
    fi
    echo "$P: not verified (exit $rc); no automatic re-upload"
  fi
  unlock
done

lock || exit 1
platforms=()
while IFS= read -r P; do platforms+=("$P"); done <<< "$order"
if ! status complete "${platforms[@]}"; then
  echo "Not all selected or previously requested platforms were verified; cleanup not armed."
  exit 2
fi
