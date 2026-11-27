# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Schreibt clips.json für einen kurzen GTA-Clip: Facecam oben, Spiel unten, Hook vorn.

Der stärkste Moment wird Cold Open (max. 3 s), der Rest endet als Payoff.
Hook und Titel nutzen ein kurzes Zitat aus dem Aufbau, wenn eines passt.
Der Spielausschnitt lässt das untere Chat-Band weg (Standard 22 Prozent).
Die Facecam setzt dieses Skript nicht: die kommt von detect_facecam.py.

Exit: 0 ok, 2 Eingabe oder Clip kürzer als 8 s.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import _lib

CHAT_BAND = 0.22
HOOK_MAX_CHARS = 40
HOOK_CONTEXT_S = 8
FILLER = frozenset("aber ach alles also auch bitte bro chat damit danke dass deine doch egal eine einen "
                   "einfach genau gleich gott hallo heute hier irgendwie jetzt keiner leute nicht noch okay "
                   "schau schon sehr sind sowas warte weil weiter wenn wieder wirklich wurde wieso zusammen".split())


def frame_size(meta: dict[str, Any]) -> tuple[int, int]:
    width, height = meta.get("width"), meta.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
        return int(width), int(height)
    src = meta.get("input")
    if src and Path(src).is_file():
        try:
            info = _lib.ffprobe_json(Path(src))
        except (OSError, RuntimeError):
            info = {}
        video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        if video and video.get("width") and video.get("height"):
            return int(video["width"]), int(video["height"])
    return 1920, 1080


def safe_game(width: int, height: int, current: list | None = None) -> list[int]:
    """Spielausschnitt, dessen Unterkante über dem Chat-Band bleibt."""
    limit = height * (1 - CHAT_BAND)
    if _lib.is_rect(current):
        gx, gy, gw, gh = (int(v) for v in current)
        if gy + gh <= limit + 0.5 and gy >= 0:
            return [gx, gy, gw, gh]
    keep = int(limit)
    keep -= keep % 2
    return [0, 0, int(width), max(2, keep)]


def segments(duration: float, moment: float, max_clip: float) -> list[dict[str, Any]]:
    duration = max(0.0, float(duration))
    moment = min(max(float(moment), 0.0), duration)
    cold_a = max(0.0, moment - 1.0)
    cold_b = min(duration, cold_a + 2.5)
    cold_a = max(0.0, cold_b - 2.5)
    cold_len = cold_b - cold_a
    use_cold = cold_len >= 0.4 and duration - cold_len >= 8
    budget = float(max_clip) - (cold_len if use_cold else 0)
    span = min(duration, max(8.0, budget))
    if duration <= span + 0.01:
        main_a, main_b = 0.0, duration
    else:
        main_a = max(0.0, moment - span * 0.45)
        main_b = min(duration, main_a + span)
        main_a = max(0.0, main_b - span)
    main = {"start": round(main_a, 3), "end": round(main_b, 3), "role": "payoff"}
    if not use_cold or abs(cold_len - (main_b - main_a)) < 0.05:
        return [main]
    return [
        {"start": round(cold_a, 3), "end": round(cold_b, 3), "role": "cold_open"},
        main,
    ]


def transcript_segments(work: Path) -> list[dict]:
    """Only use transcripts for the currently selected source sections."""
    sections_path = work / "sections.json"
    if not sections_path.is_file():
        return []
    found = []
    for section in _lib.read_json(sections_path).get("sections", []):
        sid = section.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"s[0-9]+-[0-9]+", sid):
            continue
        path = work / "transcripts" / f"{sid}.json"
        try:
            if not path.is_file():
                continue
            data = _lib.read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        records = data.get("segments") if isinstance(data, dict) else None
        if isinstance(records, list):
            found.extend(record for record in records if isinstance(record, dict))
    return found


