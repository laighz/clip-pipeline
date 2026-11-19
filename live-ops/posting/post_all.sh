#!/bin/bash
# Usage: post_all.sh <vod> <clip>
# Posts staggered: platforms in random order, 10-60 min random gap between platforms.
# Global browser lock so overlapping jobs never drive the browser at the same time.
V=$1; C=$2; cd ~/.hermes/cache/scratch/pw
LOCK=/tmp/livespiked_browser.lock
lock(){ while ! mkdir $LOCK 2>/dev/null; do sleep 20; done; }
unlock(){ rmdir $LOCK 2>/dev/null; }
run(){ uv run --quiet --with playwright python "$@" 2>&1 | tail -3 | tr '\n' ' '; echo; }
post(){ case $1 in
  YT) Y=$(run ytu.py $V $C); echo "$Y" | grep -q "shorts/" || Y="retry: $(run ytu.py $V $C)"; echo "$Y" | grep -q "shorts/" && date +%s >> ~/.hermes/cache/scratch/live/yt_posts.log; echo "$Y" | grep -qi "limit" && echo $(( $(date +%s) + 86400 )) > ~/.hermes/cache/scratch/live/YT_BLOCKED; echo "YT $(date +%H:%M): $Y";;
  TT) echo "TT $(date +%H:%M): $(run ttu.py $V $C)";;
  IG) R=$(run igu.py $V $C); echo "$R" | grep -q "shared" && date +%s >> ~/.hermes/cache/scratch/live/ig_posts.log; echo "IG $(date +%H:%M): $R";;
  X)  echo "X  $(date +%H:%M): $(run x2.py $V $C; run x3.py)";;
  SC) echo "SC $(date +%H:%M): $(run scu.py $V $C)";;
esac; }
# Voice-over (Lars) einmal vor dem ersten Upload
[ -f ~/clips/out/$V/$C.novo.mp4 ] || python3 ~/.hermes/cache/scratch/live/vo_mix.py $V $C >> ~/.hermes/cache/scratch/live/vo.log 2>&1 || echo "VO: fehlgeschlagen, poste ohne"
FIRST=1
for P in $(printf "YT\nTT\nX\nSC\nIG\n" | sort -R | grep -vxE "${SKIP:-NONE}"); do
  if [ "$P" = "YT" ]; then
    B=~/.hermes/cache/scratch/live/YT_BLOCKED
    if [ -f $B ] && [ $(date +%s) -lt $(cat $B) ]; then echo "YT: skipped (Upload-Limit-Pause)"; continue; fi
    NOW=$(date +%s); N24=$(awk -v n=$NOW '$1>n-86400' ~/.hermes/cache/scratch/live/yt_posts.log 2>/dev/null | wc -l | tr -d ' ')
    [ "$N24" -ge 10 ] && { echo "YT: skipped (max 10 Shorts / 24 h)"; continue; }
  fi
  if [ "$P" = "IG" ]; then
    [ -f ~/.hermes/cache/scratch/live/IG_BLOCKED ] && { echo "IG: skipped (blocked)"; continue; }
    NOW=$(date +%s); L=~/.hermes/cache/scratch/live/ig_posts.log
    N24=$(awk -v n=$NOW '$1>n-86400' $L 2>/dev/null | wc -l | tr -d ' ')
    LAST=$(tail -1 $L 2>/dev/null || echo 0)
    F=~/.hermes/cache/scratch/live/IG_FORCE; if [ -f $F ] && grep -qx "$V $C" $F; then sed -i '' "/^$V $C\$/d" $F; echo "IG: Limit vom User freigegeben";
    else
    [ "$N24" -ge 3 ] && { echo "IG: skipped (max 3 Reels / 24 h)"; continue; }
    [ $(( NOW - LAST )) -lt 14400 ] && { echo "IG: skipped (min 4 h Abstand)"; continue; }
    fi
  fi
  [ $FIRST = 1 ] || sleep $(( ${GAPMIN:-600} + RANDOM % ${GAPSPAN:-1800} ))
  FIRST=0
  lock; post $P; unlock
done
