# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.49"]
# ///
"""Postet fertige Clips aus <CLIPS_HOME>/out/<vod-id>/.

Die Caption steht in clipXX.txt neben der MP4. Ohne --publish wird nur gezeigt,
was rausgehen würde.

Exit: 0 ok, 1 eine Seite hat abgebrochen, 2 Eingabe ungültig, 4 Browser fehlt, 5 Anmeldung fehlt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postclips.browser import known_platforms, publish  # noqa: E402
from postclips.library import (  # noqa: E402
    PLATFORM_ORDER,
    LoginRequired,
    discover_clips,
    plan_posts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fertige Clips aus dem Ausgabeordner im eigenen Browser posten.",
    )
    parser.add_argument("--vod-id", required=True, help="Ordner unter out/")
    parser.add_argument("--clip", help="nur diese Clip-IDs, kommagetrennt")
    parser.add_argument("--platforms", default=",".join(PLATFORM_ORDER),
                        help="Komma-Liste, Standard sind alle fünf")
    parser.add_argument("--publish", action="store_true", help="wirklich posten, sonst nur anzeigen")
    parser.add_argument("--home", default=os.environ.get("CLIPS_HOME", ""),
                        help="Datenordner, sonst CLIPS_HOME oder ~/clips")
    parser.add_argument("--login-timeout", type=int, default=300, help="Sekunden fürs eigene Einloggen")
    parser.add_argument("--headless", action="store_true", help="ohne Fenster, nur wenn die Anmeldung schon sitzt")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else Path.home() / "clips"
    try:
        platforms = known_platforms(args.platforms)
        only = {part.strip() for part in args.clip.split(",") if part.strip()} if args.clip else None
        clips = discover_clips(home, args.vod_id, only)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not clips:
        print(f"Keine clip*.mp4 in {home / 'out' / args.vod_id}.", file=sys.stderr)
        return 2

    if not args.publish:
        jobs = plan_posts(clips, platforms)
        emit({
            "vod_id": args.vod_id,
            "dry_run": True,
            "home": str(home),
            "clips": [_public_job(job) for job in jobs],
        })
        return 0

    try:
        result = publish(home, args.vod_id, platforms, only, args.headless, args.login_timeout)
    except LoginRequired as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 4
    emit({
        "vod_id": args.vod_id,
        "dry_run": False,
        "posted": result.posted,
        "failed": result.failed,
        "skipped": result.skipped,
        "clips": result.jobs,
    })
    return 1 if result.failed else 0


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _public_job(job) -> dict:
    text = {
        "youtube": job.texts["youtube_title"],
        "tiktok": job.texts["tiktok"],
        "instagram": job.texts["instagram"],
        "snapchat": job.texts["snapchat"],
        "x": job.texts["x"],
    }[job.platform]
    return {
        "clip_id": job.clip_id,
        "platform": job.platform,
        "action": job.action,
        "reason": job.reason,
        "text": text,
        "video": str(job.video),
    }


if __name__ == "__main__":
    raise SystemExit(main())