def pick_quote(transcript: list[dict], main: dict, moment: float) -> str | None:
    """Use short, verbatim setup dialogue, not a guess at what the picture shows."""
    start = main["start"]
    end = min(main["end"], start + HOOK_CONTEXT_S, moment)
    choices = []
    for segment in transcript:
        a, b, text = (segment.get(key) for key in ("s", "e", "text"))
        if isinstance(a, bool) or isinstance(b, bool):
            continue
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and start <= a < b <= end):
            continue
        if not isinstance(text, str) or re.search(r"[\x00-\x1f]|https?://|www\.", text, re.I):
            continue
        for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
            quote = " ".join(sentence.split()).strip('„“”"').rstrip(".")
            if not all(ch.isalnum() or ch in " .,!?…:;’'„“”\"-–" for ch in quote):
                continue
            words = re.findall(r"[^\W\d_]+", quote.casefold())
            distinct = set(words)
            context = {word for word in distinct if len(word) >= 4} - FILLER
            if 3 <= len(words) <= 8 and len(distinct) >= 3 and len(quote) <= HOOK_MAX_CHARS and context:
                choices.append((-len(context), a, quote))
    return min(choices)[2] if choices else None


def captions(login: str, hook: str) -> dict[str, str]:
    credit = f"Clip: @{login} auf Twitch"
    lead = hook if hook.endswith(("!", "?", "…")) else f"{hook}."
    return {
        "youtube": f"{lead} {credit} #Shorts #gta6",
        "tiktok": f"{lead} {credit} #gta6 #{login} #gaming",
        "instagram": f"{lead} {credit} #gta6 #{login} #reels",
    }


def build_plan(meta: dict[str, Any], source: dict[str, Any], candidates: list[dict],
               settings: dict[str, Any], transcript: list[dict]) -> dict[str, Any]:
    duration = float(meta.get("duration_s") or 0)
    if duration < 8:
        raise ValueError(f"Clip ist {duration:.1f} s, unter 8 s")
    login = str(source.get("login") or meta.get("channel") or meta.get("streamer") or "").lstrip("@")
    if not login:
        raise ValueError("Broadcaster-Login fehlt")
    if candidates:
        moment = float(max(candidates, key=lambda c: c.get("score") or 0).get("moment_s") or duration / 2)
        cand_id = max(candidates, key=lambda c: c.get("score") or 0).get("id")
    else:
        moment = duration / 2
        cand_id = None
    cut = segments(duration, moment, float(settings.get("max_clip_s") or 60))
    quote = pick_quote(transcript, cut[-1], moment) if cand_id is not None else None
    hook = quote or f"{login} spielt GTA 6"
    title = f"{login} in GTA 6: „{quote}“" if quote else hook
    width, height = frame_size(meta)
    current = (settings.get("layout") or {}).get("game")
    return {
        "vod_id": meta.get("vod_id"),
        "cycle": "game",
        "clips": [{
            "id": "clip01",
            "candidate": cand_id,
            "segments": cut,
            "hook": hook,
            "title": f"{title} #Shorts",
            "captions": captions(login, hook),
            "commentary": "Gleich kippt die Runde.",
            "vo": "Gleich kippt die Runde.",
            "layout": "stack",
            "game": safe_game(width, height, current if isinstance(current, list) else None),
        }],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="clips.json für einen GTA-Clip schreiben.")
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _lib.setup("plan_game_clip", args.verbose)
    work = _lib.work_dir(args.vod_id)
    meta_path = work / "meta.json"
    if not meta_path.is_file():
        _lib.fail(f"{meta_path} fehlt", _lib.EXIT_INVALID)
    meta = _lib.read_json(meta_path)
    meta.setdefault("vod_id", args.vod_id)
    source = _lib.read_json(work / "source.json") if (work / "source.json").is_file() else {}
    cands = _lib.read_json(work / "candidates.json") if (work / "candidates.json").is_file() else {}
    cfg, warnings = _lib.load_config()
    settings = _lib.streamer_settings(cfg, meta.get("streamer") or source.get("login"))
    try:
        plan = build_plan(meta, source, cands.get("candidates") or [], settings, transcript_segments(work))
    except ValueError as exc:
        _lib.fail(str(exc), _lib.EXIT_INVALID)
    if args.dry_run:
        _lib.emit({"dry_run": True, "vod_id": args.vod_id, "hook": plan["clips"][0]["hook"],
                   "game": plan["clips"][0]["game"], "warnings": warnings})
    (work / "clips.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _lib.emit({"vod_id": args.vod_id, "file": str(work / "clips.json"),
               "hook": plan["clips"][0]["hook"], "game": plan["clips"][0]["game"], "warnings": warnings})


if __name__ == "__main__":
    main()
