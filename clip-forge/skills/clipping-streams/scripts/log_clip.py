# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Schreibt VOD, alle Kandidaten (auch verworfene) und gerenderte Clips nach <CLIPS_HOME>/clips.db.

Grundlage für den späteren Feedback-Loop: Signalwerte zum Zeitpunkt der Auswahl,
welcher Kandidat genommen wurde, und eine leere Tabelle `results` für Views,
"Stayed to watch" usw. (Phase 3). Idempotent: erneutes Loggen aktualisiert die Zeilen.

Aufruf: log_clip.py --vod-id <id> [--dry-run]
        log_clip.py --summary                    # Zählwerte aus der Datenbank
Exit: 0 ok, 2 Eingabe fehlt.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import _lib

SCHEMA = """
CREATE TABLE IF NOT EXISTS vods (
  vod_id TEXT PRIMARY KEY, streamer TEXT, source TEXT, url TEXT, title TEXT,
  duration_s REAL, chat_per_hour REAL, mode TEXT, logged_at TEXT);
CREATE TABLE IF NOT EXISTS candidates (
  vod_id TEXT, cand_id TEXT, moment_s REAL, score REAL, reasons TEXT, labels TEXT,
  signals_json TEXT, selected INTEGER, PRIMARY KEY (vod_id, cand_id));
CREATE TABLE IF NOT EXISTS clips (
  vod_id TEXT, clip_id TEXT, cand_id TEXT, file TEXT, duration_s REAL, segments_json TEXT,
  hook TEXT, title TEXT, captions_json TEXT, commentary TEXT, layout TEXT, encoder TEXT,
  rendered INTEGER, rendered_at TEXT, logged_at TEXT, PRIMARY KEY (vod_id, clip_id));
CREATE TABLE IF NOT EXISTS results (
  vod_id TEXT, clip_id TEXT, platform TEXT, hours_after INTEGER, views INTEGER,
  stayed_pct REAL, avg_watch_s REAL, shares INTEGER, follows INTEGER, recorded_at TEXT,
  PRIMARY KEY (vod_id, clip_id, platform, hours_after));
"""


def db_path() -> Path:
    return _lib.clips_home() / "clips.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def collect(vod_id: str) -> dict[str, Any]:
    work = _lib.work_dir(vod_id)
    if not (work / "meta.json").is_file():
        _lib.fail(f"{work / 'meta.json'} fehlt: zu diesem VOD gibt es nichts zu loggen.", _lib.EXIT_INVALID)
    meta = _lib.read_json(work / "meta.json")
    cands = _lib.read_json(work / "candidates.json") if (work / "candidates.json").is_file() else {}
    plan = _lib.read_json(work / "clips.json") if (work / "clips.json").is_file() else {"clips": []}
    rendered = {}
    odir = _lib.out_dir(vod_id)
    for side in sorted(odir.glob("*.json")) if odir.is_dir() else []:
        data = _lib.read_json(side)
        rendered[data["clip_id"]] = data
    selected = {c.get("candidate") for c in plan.get("clips", [])}
    return {"meta": meta, "cands": cands, "plan": plan, "rendered": rendered, "selected": selected}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kandidaten und Clips eines VODs in clips.db (SQLite) schreiben.",
        epilog="Exit: 0 ok, 2 Eingabe fehlt.",
    )
    parser.add_argument("--vod-id", help="VOD, dessen Daten geloggt werden")
    parser.add_argument("--summary", action="store_true", help="nur Zählwerte aus clips.db zeigen")
    parser.add_argument("--dry-run", action="store_true", help="zeigen, was geschrieben würde")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("log_clip", args.verbose)

    if args.summary:
        if not db_path().is_file():
            _lib.emit({"db": str(db_path()), "exists": False})
        con = connect()
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (feste Namen)
                  for t in ("vods", "candidates", "clips", "results")}
        con.close()
        _lib.emit({"db": str(db_path()), "exists": True, "counts": counts})
    if not args.vod_id:
        _lib.fail("--vod-id oder --summary angeben", _lib.EXIT_INVALID)

    d = collect(args.vod_id)
    meta, cands, plan, rendered, selected = d["meta"], d["cands"], d["plan"], d["rendered"], d["selected"]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cand_rows = [(args.vod_id, c["id"], c["moment_s"], c["score"], ",".join(c.get("reasons", [])),
                  ",".join(c.get("labels", [])), json.dumps(c.get("signals", {}), ensure_ascii=False),
                  int(c["id"] in selected)) for c in cands.get("candidates", [])]
    clip_rows = []
    for clip in plan.get("clips", []):
        r = rendered.get(clip.get("id"), {})
        clip_rows.append((args.vod_id, clip["id"], clip.get("candidate"), r.get("file"), r.get("duration_s"),
                          json.dumps(clip.get("segments"), ensure_ascii=False), clip.get("hook"), clip.get("title"),
                          json.dumps(clip.get("captions"), ensure_ascii=False), clip.get("commentary"),
                          r.get("layout"), r.get("encoder"), int(bool(r)), r.get("rendered_at"), now))
    summary = {"vod_id": args.vod_id, "db": str(db_path()), "candidates": len(cand_rows),
               "selected": sum(r[-1] for r in cand_rows), "clips": len(clip_rows),
               "rendered": sum(1 for r in clip_rows if r[12])}
    if args.dry_run:
        _lib.emit({"dry_run": True, **summary})
    con = connect()
    with con:
        con.execute("INSERT OR REPLACE INTO vods VALUES (?,?,?,?,?,?,?,?,?)",
                    (args.vod_id, meta.get("streamer"), meta.get("source"), meta.get("url"), meta.get("title"),
                     meta.get("duration_s"), cands.get("chat_per_hour"), cands.get("mode"), now))
        con.executemany("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?)", cand_rows)
        con.executemany("INSERT OR REPLACE INTO clips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", clip_rows)
    con.close()
    _lib.emit(summary)


if __name__ == "__main__":
    main()
