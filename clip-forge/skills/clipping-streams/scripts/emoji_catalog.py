"""Offline Unicode emoji catalog, safe exact names and caption phrase matching."""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import unicodedata
import zlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

ASSETS = Path(__file__).resolve().parents[1] / "assets"
WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")
WORD_EDGE = re.compile(r"^\W+|\W+$")
BREAK = re.compile(r"[.!?,;:…–—]\W*$")
LEADING_BREAK = re.compile(r"^\W*[.!?,;:…–—]")
MAX_WORDS = 6
# Only concrete labels are considered automatically. Generic tags, faces, flags,
# identity/medical symbols and technical components are available for lookup.
CONCRETE = frozenset({"Animals & Nature", "Food & Drink", "Travel & Places", "Activities", "Objects"})
EXCLUDED = frozenset({"medical", "place-religious", "person-sport", "currency"})
CURATED = {
    "🔥": "flammenwerfer|feuerball|lagerfeuer|flamethrower|on fire",
    "🚨": "blaulichtfahrzeug|polizeiwagen|polizeikontrolle|fahndungslevel|police chase",
    "🚗": "mietwagen|kofferraum|fluchtwagen|rennwagen|getaway car",
    "💵": "geldbündel|scheine zählen|cash auszahlen|jackpot payout|cash prize",
    "🎥": "filmkamera|kameraperspektive|drehbeginn|behind the scenes|camera angle",
    "👀": "schau genau hin|siehst du das|guck mal da hinten|look closely|did you see that",
    "😂": "lachflash|lachkrampf|ich lach mich kaputt|can't stop laughing|burst out laughing",
    "📱": "handydisplay|sprachnachricht|handykamera|mobile app|phone screen",
    "💥": "zusammenstoß|blechschaden|auffahrunfall|head-on collision|car wreck",
    "🗺️": "weltkarte|stadtplan|wegpunkt|minimap|map marker",
    "😱": "jumpscare|jump scare",
    "🤯": "mir fliegt der kopf weg|mind blown",
    "🏆": "siegerpokal|winner's trophy",
    "🎮": "gamepad|spielcontroller|game controller",
    "🔇": "mikro stumm|microphone muted",
    "🎤": "gesangsmikrofon|handmikrofon|handheld microphone",
    "🎧": "kopfhörer|headphones",
    "🚧": "straßensperre|baustellenabsperrung|road closure",
    "🧯": "feuerlöscher|fire extinguisher",
    "🦫": "biber|beaver",
    "🦈": "hai gesichtet|shark spotted",
    "🍕": "pizzakarton|pizza box",
    "🧩": "puzzleteil|puzzle piece",
    "🕹️": "joystick|arcade stick",
}


def folded(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().replace("’", "'")


def token(text: str) -> str:
    return WORD_EDGE.sub("", folded(text))


def phrase(text: str) -> tuple[str, ...]:
    parts = tuple(folded(text).split())
    return parts if all(WORD.fullmatch(w) for w in parts) else ()


@lru_cache(maxsize=1)
def entries() -> list[dict[str, Any]]:
    with gzip.open(ASSETS / "emoji-catalog.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)["emojis"]


@lru_cache(maxsize=1)
def exact_names() -> dict[tuple[str, ...], str]:
    labels: dict[tuple[str, ...], set[str]] = defaultdict(set)
    allowed = set()
    for item in entries():
        if item["unicode_status"] != "fully-qualified" or item["is_skin_variant"]:
            continue
        glyph = item["emoji"]
        if item["category"] in CONCRETE and item["subcategory"] not in EXCLUDED:
            allowed.add(glyph)
        for name in (item["name_de"], item["emojipedia_name"]):
            p = phrase(name)
            if 2 <= len(p) <= MAX_WORDS:
                labels[p].add(glyph)
    return {p: glyph for p, gs in labels.items() if len(gs) == 1 and (glyph := next(iter(gs))) in allowed}


@lru_cache(maxsize=2)
def trigger_index(catalog_enabled: bool) -> dict[tuple[str, ...], str]:
    mapping = {}
    if catalog_enabled:
        try:
            mapping.update(exact_names())
        except (OSError, ValueError, EOFError, KeyError, TypeError, AttributeError, zlib.error) as exc:
            # A missing optional asset must not prevent normal subtitle rendering.
            logging.getLogger(__name__).warning("Emoji-Katalog nicht lesbar; nur kuratierte Auslöser: %s", exc)
    mapping.update({phrase(name): glyph for glyph, names in CURATED.items() for name in names.split("|")})
    return mapping


def hits(words: list[dict], legacy: dict[str, str], *, catalog_enabled: bool,
         bounds: tuple[float, float], default_language: str, unavailable: set[str] | None = None,
         candidates: set[str] | None = None) -> Iterator[tuple[int, str]]:
    """Yield (last word index, glyph), longest exact phrase first; no cut/pause joins."""
    index = trigger_index(catalog_enabled)
    tokens = [token(w["w"]) for w in words]
    left, right = bounds
    consumed_until = 0
    for i in range(len(words)):
        if i < consumed_until and candidates is None:
            continue
        end = i + 1
        while end < min(i + MAX_WORDS, len(words)):
            a, b = words[end - 1], words[end]
            if (a.get("caption_sentence_end") or BREAK.search(a["w"]) or LEADING_BREAK.match(b["w"])
                    or a["s"] < left or b["e"] > right or b["s"] - a["e"] > .55
                    or (a.get("transcript_language") or default_language)
                    != (b.get("transcript_language") or default_language)):
                break
            end += 1
        # Preflight includes alternatives that a longer phrase would hide, and
        # includes words inside that phrase in case it can't be displayed.
        for limit in range(end, i, -1):
            glyph = index.get(tuple(tokens[i:limit]))
            choices = (glyph, legacy.get(tokens[i])) if limit == i + 1 else (glyph,)
            for choice in choices:
                if not choice:
                    continue
                if candidates is not None:
                    candidates.add(choice)
                if i >= consumed_until and (unavailable is None or choice not in unavailable):
                    consumed_until = limit
                    yield limit - 1, choice
            if i < consumed_until and candidates is None:
                break


def search(query: str) -> list[dict[str, Any]]:
    q = folded(query).strip()
    if not q:
        return []
    ranked = []
    for row in entries():
        fields = [folded(w) for w in (row["emojipedia_name"], row["name_de"])]
        keys = {folded(w) for w in row["suggestions_de"] + row["suggestions_en"]}
        if q.replace("\ufe0f", "") == row["emoji"].replace("\ufe0f", ""):
            rank = 0
        elif q in fields:
            rank = 1
        elif q in keys:
            rank = 2
        elif any(q in s for s in fields):
            rank = 3
        else:
            continue
        ranked.append((rank, row["is_skin_variant"], len(fields[1]), row))
    return [r[-1] for r in sorted(ranked, key=lambda r: r[:3])]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Emoji-Katalog nach Name, Stichwort oder Emoji durchsuchen")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    print(json.dumps(search(args.query)[:args.limit], ensure_ascii=False))
