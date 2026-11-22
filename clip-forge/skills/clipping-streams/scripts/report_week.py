# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Wochenreport aus clips, results, sources und posts.

Schreibt ~/clips/reports/YYYY-WW.md. Vorschläge für streamers.yaml stehen nur
in dieser Datei. Die YAML wird nicht angefasst, und es gibt keine Tabelle stats.

Aufruf: report_week.py [--out datei] [--now ISO] [--dry-run]
Exit: 0 ok, 2 Datenbank fehlt.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import log_clip
from log_clip import _lib

BERLIN = ZoneInfo("Europe/Berlin")
FACTOR = 2.0
LOOKBACK = 30


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2


def bucket_duration(seconds: float | None) -> str:
    return log_clip._bucket_duration(seconds)


def bucket_hook(hook: str | None) -> str:
    return log_clip._bucket_hook(hook)


def hour_of(stamp: str | None) -> str:
    if not stamp:
        return "unbekannt"
    text = stamp.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return "unbekannt"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    return f"{parsed.astimezone(BERLIN).hour:02d}"


def table_count(con: sqlite3.Connection, name: str) -> int:
    if name not in {"sources", "posts", "results", "clips"}:
        return 0
    found = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    if not found:
        return 0
    return int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])  # noqa: S608


def load_rows(con: sqlite3.Connection) -> list[dict]:
    has_posts = table_count(con, "posts") >= 0 and con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='posts'").fetchone()
    posted_sql = (
        "(SELECT COUNT(*) FROM posts p WHERE p.vod_id = r.vod_id AND p.clip_id = r.clip_id "
        "AND p.status = 'posted')" if has_posts else "0"
    )
    sql = f"""
        SELECT r.vod_id, r.clip_id, r.platform, r.views, r.avg_watch_s, r.stayed_pct,
               r.recorded_at, v.streamer, c.duration_s, c.hook, c.cand_id, cand.labels,
               {posted_sql} AS posted_n
        FROM results r
        JOIN clips c ON c.vod_id = r.vod_id AND c.clip_id = r.clip_id
        JOIN vods v ON v.vod_id = r.vod_id
        LEFT JOIN candidates cand ON cand.vod_id = c.vod_id AND cand.cand_id = c.cand_id
        WHERE r.views IS NOT NULL
        ORDER BY r.recorded_at, r.vod_id, r.clip_id, r.platform
    """
    rows = []
    for item in con.execute(sql):
        rows.append({
            "vod_id": item[0], "clip_id": item[1], "platform": item[2], "views": int(item[3]),
            "avg_watch_s": item[4], "stayed_pct": item[5], "recorded_at": item[6],
            "streamer": item[7] or "unbekannt", "duration_s": item[8], "hook": item[9] or "",
            "cand_id": item[10], "labels": item[11] or "", "posted_n": int(item[12] or 0),
        })
    return rows


def best_per_clip(rows: list[dict]) -> list[dict]:
    chosen: dict[tuple, dict] = {}
    for row in rows:
        key = (row["vod_id"], row["clip_id"])
        current = chosen.get(key)
        if current is None or row["views"] > current["views"]:
            chosen[key] = row
    return list(chosen.values())


def group_lines(clips: list[dict], key_fn) -> list[str]:
    groups: dict[str, list[int]] = {}
    for clip in clips:
        groups.setdefault(key_fn(clip), []).append(clip["views"])
    lines = []
    for name in sorted(groups):
        values = groups[name]
        mid = median([float(value) for value in values])
        lines.append(f"- {name}: {len(values)} Clips, Median {mid:.0f} Views")
    return lines or ["- keine Zeilen"]


def suggestion(clips: list[dict]) -> str:
    if len(clips) < 5:
        return "zu wenig Clips in results für einen Vorschlag (unter 5)."
    views = [float(clip["views"]) for clip in clips]
    mid = median(views)
    if mid is None:
        return "zu wenig Clips in results für einen Vorschlag."
    winners = [clip for clip in clips if clip["views"] > FACTOR * mid]
    if len(winners) < 2:
        return (f"kein Ausreißer über {FACTOR:g}× Median ({mid:.0f} Views). "
                "chat_sigma: kein Vorschlag, results enthält die Kennzahl nicht.")
    winner_len = median([float(clip["duration_s"]) for clip in winners if clip["duration_s"] is not None])
    overall_len = median([float(clip["duration_s"]) for clip in clips if clip["duration_s"] is not None])
    if winner_len is None or overall_len is None or winner_len + 5 >= overall_len:
        return "chat_sigma: kein Vorschlag, results enthält die Kennzahl nicht."
    proposed = int(round(winner_len))
    return (
        "chat_sigma: kein Vorschlag, results enthält die Kennzahl nicht.\n\n"
        "```yaml\n"
        "# Vorschlag, nicht in streamers.yaml geschrieben\n"
        "defaults:\n"
        f"  max_clip_s: {proposed}\n"
        "```"
    )


