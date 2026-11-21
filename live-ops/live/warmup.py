#!/usr/bin/env python3
"""Tageslimit für neue Konten. Likes, Kommentare und Folgen macht der User.

Liegt im Live-Ordner eine Datei warmup_since mit einer Unix-Zeit, gelten 14 Tage
lang engere Limits: Instagram 1 Post pro 24 h, TikTok 2. Danach bleibt es bei
den Limits in post_all.sh. Um 13:00 Europe/Berlin schreibt warmup.sh den Stand
nach warmup.json. Der Lauf liest nur diese Datei und die Post-Logs.

  warmup.py --write [--live DIR] [--now EPOCH]
  warmup.py --allow IG|TT --log DATEI [--live DIR] [--now EPOCH]

--allow endet mit 0, wenn gepostet werden darf, sonst mit 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
WARM_DAYS = 14
LIMITS = {"instagram": 1, "tiktok": 2}
KEYS = {"IG": "instagram", "TT": "tiktok"}


def live_dir(path: str) -> Path:
    if path:
        return Path(path).expanduser()
    return Path(os.path.expanduser("~/.hermes/cache/scratch/live"))


def read_since(folder: Path) -> float | None:
    path = folder / "warmup_since"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return float(text.split()[0])


def phase_limits(since: float | None, now: float) -> tuple[str, dict[str, int]]:
    if since is None or now - since >= WARM_DAYS * 86400:
        return "normal", {}
    return "warmup", dict(LIMITS)


def recent(log_path: Path | None, now: float, window: float = 86400) -> int:
    if log_path is None or not log_path.is_file():
        return 0
    count = 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        piece = line.strip().split()
        if not piece:
            continue
        try:
            stamp = float(piece[0])
        except ValueError:
            continue
        if now - stamp < window:
            count += 1
    return count


def status(folder: Path, now: float) -> dict:
    name, limits = phase_limits(read_since(folder), now)
    blocked = {key: (folder / f"{key}_BLOCKED").is_file() for key in KEYS}
    return {
        "phase": name,
        "limits": limits,
        "blocked": blocked,
        "checked_at": datetime.fromtimestamp(now, BERLIN).isoformat(timespec="seconds"),
        "tz": "Europe/Berlin",
    }


def allow(folder: Path, key: str, log_path: Path | None, now: float) -> tuple[bool, str]:
    if key not in KEYS:
        return False, f"unbekannte Plattform {key}"
    if (folder / f"{key}_BLOCKED").is_file():
        return False, "blocked"
    data = {}
    path = folder / "warmup.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    if data.get("phase") != "warmup":
        return True, "normal"
    limit = int((data.get("limits") or {}).get(KEYS[key]) or 0)
    if limit <= 0:
        return True, "normal"
    count = recent(log_path, now)
    if count >= limit:
        return False, f"warmup {count}/{limit} in 24 h"
    return True, "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warmup-Takt schreiben oder einen Post dagegen prüfen.")
    parser.add_argument("--live", default="", help="Live-Ordner, Standard ~/.hermes/cache/scratch/live")
    parser.add_argument("--now", type=float, default=0, help="Unix-Zeit, Tests")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--allow", choices=sorted(KEYS))
    parser.add_argument("--log", default="", help="Post-Log mit Unix-Zeiten, eine pro Zeile")
    args = parser.parse_args(argv)
    folder = live_dir(args.live)
    now = args.now or datetime.now().timestamp()
    folder.mkdir(parents=True, exist_ok=True)
    if args.write:
        payload = status(folder, now)
        (folder / "warmup.json").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    ok, reason = allow(folder, args.allow, Path(args.log).expanduser() if args.log else None, now)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
