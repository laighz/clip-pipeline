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
  stayed_pct REAL, avg_watch_s REAL, shares INTEGER, follows INTEGER, comments INTEGER,
  recorded_at TEXT,
  PRIMARY KEY (vod_id, clip_id, platform, hours_after));
CREATE TABLE IF NOT EXISTS sources (
  twitch_clip_id TEXT PRIMARY KEY, vod_id TEXT, clip_id TEXT, video_id TEXT,
  vod_offset REAL, url TEXT, game_id TEXT, game_name TEXT, logged_at TEXT);
"""


def db_path() -> Path:
    return _lib.clips_home() / "clips.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    cols = {row[1] for row in con.execute("PRAGMA table_info(results)")}
    if "comments" not in cols:
        con.execute("ALTER TABLE results ADD COLUMN comments INTEGER")
    return con


def known_sources(con: sqlite3.Connection) -> tuple[set[str], list[tuple[str, float]]]:
    """Schon benutzte Twitch-Clip-Ids und (video_id, vod_offset) aus clips.db."""
    ids = {row[0] for row in con.execute("SELECT twitch_clip_id FROM sources") if row[0]}
    offsets = [(row[0], float(row[1])) for row in con.execute(
        "SELECT video_id, vod_offset FROM sources WHERE video_id IS NOT NULL AND vod_offset IS NOT NULL")]
    return ids, offsets


def lesson_weights(con: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Multiplikator je Dauer- und Hook-Klasse aus results × clips.

    Unter zwei Proben in einer Klasse bleibt das Gewicht 1. Sonst 1.15, wenn der
    Mittelwert über dem 1,5-fachen des Gesamtmittels liegt, und 0.85, wenn er
    darunter beim halben Mittel liegt.
    """
    rows = con.execute(
        """SELECT c.duration_s, c.hook, r.views FROM results r
           JOIN clips c ON c.vod_id = r.vod_id AND c.clip_id = r.clip_id
           WHERE r.views IS NOT NULL"""
    ).fetchall()
    return _weights(rows)


def _bucket_duration(seconds: float | None) -> str:
    if seconds is None:
        return "mid"
    if seconds < 20:
        return "short"
    if seconds < 40:
        return "mid"
    return "long"


def _bucket_hook(hook: str | None) -> str:
    return "short" if len((hook or "").strip()) <= 24 else "long"


def _weights(rows: list[tuple]) -> dict[str, dict[str, float]]:
    groups: dict[str, dict[str, list[float]]] = {"duration": {}, "hook": {}}
    all_views: list[float] = []
    for duration, hook, views in rows:
        value = float(views)
        all_views.append(value)
        groups["duration"].setdefault(_bucket_duration(duration), []).append(value)
        groups["hook"].setdefault(_bucket_hook(hook), []).append(value)
    if not all_views:
        return {"duration": {}, "hook": {}}
    overall = sum(all_views) / len(all_views)

    def factor(samples: list[float]) -> float:
        if len(samples) < 2 or overall <= 0:
            return 1.0
        mean = sum(samples) / len(samples)
        if mean > overall * 1.5:
            return 1.15
        if mean < overall * 0.5:
            return 0.85
        return 1.0

    return {kind: {name: factor(samples) for name, samples in buckets.items()}
            for kind, buckets in groups.items()}


def write_result(con: sqlite3.Connection, vod_id: str, clip_id: str, platform: str,
                 hours_after: int, views: int, shares: int, comments: int) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    con.execute(
        """INSERT OR REPLACE INTO results
           (vod_id, clip_id, platform, hours_after, views, stayed_pct, avg_watch_s,
            shares, follows, comments, recorded_at)
           VALUES (?,?,?,?,?,NULL,NULL,?,NULL,?,?)""",
        (vod_id, clip_id, platform, hours_after, views, shares, comments, now),
    )


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
    parser.add_argument("--result", action="store_true", help="eine Zeile in results schreiben")
    parser.add_argument("--clip", help="Clip-ID für --result")
    parser.add_argument("--platform", help="Plattform für --result")
    parser.add_argument("--views", type=int, help="Views für --result")
    parser.add_argument("--shares", type=int, default=0)
    parser.add_argument("--comments", type=int, default=0)
    parser.add_argument("--hours-after", type=int, default=24)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("log_clip", args.verbose)

    if args.result:
        if not args.vod_id or not args.clip or not args.platform or args.views is None:
            _lib.fail("--result braucht --vod-id, --clip, --platform und --views", _lib.EXIT_INVALID)
        if args.dry_run:
            _lib.emit({"dry_run": True, "vod_id": args.vod_id, "clip": args.clip, "platform": args.platform,
                       "views": args.views})
        con = connect()
        with con:
            write_result(con, args.vod_id, args.clip, args.platform, args.hours_after,
                         args.views, args.shares, args.comments)
        con.close()
        _lib.emit({"vod_id": args.vod_id, "clip": args.clip, "platform": args.platform,
                   "views": args.views, "shares": args.shares, "comments": args.comments})

    if args.summary:
        if not db_path().is_file():
            _lib.emit({"db": str(db_path()), "exists": False})
        con = connect()
        queries = {
            "vods": "SELECT COUNT(*) FROM vods",
            "candidates": "SELECT COUNT(*) FROM candidates",
            "clips": "SELECT COUNT(*) FROM clips",
            "results": "SELECT COUNT(*) FROM results",
            "sources": "SELECT COUNT(*) FROM sources",
        }
        counts = {name: con.execute(sql).fetchone()[0] for name, sql in queries.items()}
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
    source_path = work / "source.json" if (work := _lib.work_dir(args.vod_id)) else None
    source = _lib.read_json(source_path) if source_path is not None and source_path.is_file() else None
    summary = {"vod_id": args.vod_id, "db": str(db_path()), "candidates": len(cand_rows),
               "selected": sum(r[-1] for r in cand_rows), "clips": len(clip_rows),
               "rendered": sum(1 for r in clip_rows if r[12]),
               "source": bool(source and source.get("twitch_clip_id"))}
    if args.dry_run:
        _lib.emit({"dry_run": True, **summary})
    con = connect()
    with con:
        con.execute("INSERT OR REPLACE INTO vods VALUES (?,?,?,?,?,?,?,?,?)",
                    (args.vod_id, meta.get("streamer"), meta.get("source"), meta.get("url"), meta.get("title"),
                     meta.get("duration_s"), cands.get("chat_per_hour"), cands.get("mode"), now))
        con.executemany("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?)", cand_rows)
        con.executemany("INSERT OR REPLACE INTO clips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", clip_rows)
        if source and source.get("twitch_clip_id"):
            clip_id = (plan.get("clips") or [{}])[0].get("id")
            con.execute(
                """INSERT OR REPLACE INTO sources
                   (twitch_clip_id, vod_id, clip_id, video_id, vod_offset, url, game_id, game_name, logged_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (source.get("twitch_clip_id"), args.vod_id, clip_id, source.get("video_id"),
                 source.get("vod_offset"), source.get("url"), source.get("game_id"),
                 source.get("game_name"), now),
            )
    con.close()
    _lib.emit(summary)


if __name__ == "__main__":
    main()
