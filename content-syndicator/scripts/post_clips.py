# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.49"]
# ///
"""Postet fertige Clips aus <CLIPS_HOME>/out/<vod-id>/.

Die Caption steht in clipXX.txt neben der MP4. Ohne --publish wird nur gezeigt,
was rausgehen würde.

Exit: 0 ok, 1 Upload fehlgeschlagen, 2 Eingabe ungültig, 4 Browser fehlt,
5 Anmeldung fehlt, 6 nativer Audio-Schritt steht aus.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postclips.browser import guide_tiktok_native, known_platforms, publish  # noqa: E402
from postclips.library import (  # noqa: E402
    PLATFORM_ORDER,
    LoginRequired,
    NativeMusic,
    discover_clips,
    load_sidecar,
    plan_posts,
    prepare_upload_music,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fertige Clips aus dem Ausgabeordner im eigenen Browser posten.",
    )
    parser.add_argument("--vod-id", required=True, help="Ordner unter out/")
    parser.add_argument("--clip", help="nur diese Clip-IDs, kommagetrennt")
    parser.add_argument("--platforms", default=",".join(PLATFORM_ORDER),
                        help="Komma-Liste, Standard sind alle fünf")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--publish", action="store_true", help="automatisierbare Uploads starten")
    action.add_argument("--open-native-editor", action="store_true",
                        help="einen freigegebenen TikTok-Clip im sichtbaren Webeditor vorbereiten")
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
        try:
            upload_music = prepare_upload_music(clips, platforms)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.open_native_editor:
            return _open_native_editor(args, home, jobs, upload_music)
        rows = [_public_job(job, upload_music.get(job.video)) for job in jobs]
        emit({
            "vod_id": args.vod_id,
            "dry_run": True,
            "home": str(home),
            "manual_required": sum(row["action"] == "manual_required" for row in rows),
            "clips": rows,
        })
        return 0

    try:
        result = publish(home, args.vod_id, platforms, only, args.headless, args.login_timeout)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
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
        "manual_required": result.manual_required,
        "clips": result.jobs,
    })
    if result.failed:
        return 1
    return 6 if result.manual_required else 0


def _open_native_editor(args, home, jobs, upload_music) -> int:
    if args.headless or not sys.stdin.isatty() or len(jobs) != 1 or jobs[0].platform != "tiktok":
        print("--open-native-editor benötigt ein Terminal, einen Clip und --platforms tiktok.",
              file=sys.stderr)
        return 2
    job = jobs[0]
    prepared = upload_music.get(job.video)
    if not isinstance(prepared, NativeMusic) or "tiktok" not in prepared.platforms:
        print("Für diesen Clip fehlt eine offene, freigegebene TikTok-Übergabe mit Plattform-Sound.",
              file=sys.stderr)
        return 2
    row = _public_job(job, prepared)
    try:
        opened = guide_tiktok_native(home, job, row["native_music"]["instructions"], args.login_timeout)
    except LoginRequired as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"TikTok-Editor konnte nicht vorbereitet werden: {exc}", file=sys.stderr)
        return 1
    emit({"vod_id": args.vod_id, "posted": 0, "manual_required": 1,
          "editor_opened": opened, "clips": [row]})
    return 0 if opened else 6


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _public_job(job, prepared=None) -> dict:
    text = {
        "youtube": job.texts["youtube_title"],
        "tiktok": job.texts["tiktok"],
        "instagram": job.texts["instagram"],
        "snapchat": job.texts["snapchat"],
        "x": job.texts["x"],
    }[job.platform]
    result = {
        "clip_id": job.clip_id,
        "platform": job.platform,
        "action": job.action,
        "reason": job.reason,
        "text": text,
        "video": str(job.video),
    }
    previous = (load_sidecar(job.sidecar_path).get("posts", {}).get(job.platform) or {}).get("status")
    if previous in {"posted", "skipped"}:
        result.update(action="skip", reason=f"Bereits verarbeitet ({previous}).")
    elif prepared and job.platform in prepared.platforms:
        if isinstance(prepared, NativeMusic):
            result.update(action="manual_required", reason="Plattform-Sound im jeweiligen Editor hinzufügen.",
                          **prepared.handoff(job))
        else:
            result["trend_music"] = prepared.summary()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
