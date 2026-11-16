"""Gemeinsame Helfer für die Live-Aufnahme (Standardbibliothek + _lib im selben Ordner).

Pfade:
  <CLIPS_HOME>/state/live_<rec_id>.json   Status (schreibt der Worker alle paar Sekunden)
  <CLIPS_HOME>/state/live_<rec_id>.stop   Stopp-Signal (legt record_stop.py an)
  <CLIPS_HOME>/logs/live_<rec_id>.log     Worker-Log inkl. SPIKE-Zeilen
  <live.record_dir>/<rec_id>/             seg_0000.ts ..., segments.csv, chat_counts.jsonl
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _lib  # noqa: E402

SPIKE_RE = re.compile(r"SPIKE (?P<wall>\S+) t=(?P<t>\d+) moment=(?P<moment>-?\d+) z=(?P<z>[\d.]+) "
                      r"uniq=(?P<uniq>\d+)(?: labels=(?P<labels>\S*))?")


def state_file(rec_id: str) -> Path:
    return _lib.state_dir() / f"live_{rec_id}.json"


def stop_file(rec_id: str) -> Path:
    return _lib.state_dir() / f"live_{rec_id}.stop"


def log_file(rec_id: str) -> Path:
    return _lib.clips_home() / "logs" / f"live_{rec_id}.log"


def record_root(cfg: dict) -> Path:
    return Path(str(cfg["live"]["record_dir"])).expanduser()


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259   # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def read_spikes(rec_id: str) -> list[dict]:
    path = log_file(rec_id)
    if not path.is_file():
        return []
    spikes = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = SPIKE_RE.search(line)
        if m:
            spikes.append({"n": len(spikes) + 1, "wall": m["wall"], "t": int(m["t"]), "moment": int(m["moment"]),
                           "z": float(m["z"]), "uniq": int(m["uniq"]),
                           "labels": [x for x in (m["labels"] or "").split(",") if x]})
    return spikes


def read_segments(rec_dir: Path) -> list[dict]:
    """Fertige Segmente aus segments.csv (ffmpeg schreibt eine Zeile, wenn ein Segment geschlossen ist)."""
    csv = rec_dir / "segments.csv"
    if not csv.is_file():
        return []
    segs = []
    for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3:
            try:
                segs.append({"file": str(rec_dir / parts[0]), "start": float(parts[1]), "end": float(parts[2])})
            except ValueError:
                continue
    if segs:   # Zeitachse auf Aufnahmebeginn normieren
        base = segs[0]["start"]
        for s in segs:
            s["start"] = round(s["start"] - base, 3)
            s["end"] = round(s["end"] - base, 3)
    return segs
