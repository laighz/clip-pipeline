# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "ruamel.yaml>=0.18", "numpy>=1.26"]
# ///
"""Startet eine Live-Aufnahme eines Twitch-Kanals als eigenständigen Prozess.

Der Worker (live_worker.py) läuft losgelöst von Claude Code weiter (DETACHED_PROCESS,
eigene Prozessgruppe, Ausbruch aus dem Job-Objekt der Session). Dieses Skript endet
sofort mit JSON. Danach: record_status.py (Stand, --wait-spike), record_stop.py.

Aufruf: record_start.py --channel <login> [--quality best] [--no-chat] [--dry-run]
Idempotent: Läuft für den Kanal schon eine Aufnahme, wird deren ID zurückgegeben.
Exit: 0 ok/läuft schon, 1 Startfehler, 2 Eingabe ungültig, 3 Kanal offline, 4 Programm fehlt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import live_common
from live_common import _lib

EXIT_OFFLINE = 3
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def running_for(channel: str) -> dict | None:
    for f in sorted(_lib.state_dir().glob("live_*.json")) if _lib.state_dir().is_dir() else []:
        if f.name.endswith(".spec.json"):
            continue
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if st.get("channel") == channel and st.get("status") in ("starting", "recording") \
                and live_common.pid_alive(st.get("pid")):
            return st
    return None


def is_online(streamlink: str, url: str) -> tuple[bool, str]:
    proc = _lib.run([streamlink, "--json", url], timeout=60)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False, (proc.stderr or proc.stdout or "")[-300:]
    if data.get("error"):
        return False, str(data["error"])
    return bool(data.get("streams")), "ok"


def spawn(cmd: list[str], cwd: Path) -> tuple[subprocess.Popen, str]:
    if os.name != "nt":
        return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True), "posix-session"
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                creationflags=flags | CREATE_BREAKAWAY_FROM_JOB), "detached+breakaway"
    except OSError:
        # Job-Objekt erlaubt keinen Ausbruch: trotzdem losgelöst starten, aber Warnung
        return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags), "detached"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live-Aufnahme (streamlink, 10-Min-Segmente) + Chat-Spike-Erkennung als eigenständigen Prozess starten.",
        epilog="Exit: 0 ok/läuft schon, 1 Startfehler, 2 Eingabe ungültig, 3 Kanal offline, 4 Programm fehlt.",
    )
    parser.add_argument("--channel", required=True, help="Twitch-Login, z. B. papaplatte")
    parser.add_argument("--streamer", help="Streamer-ID in streamers.yaml (Standard: channel)")
    parser.add_argument("--quality", help="streamlink-Qualität (Standard: live.quality)")
    parser.add_argument("--no-chat", action="store_true", help="ohne Chat-Zähler")
    parser.add_argument("--wait", type=float, default=30, help="so lange auf das erste Byte warten (s)")
    parser.add_argument("--set", action="append", default=[], metavar="key=value", help="live-Einstellung überschreiben")
    parser.add_argument("--source-url", help="nur für Tests: andere streamlink-URL (z. B. hls://http://127.0.0.1/...)")
    parser.add_argument("--irc-host", default="irc.chat.twitch.tv", help=argparse.SUPPRESS)
    parser.add_argument("--irc-port", type=int, default=6697, help=argparse.SUPPRESS)
    parser.add_argument("--no-tls", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-realtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, was gestartet würde")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("record_start", args.verbose)

    channel = args.channel.lower().lstrip("#")
    if not re.fullmatch(r"[a-z0-9_]{2,25}", channel):
        _lib.fail(f"ungültiger Twitch-Kanal {args.channel!r} (nur a-z, 0-9, _)", _lib.EXIT_INVALID)
    cfg, warnings = _lib.load_config()
    live = dict(cfg["live"])
    for pair in args.set:
        key, _, value = pair.partition("=")
        if key not in _lib.LIVE_DEFAULTS:
            _lib.fail(f"--set {key}: unbekannt, erlaubt: {sorted(_lib.LIVE_DEFAULTS)}", _lib.EXIT_INVALID)
        try:
            live[key] = value if key in ("record_dir", "quality") else (float(value) if "." in value else int(value))
        except ValueError:
            _lib.fail(f"--set {key}={value!r}: Zahl erwartet", _lib.EXIT_INVALID)
    streamer = (args.streamer or channel).lower()
    settings = _lib.streamer_settings(cfg, streamer)
    streamlink = _lib.require_tool("streamlink")
    ffmpeg = _lib.require_tool("ffmpeg")
    url = args.source_url or f"https://www.twitch.tv/{channel}"
    quality = args.quality or live["quality"]

    existing = running_for(channel)
    if existing:
        _lib.emit({"status": "läuft schon", "rec_id": existing["rec_id"], "pid": existing["pid"],
                   "rec_dir": existing["rec_dir"]})

    root = Path(str(live["record_dir"])).expanduser()
    rec_id = f"{channel}-{time.strftime('%Y%m%d-%H%M%S')}"
    rec_dir = root / rec_id
    if args.dry_run:
        _lib.emit({"dry_run": True, "rec_id": rec_id, "url": url, "quality": quality, "rec_dir": str(rec_dir),
                   "segment_s": live["segment_s"], "max_hours": live["max_hours"], "chat": not args.no_chat,
                   "would_run": f"streamlink --stdout {url} {quality} | ffmpeg -f segment ..."})
    root.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(root).free / 1024**3
    if free_gb < float(live["min_free_gb"]):
        _lib.fail(f"nur {free_gb:.1f} GB frei unter {root}, mindestens {live['min_free_gb']} GB nötig",
                  _lib.EXIT_ERROR)
    if not args.source_url:
        online, why = is_online(streamlink, url)
        if not online:
            _lib.fail(f"{channel} ist gerade nicht live ({why})", EXIT_OFFLINE)
    if not settings["known"]:
        try:
            import manage_streamers  # noqa: PLC0415  (aus clipping-streams/scripts)
            manage_streamers.add_streamer(streamer, channel=channel, auto=True)
            warnings.append(f"Streamer '{streamer}' neu in streamers.yaml eingetragen")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Streamer nicht eingetragen: {exc}")

    spec = {"channel": channel, "streamer": streamer, "url": url, "quality": quality, "rec_dir": str(rec_dir),
            "streamlink": streamlink, "ffmpeg": ffmpeg, "live": live, "no_chat": args.no_chat,
            "settings": {k: settings[k] for k in ("chat_sigma", "reaction_offset_s", "emotes")},
            "irc_host": args.irc_host, "irc_port": args.irc_port, "irc_tls": not args.no_tls,
            "realtime": args.test_realtime}
    spec_path = _lib.state_dir() / f"live_{rec_id}.spec.json"
    _lib.write_json(spec_path, spec)
    worker = Path(__file__).resolve().parent / "live_worker.py"
    proc, mode = spawn([sys.executable, str(worker), "--rec-id", rec_id, "--spec", str(spec_path)], rec_dir.parent)
    if mode != "detached+breakaway" and os.name == "nt":
        warnings.append("Prozess konnte nicht aus dem Job-Objekt der Session ausbrechen: "
                        "beim Schließen von Claude Code endet die Aufnahme evtl. mit")
    log.info("Worker gestartet (PID %s, %s), warte auf erstes Byte ...", proc.pid, mode)
    deadline = time.time() + args.wait
    state: dict = {}
    while time.time() < deadline:
        sf = live_common.state_file(rec_id)
        if sf.is_file():
            try:
                state = json.loads(sf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            if state.get("status") in ("recording", "ended", "stopped", "error"):
                break
        if proc.poll() is not None and not state:
            _lib.fail(f"Worker sofort beendet (Code {proc.returncode}), siehe {live_common.log_file(rec_id)}",
                      _lib.EXIT_ERROR)
        time.sleep(0.5)
    status = state.get("status", "starting")
    if status == "error":
        _lib.fail(f"Aufnahme-Fehler: {state.get('reason')}, siehe {live_common.log_file(rec_id)}", _lib.EXIT_ERROR,
                  rec_id=rec_id)
    _lib.emit({"rec_id": rec_id, "status": status, "pid": proc.pid, "spawn_mode": mode, "channel": channel,
               "rec_dir": str(rec_dir), "log": str(live_common.log_file(rec_id)),
               "next": f"record_status.py --rec-id {rec_id} --wait-spike", "warnings": warnings})


if __name__ == "__main__":
    main()
