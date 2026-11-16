# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Stand laufender und beendeter Live-Aufnahmen; wartet auf Wunsch auf den nächsten Chat-Spike.

  record_status.py                               # alle Aufnahmen (neueste zuerst)
  record_status.py --rec-id <id>                 # eine Aufnahme
  record_status.py --rec-id <id> --wait-spike --after 2 --timeout 540
      blockiert, bis ein Spike mit Nummer > --after im Log steht (oder Timeout / Aufnahme-Ende)

Liest nur. Exit: 0 ok (auch bei Timeout), 2 Aufnahme unbekannt.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import live_common
from live_common import _lib


def load_states() -> list[dict[str, Any]]:
    out = []
    sdir = _lib.state_dir()
    for f in sdir.glob("live_*.json") if sdir.is_dir() else []:
        if f.name.endswith(".spec.json"):
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: s.get("started_at", ""), reverse=True)


def describe(st: dict[str, Any]) -> dict[str, Any]:
    alive = live_common.pid_alive(st.get("pid"))
    status = st.get("status")
    if status in ("starting", "recording") and not alive:
        status = "abgestürzt"
    segs = live_common.read_segments(Path(st["rec_dir"]))
    spikes = live_common.read_spikes(st["rec_id"])
    t0 = st.get("t0")
    return {
        "rec_id": st["rec_id"], "channel": st.get("channel"), "status": status, "alive": alive,
        "reason": st.get("reason"), "runtime_s": round(time.time() - t0, 0) if t0 and alive else st.get("duration_s"),
        "gb": round(st.get("bytes", 0) / 1024**3, 2), "segments_done": len(segs),
        "recorded_until_s": segs[-1]["end"] if segs else 0, "free_gb": st.get("free_gb"),
        "last_data_at": st.get("last_data_at"), "ads": len(st.get("ads", [])),
        "spikes": len(spikes), "last_spikes": spikes[-5:], "rec_dir": st["rec_dir"], "log": st.get("log"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stand der Live-Aufnahmen, optional auf den nächsten Spike warten.",
                                     epilog="Exit: 0 ok (auch bei Timeout), 2 Aufnahme unbekannt.")
    parser.add_argument("--rec-id", help="Aufnahme-ID (Standard: alle)")
    parser.add_argument("--wait-spike", action="store_true", help="auf neuen Spike warten (braucht --rec-id)")
    parser.add_argument("--after", type=int, default=0, help="nur Spikes mit Nummer > after melden")
    parser.add_argument("--timeout", type=float, default=540, help="max. Wartezeit in s (Standard 540)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("record_status", args.verbose)

    states = load_states()
    if args.rec_id:
        states = [s for s in states if s.get("rec_id") == args.rec_id]
        if not states:
            _lib.fail(f"Aufnahme {args.rec_id} unbekannt", _lib.EXIT_INVALID)
    if not args.wait_spike:
        _lib.emit({"recordings": [describe(s) for s in states[:10]]})
    if not args.rec_id:
        active = [s for s in states if describe(s)["alive"]]
        if len(active) != 1:
            _lib.fail("--wait-spike braucht --rec-id (es laufen 0 oder mehrere Aufnahmen)", _lib.EXIT_INVALID)
        args.rec_id = active[0]["rec_id"]
    deadline = time.time() + args.timeout
    while True:
        new = [s for s in live_common.read_spikes(args.rec_id) if s["n"] > args.after]
        st = json.loads(live_common.state_file(args.rec_id).read_text(encoding="utf-8"))
        d = describe(st)
        if new:
            _lib.emit({"rec_id": args.rec_id, "new_spikes": new, "status": d["status"],
                       "recorded_until_s": d["recorded_until_s"], "timed_out": False,
                       "hint": "Spike-Moment liegt erst im Segment, wenn recorded_until_s > moment + 60 ist"})
        if not d["alive"]:
            _lib.emit({"rec_id": args.rec_id, "new_spikes": [], "status": d["status"], "timed_out": False,
                       "ended": True, "recorded_until_s": d["recorded_until_s"]})
        if time.time() > deadline:
            _lib.emit({"rec_id": args.rec_id, "new_spikes": [], "status": d["status"], "timed_out": True,
                       "recorded_until_s": d["recorded_until_s"]})
        time.sleep(2)


if __name__ == "__main__":
    main()