def render(con: sqlite3.Connection, moment: datetime) -> str:
    rows = load_rows(con)
    clips = best_per_clip(rows)
    ordered = sorted(clips, key=lambda clip: (-clip["views"], clip["vod_id"], clip["clip_id"]))
    recent = sorted(clips, key=lambda clip: (clip["recorded_at"] or "", clip["vod_id"]))[-LOOKBACK:]
    mid = median([float(clip["views"]) for clip in recent])
    outliers = []
    if mid is not None:
        outliers = [clip for clip in recent if clip["views"] > FACTOR * mid]
    week = moment.astimezone(BERLIN).strftime("%G-%V")
    lines = [
        f"# Woche {week}",
        "",
        f"Quellen: results {table_count(con, 'results')}, clips {table_count(con, 'clips')}, "
        f"sources {table_count(con, 'sources')}, posts {table_count(con, 'posts')}.",
        "streamers.yaml wurde nicht geändert.",
        "",
        "## Top 5",
    ]
    for clip in ordered[:5]:
        lines.append(f"- {clip['vod_id']} {clip['clip_id']} {clip['streamer']}: {clip['views']} Views")
    if not ordered:
        lines.append("- keine Zeilen")
    lines += ["", "## Flop 5"]
    for clip in sorted(clips, key=lambda item: (item["views"], item["vod_id"]))[:5]:
        lines.append(f"- {clip['vod_id']} {clip['clip_id']} {clip['streamer']}: {clip['views']} Views")
    if not clips:
        lines.append("- keine Zeilen")
    lines += ["", f"## Ausreißer (>{FACTOR:g}× Median der letzten {min(len(recent), LOOKBACK)} Clips)"]
    if outliers:
        for clip in outliers:
            lines.append(f"- {clip['vod_id']} {clip['clip_id']}: {clip['views']} Views")
    else:
        lines.append("- keine")
    lines += ["", "## Muster", "### Streamer", *group_lines(clips, lambda clip: str(clip["streamer"]))]
    lines += ["### Länge", *group_lines(clips, lambda clip: bucket_duration(clip["duration_s"]))]
    lines += ["### Hook", *group_lines(clips, lambda clip: bucket_hook(clip["hook"]))]
    lines += ["### Emote-Kategorie", *group_lines(clips, lambda clip: clip["labels"] or "keine")]
    lines += ["### Stunde (Europe/Berlin)", *group_lines(clips, lambda clip: hour_of(clip["recorded_at"]))]
    lines += ["", "## Vorschlag", suggestion(clips), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wochenreport aus clips.db, ohne die Config zu schreiben.")
    parser.add_argument("--out", type=Path, help="Zieldatei, sonst ~/clips/reports/YYYY-WW.md")
    parser.add_argument("--now", help="Zeitpunkt für die Woche, ISO-8601")
    parser.add_argument("--dry-run", action="store_true", help="nur JSON, Datei bleibt liegen")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("report_week", args.verbose)
    if not log_clip.db_path().is_file():
        _lib.fail(f"{log_clip.db_path()} fehlt", _lib.EXIT_INVALID)
    if args.now:
        moment = datetime.fromisoformat(args.now)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BERLIN)
    else:
        moment = datetime.now(BERLIN)
    con = log_clip.connect()
    try:
        text = render(con, moment)
        counts = {
            "results": table_count(con, "results"),
            "sources": table_count(con, "sources"),
            "posts": table_count(con, "posts"),
        }
    finally:
        con.close()
    week = moment.astimezone(BERLIN).strftime("%G-%V")
    target = args.out or (_lib.clips_home() / "reports" / f"{week}.md")
    if not args.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _lib.emit({
        "week": week,
        "path": None if args.dry_run else str(target),
        "dry_run": bool(args.dry_run),
        "wrote_config": False,
        "counts": counts,
        "suggestion": "max_clip_s" in text,
    })


if __name__ == "__main__":
    main()
