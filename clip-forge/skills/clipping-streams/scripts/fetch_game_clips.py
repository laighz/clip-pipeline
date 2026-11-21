# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Meistgesehene Twitch-Clips eines Spiels, 3 bis 24 Stunden alt.

Die Abfrage steht in live-ops/.../posting.md: GQL game(name){clips(period LAST_DAY,
sort VIEWS_DESC)}. Grand Theft Auto V (Helix-Id 32982) fliegt immer raus. Lässt sich
Grand Theft Auto VI nicht auflösen, endet der Lauf mit einem Fehler und fällt nicht
auf V zurück.

Aufruf:
  fetch_game_clips.py [--game "Grand Theft Auto VI"] [--fixture antwort.json]
                      [--now UNIX] [--blocklist datei]
Exit: 0 ok (auch ohne Treffer), 1 Spiel fehlt oder GQL scheitert, 2 Eingabe.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _https
import _lib
import log_clip

GTA_V_ID = "32982"
WINDOW_MIN_S = 3 * 3600
WINDOW_MAX_S = 24 * 3600
OFFSET_SLACK_S = 15.0
GQL_URL = "https://gql.twitch.tv/gql"
GQL_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"
QUERY = """
query GameClips($name: String!) {
  game(name: $name) {
    id
    name
    clips(criteria: {period: LAST_DAY, sort: VIEWS_DESC, languages: [DE, EN]}) {
      edges {
        node {
          id
          slug
          url
          viewCount
          durationSeconds
          createdAt
          title
          videoOffsetSeconds
          game { id name }
          broadcaster { login }
          video { id }
        }
      }
    }
  }
}
"""


def norm_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def is_gta_v(name: str | None, game_id: str | None = None) -> bool:
    """V, aber nicht VI. 'grand theft auto vi' enthält 'v' und darf nicht ziehen."""
    if str(game_id or "") == GTA_V_ID:
        return True
    text = norm_name(name)
    if re.search(r"grand theft auto v(?!i)", text):
        return True
    if re.search(r"\bgta\s*v(?!i)\b", text) or "gta 5" in text or "gta5" in text:
        return True
    return False


def is_gta_vi(name: str | None) -> bool:
    text = norm_name(name)
    return bool(re.search(r"grand theft auto vi\b", text) or re.search(r"\bgta\s*(?:vi|6)\b", text))


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def clip_url(node: dict[str, Any]) -> str:
    if node.get("url"):
        return str(node["url"])
    slug = node.get("slug") or node.get("id") or ""
    return f"https://clips.twitch.tv/{slug}" if slug else ""


def node_fields(node: dict[str, Any]) -> dict[str, Any]:
    game = node.get("game") or {}
    broadcaster = node.get("broadcaster") or {}
    video = node.get("video") or {}
    created = parse_time(node.get("createdAt"))
    offset = node.get("videoOffsetSeconds")
    if offset is None:
        offset = node.get("vodOffset")
    duration = node.get("durationSeconds")
    if duration is None:
        duration = node.get("duration")
    return {
        "id": str(node.get("id") or node.get("slug") or ""),
        "url": clip_url(node),
        "views": int(node.get("viewCount") or 0),
        "duration_s": float(duration or 0),
        "created_at": created,
        "video_id": str(video.get("id") or "") or None,
        "vod_offset": float(offset) if offset is not None else None,
        "login": str(broadcaster.get("login") or "").lower() or None,
        "game_id": str(game.get("id") or "") or None,
        "game_name": game.get("name"),
    }


def near_offset(video_id: str | None, offset: float | None, known: list[tuple[str, float]]) -> bool:
    if not video_id or offset is None:
        return False
    return any(video_id == vid and abs(offset - off) <= OFFSET_SLACK_S for vid, off in known)


def blocked(clip_id: str, url: str, lines: list[str]) -> bool:
    keys = {clip_id, url, url.rstrip("/")}
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item in keys or item.rstrip("/") in keys:
            return True
    return False


