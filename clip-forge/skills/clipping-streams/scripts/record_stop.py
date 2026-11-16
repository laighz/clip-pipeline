# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Beendet Live-Aufnahmen sauber (Stopp-Datei), notfalls hart (taskkill /T /F).

Idempotent: "läuft schon nicht mehr" gilt als Erfolg. Segmente bleiben erhalten (Rohdateien).
Aufruf: record_stop.py --rec-id <id> | --channel <login> | --all [--timeout 45] [--dry-run]
Exit: 0 ok, 2 nichts angegeben / unbekannt.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

import live_common
from live_common import _lib


def main() -> None:
    parser = argparse.ArgumentParser(description="Live-Aufnahme(n) beenden (idempotent).",
                                     epilog="Exit: 0 ok, 2 nichts angegeben oder unbekannt.")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--rec-id")
    grp.add_argument("--channel")
    grp.add_argument("--all", action="store_true")
    parser.add_argument("--timeout", type=float, default=45, help="so lange auf sauberes Ende warten (s)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("record_stop", args.verbose)

    states = []
    sdir = _lib.state_dir()
    for f in sdir.glob("live_*.json") if sdir.is_dir() else []:
        if f.name.endswith(".spec.json"):
            continue
        try:
            states.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if args.rec_id:
        states = [s for s in states if s.get("rec_id") == args.rec_id]
        if not states:
            _lib.fail(f"Aufnahme {args.rec_id} unbekannt", _lib.EXIT_INVALID)
    elif args.channel:
        states = [s for s in states if s.get("channel") == args.channel.lower()]
    results = []
    for st in states:
        rec_id, pid = st["rec_id"], st.get("pid")
        if not live_common.pid_alive(pid):
            results.append({"rec_id": rec_id, "result": "läuft schon nicht mehr", "status": st.get("status")})
            continue
        if args.dry_run:
            results.append({"rec_id": rec_id, "result": "(dry-run) würde stoppen", "pid": pid})
            continue
        live_common.stop_file(rec_id).parent.mkdir(parents=True, exist_ok=True)
        live_common.stop_file(rec_id).write_text("stop", encoding="utf-8")
        deadline = time.time() + args.timeout
        while time.time() < deadline and live_common.pid_alive(pid):
            time.sleep(1)
        how = "sauber"
        if live_common.pid_alive(pid):
            how = "hart (taskkill)"
            log.warning("%s reagiert nicht, beende hart", rec_id)
            for p in [pid, *st.get("child_pids", [])]:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
                else:
                    try:
                        os.kill(int(p), 9)
                    except OSError:
                        pass
        final = json.loads(live_common.state_file(rec_id).read_text(encoding="utf-8"))
        results.append({"rec_id": rec_id, "result": f"gestoppt ({how})", "status": final.get("status"),
                        "segments": final.get("segments"), "duration_s": final.get("duration_s")})
    _lib.emit({"stopped": results, "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
