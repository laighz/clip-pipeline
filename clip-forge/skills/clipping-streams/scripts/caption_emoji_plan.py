"""Validate a context review against the actual edit and original ASR tokens."""
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import emoji_catalog

FIELD = "emoji_popouts"
REVIEW_DIR = "emoji-reviews"
MAX_MARKERS = 3
MIN_GAP = 1.5


@lru_cache(maxsize=1)
def approved_glyphs() -> frozenset[str]:
    try:
        rows = emoji_catalog.entries()
    except TypeError as exc:
        raise ValueError("ungültige Emoji-Katalogstruktur") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("ungültige Emoji-Katalogstruktur")
    return frozenset(row["emoji"] for row in rows if isinstance(row.get("emoji"), str)
                     and row.get("unicode_status") == "fully-qualified" and not row.get("is_skin_variant"))


def review_path(work: Path, clip_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", clip_id):
        raise ValueError("ungültige Clip-ID für Emoji-Review")
    return work / REVIEW_DIR / f"{clip_id}.json"


def revision(clip: dict, words: list[dict]) -> str:
    # Include words visible at the cut edges, not just words eligible for a pop-out.
    segments = [(float(s["start"]), float(s["end"])) for s in clip["segments"]]
    transcript = [(w["w"], w["s"], w["e"], w.get("transcript_language")) for w in words
                  if any(a - .05 <= w["s"] < b and a < w["e"] <= b + .05 for a, b in segments)]
    edit = {key: clip.get(key) for key in ("id", "segments", "hook", "facecam", "game", "layout", "vo")}
    payload = json.dumps([1, edit, transcript], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def receipt_markers(receipt: Any, clip: dict, words: list[dict]) -> list:
    if not isinstance(receipt, dict) or receipt.keys() != {"revision", FIELD}:
        raise ValueError("ungültiger Emoji-Review-Beleg")
    if receipt["revision"] != revision(clip, words):
        raise ValueError("Schnitt oder Transkript seit dem Emoji-Review geändert")
    markers = receipt[FIELD]
    validate_markers(markers, word_context(clip, words) if markers else [])
    return markers


def word_context(clip: dict, words: list[dict]) -> list[dict]:
    """Build occurrence IDs in playback order, including repeated source spans."""
    result: list[dict] = []
    offset = 0.
    for i, segment in enumerate(clip["segments"]):
        a, b = float(segment["start"]), float(segment["end"])
        for w in words:
            if a <= w["s"] < w["e"] <= b + .05 and w["s"] <= b - .12:
                result.append({"id": len(result), "segment": i, "start": w["s"], "word": w["w"],
                               "clip_time": offset + w["s"] - a})
        offset += b - a
    return result


def validate_markers(value: Any, context: list[dict]) -> dict[int, list[tuple[float, str]]]:
    """Return sorted source starts by segment or reject the entire malformed review."""
    if type(value) is not list or len(value) > MAX_MARKERS:
        raise ValueError(f"{FIELD}: erwartet eine Liste mit 0–{MAX_MARKERS} Markern")
    if not value:
        return {}
    available = Counter((row["segment"], row["start"], row["word"]) for row in context)
    by_segment = defaultdict(list)
    seen = set()
    glyphs = approved_glyphs()
    for row in value:
        if type(row) is not dict or row.keys() != {"segment", "start", "word", "emoji"}:
            raise ValueError(f"{FIELD}: ungültige Markerfelder")
        segment, start, word, glyph = (row[k] for k in ("segment", "start", "word", "emoji"))
        if (type(segment) is not int or type(start) not in (int, float) or type(word) is not str
                or type(glyph) is not str or glyph not in glyphs):
            raise ValueError(f"{FIELD}: ungültiger Wortanker oder unbekanntes Emoji")
        if available[segment, start, word] != 1 or (segment, start) in seen:
            raise ValueError(f"{FIELD}: Wortanker fehlt, ist mehrdeutig oder doppelt")
        seen.add((segment, start))
        by_segment[segment].append((start, glyph))
    return {segment: sorted(markers) for segment, markers in by_segment.items()}
