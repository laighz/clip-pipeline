# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Schreibt Plattform-Kennzahlen in die vorhandene Tabelle results.

Nimmt entweder eine Fixture mit den API-Antworten, Dateien out/<vod>/<clip>.stats.json,
oder holt die Zahlen selbst, wenn Token und external_id da sind. Watch Time und
Absprung bleiben NULL, wenn die Antwort sie nicht hergibt. Instagram-Durchschnittszeit
wird nicht übernommen: die gelesene Doku legt die Einheit nicht als Sekunden fest.

Aufruf:
  collect_results.py --fixture antworten.json
  collect_results.py --scan
  collect_results.py --fetch
Exit: 0 ok (auch ohne Zeilen), 1 Abruf- oder Schreibfehler, 2 Eingabe ungültig.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import log_clip
from log_clip import _lib

YOUTUBE_METRICS = "views,averageViewDuration,averageViewPercentage,comments"
TIKTOK_FIELDS = ["item_id", "video_views", "average_time_watched", "comments", "shares"]


def youtube_url(video_id: str, end_day: str) -> str:
    query = urllib.parse.urlencode({
        "ids": "channel==MINE",
        "filters": f"video=={video_id}",
        "metrics": YOUTUBE_METRICS,
        "startDate": "2008-07-01",
        "endDate": end_day,
    })
    return "https://youtubeanalytics.googleapis.com/v2/reports?" + query


def tiktok_url(business_id: str, video_id: str) -> str:
    query = urllib.parse.urlencode({
        "business_id": business_id,
        "fields": json.dumps(TIKTOK_FIELDS, separators=(",", ":")),
        "filters": json.dumps({"video_ids": [video_id]}, separators=(",", ":")),
    })
    return "https://business-api.tiktok.com/open_api/v1.3/business/video/list/?" + query


def instagram_url(media_id: str, token: str) -> str:
    query = urllib.parse.urlencode({"metric": "views", "access_token": token})
    return f"https://graph.facebook.com/v22.0/{urllib.parse.quote(media_id, safe='')}/insights?{query}"


def _num(value: Any, cast: Callable) -> Any:
    if value is None or value == "":
        return None
    return cast(value)


def parse_youtube(body: dict) -> dict:
    if body.get("error"):
        raise ValueError(str(body["error"])[:300])
    headers = [item.get("name") for item in body.get("columnHeaders") or []]
    rows = body.get("rows") or []
    if not rows or "views" not in headers:
        raise ValueError("youtube ohne views")
    row = rows[0]
    data = {name: row[index] for index, name in enumerate(headers) if name and index < len(row)}
    return {
        "views": int(float(data["views"])),
        "avg_watch_s": _num(data.get("averageViewDuration"), float),
        "stayed_pct": _num(data.get("averageViewPercentage"), float),
        "shares": None,
        "comments": _num(data.get("comments"), lambda value: int(float(value))),
    }


def parse_tiktok(body: dict, video_id: str | None = None) -> dict:
    if body.get("code") not in (0, None):
        raise ValueError(str(body.get("message") or "tiktok fehler")[:300])
    videos = ((body.get("data") or {}).get("videos") or [])
    video = None
    if video_id is not None:
        video = next((item for item in videos if str(item.get("item_id")) == str(video_id)), None)
    elif len(videos) == 1:
        video = videos[0]
    if not video or "video_views" not in video:
        raise ValueError("tiktok ohne video_views")
    return {
        "views": int(video["video_views"]),
        "avg_watch_s": _num(video.get("average_time_watched"), float),
        "stayed_pct": None,
        "shares": _num(video.get("shares"), int),
        "comments": _num(video.get("comments"), int),
    }


def parse_instagram(body: dict) -> dict:
    """Nur views. Die Durchschnittszeit hat in der gelesenen Doku keine Sekunden-Einheit."""
    if body.get("error"):
        raise ValueError(str(body["error"])[:300])
    views = None
    for item in body.get("data") or []:
        if item.get("name") != "views":
            continue
        values = item.get("values") or []
        if values and values[0].get("value") is not None:
            views = int(values[0]["value"])
    if views is None:
        raise ValueError("instagram ohne views")
    return {"views": views, "avg_watch_s": None, "stayed_pct": None, "shares": None, "comments": None}


def parse_body(platform: str, body: dict, video_id: str | None = None) -> dict:
    if platform == "youtube":
        return parse_youtube(body)
    if platform == "tiktok":
        return parse_tiktok(body, video_id)
    if platform == "instagram":
        return parse_instagram(body)
    raise ValueError(f"unbekannte Plattform {platform}")