def select_clips(game: dict[str, Any] | None, now: float, known_ids: set[str],
                 known_offsets: list[tuple[str, float]], block_lines: list[str],
                 weights: dict[str, dict[str, float]] | None = None) -> tuple[list[dict], list[dict]]:
    """Treffer und Ablehnungen. Titel bleiben aussen vor: das sind Daten, keine Felder zum Sortieren."""
    if not game or not game.get("id"):
        raise LookupError("Spiel nicht gefunden")
    if is_gta_v(game.get("name"), str(game.get("id"))):
        raise LookupError(f"Spiel ist Grand Theft Auto V ({game.get('name')}), nicht VI")
    if not is_gta_vi(game.get("name")):
        raise LookupError(f"Spiel {game.get('name')!r} ist nicht Grand Theft Auto VI")
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    edges = ((game.get("clips") or {}).get("edges")) or []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        clip = node_fields(node)
        reason = _reject_reason(clip, now, known_ids, known_offsets, block_lines)
        if reason:
            rejected.append({"id": clip["id"], "reason": reason})
            continue
        bucket = log_clip._bucket_duration(clip["duration_s"])
        factor = float((weights or {}).get("duration", {}).get(bucket, 1.0))
        clip["score"] = round(clip["views"] * factor, 1)
        clip["duration_bucket"] = bucket
        kept.append(clip)
    kept.sort(key=lambda c: (-c["score"], c["id"]))
    return kept, rejected


def _reject_reason(clip: dict[str, Any], now: float, known_ids: set[str],
                   known_offsets: list[tuple[str, float]], block_lines: list[str]) -> str | None:
    if not clip["id"]:
        return "keine Clip-Id"
    if is_gta_v(clip.get("game_name"), clip.get("game_id")):
        return "GTA V"
    if clip["created_at"] is None:
        return "kein createdAt"
    age = now - clip["created_at"]
    if age < WINDOW_MIN_S:
        return "jünger als 3 h"
    if age > WINDOW_MAX_S:
        return "älter als 24 h"
    if clip["id"] in known_ids or clip["url"] in known_ids:
        return "schon in clips.db"
    if near_offset(clip.get("video_id"), clip.get("vod_offset"), known_offsets):
        return "gleicher VOD-Offset"
    if blocked(clip["id"], clip["url"], block_lines):
        return "blocklist"
    return None


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "data" in data:
        return data["data"].get("game")
    return data.get("game")


def fetch_gql(game_name: str) -> dict[str, Any]:
    body = json.dumps({"query": QUERY, "variables": {"name": game_name}}).encode()
    raw = _https.read_https(GQL_URL, data=body, headers={
        "Client-Id": GQL_CLIENT, "Content-Type": "application/json"}, timeout=30)
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"])[:400])
    return (payload.get("data") or {}).get("game")


def read_blocklist(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def db_state() -> tuple[set[str], list[tuple[str, float]], dict[str, dict[str, float]]]:
    if not log_clip.db_path().is_file():
        return set(), [], {"duration": {}, "hook": {}}
    con = log_clip.connect()
    try:
        ids, offsets = log_clip.known_sources(con)
        weights = log_clip.lesson_weights(con)
    finally:
        con.close()
    return ids, offsets, weights


def main() -> None:
    parser = argparse.ArgumentParser(description="GTA-VI-Clips der letzten 3–24 h nach Views.")
    parser.add_argument("--game", default="Grand Theft Auto VI")
    parser.add_argument("--fixture", type=Path, help="GQL-JSON statt Netz")
    parser.add_argument("--now", type=float, help="Unix-Zeit fürs Fenster (Tests)")
    parser.add_argument("--blocklist", type=Path, help="eine Clip-Id oder URL pro Zeile")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("fetch_game_clips", args.verbose)
    now = args.now if args.now is not None else time.time()
    try:
        game = load_fixture(args.fixture) if args.fixture else fetch_gql(args.game)
        ids, offsets, weights = db_state()
        kept, rejected = select_clips(game, now, ids, offsets, read_blocklist(args.blocklist), weights)
    except LookupError as exc:
        _lib.fail(str(exc), _lib.EXIT_ERROR)
    except (OSError, json.JSONDecodeError, RuntimeError, _https.HttpsError) as exc:
        _lib.fail(f"GQL fehlgeschlagen: {exc}", _lib.EXIT_ERROR)
    _lib.emit({
        "game_id": str(game.get("id")),
        "game_name": game.get("name"),
        "candidates": kept,
        "rejected": rejected,
    })


if __name__ == "__main__":
    main()