def rows_from_fixture(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Fixture braucht eine Liste items")
    rows = []
    for item in items:
        platform = item.get("platform")
        metrics = parse_body(platform, item.get("body") or {}, item.get("external_id"))
        rows.append({
            "vod_id": item["vod_id"], "clip_id": item["clip_id"], "platform": platform,
            "hours_after": int(item.get("hours_after") or 24), **metrics,
        })
    return rows


def rows_from_stats(home: Path) -> list[dict]:
    rows = []
    out = home / "out"
    if not out.is_dir():
        return rows
    for path in sorted(out.glob("*/*.stats.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        clip_id = path.name[: -len(".stats.json")]
        for platform, stats in data.items():
            if not isinstance(stats, dict) or "views" not in stats:
                continue
            rows.append({
                "vod_id": path.parent.name, "clip_id": clip_id, "platform": platform,
                "hours_after": int(stats.get("hours_after") or 24),
                "views": int(stats["views"]),
                "shares": stats.get("shares"),
                "comments": stats.get("comments"),
                "avg_watch_s": stats.get("avg_watch_s"),
                "stayed_pct": stats.get("stayed_pct"),
            })
    return rows


def posted_targets(home: Path) -> list[dict]:
    found = []
    out = home / "out"
    if not out.is_dir():
        return found
    for path in sorted(out.glob("*/*.post.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for platform, info in (data.get("posts") or {}).items():
            if not isinstance(info, dict) or info.get("status") != "posted" or not info.get("external_id"):
                continue
            found.append({
                "vod_id": path.parent.name,
                "clip_id": path.name[: -len(".post.json")],
                "platform": platform,
                "external_id": str(info["external_id"]),
            })
    return found


def fetch_one(target: dict, opener: Callable, end_day: str) -> dict:
    platform = target["platform"]
    video_id = target["external_id"]
    if platform == "youtube":
        token = os.environ.get("YOUTUBE_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("YOUTUBE_ACCESS_TOKEN fehlt")
        url = youtube_url(video_id, end_day)
        headers = {"Authorization": f"Bearer {token}"}
    elif platform == "tiktok":
        token = os.environ.get("TIKTOK_ACCESS_TOKEN")
        business = os.environ.get("TIKTOK_BUSINESS_ID")
        if not token or not business:
            raise RuntimeError("TIKTOK_ACCESS_TOKEN oder TIKTOK_BUSINESS_ID fehlt")
        url = tiktok_url(business, video_id)
        headers = {"Access-Token": token}
    elif platform == "instagram":
        token = os.environ.get("IG_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("IG_ACCESS_TOKEN fehlt")
        url = instagram_url(video_id, token)
        headers = {}
    else:
        raise RuntimeError(f"kein Abruf für {platform}")
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    return parse_body(platform, body, video_id)


def store_rows(rows: list[dict]) -> int:
    con = log_clip.connect()
    try:
        with con:
            for row in rows:
                log_clip.write_result(
                    con, row["vod_id"], row["clip_id"], row["platform"], int(row["hours_after"]),
                    int(row["views"]), row.get("shares"), row.get("comments"),
                    row.get("avg_watch_s"), row.get("stayed_pct"),
                )
    finally:
        con.close()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kennzahlen in clips.db results schreiben.")
    parser.add_argument("--fixture", type=Path, help="API-Antworten, kein Netz")
    parser.add_argument("--scan", action="store_true", help="out/<vod>/<clip>.stats.json lesen")
    parser.add_argument("--fetch", action="store_true", help="APIs abrufen, Token aus der Umgebung")
    parser.add_argument("--hours-after", type=int, default=24)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("collect_results", args.verbose)
    if not args.fixture and not args.scan and not args.fetch:
        _lib.fail("--fixture, --scan oder --fetch angeben", _lib.EXIT_INVALID)
    home = _lib.clips_home()
    rows: list[dict] = []
    skipped: list[dict] = []
    try:
        if args.fixture:
            rows.extend(rows_from_fixture(args.fixture))
        if args.scan:
            rows.extend(rows_from_stats(home))
        if args.fetch:
            end_day = datetime.now(timezone.utc).date().isoformat()
            for target in posted_targets(home):
                try:
                    metrics = fetch_one(target, urllib.request.urlopen, end_day)
                except Exception as exc:  # noqa: BLE001
                    reason = str(exc)
                    for key in ("YOUTUBE_ACCESS_TOKEN", "TIKTOK_ACCESS_TOKEN", "IG_ACCESS_TOKEN",
                                "TIKTOK_BUSINESS_ID"):
                        secret = os.environ.get(key)
                        if secret:
                            reason = reason.replace(secret, "***")
                    skipped.append({"vod_id": target["vod_id"], "clip_id": target["clip_id"],
                                    "platform": target["platform"], "reason": reason[:300]})
                    continue
                rows.append({**target, "hours_after": args.hours_after, **metrics})
        written = store_rows(rows) if rows else 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _lib.fail(str(exc), _lib.EXIT_INVALID)
    _lib.emit({"written": written, "skipped": skipped, "db": str(log_clip.db_path()),
               "instagram_avg_watch_s": "nicht übernommen"})


if __name__ == "__main__":
    main()
