"""Opening hook: compact white card, rounded corners, centered bold black type."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

from caption_emoji import word_emoji

if TYPE_CHECKING:
    from collections.abc import Iterable

    from PIL.Image import Image as CardImage
    from PIL.ImageFont import FreeTypeFont, ImageFont as BitmapFont

TOP = 520  # Keep the hook near the facecam/gameplay seam in the 1080×1920 layout.
MAX_WIDTH = 900
SCALE = 2
PAD_X, PAD_Y, RADIUS = 40, 22, 24
EMOJI = re.compile(
    r"[0-9#*]\ufe0f?\u20e3|[\U0001f1e6-\U0001f1ff]{2}|"
    r"[\U0001f300-\U0001faff\u2600-\u27bf]\ufe0f?[\U0001f3fb-\U0001f3ff]?"
    r"(?:\u200d[\U0001f300-\U0001faff\u2600-\u27bf]\ufe0f?[\U0001f3fb-\U0001f3ff]?)*"
)


def title_parts(hook: str, *, emojis: bool = True) -> tuple[str, str | None]:
    literal = EMOJI.search(hook) if emojis else None
    text = " ".join(EMOJI.sub(" ", hook).split())
    text = re.sub(r" +([.,!?;:])", r"\1", text)
    if not emojis or not text:
        return text, None
    if literal:
        return text, literal.group()
    # Prefer concrete visual motifs, not generic mappings such as 'schaut' → 👀.
    concrete = {"🔥", "🚨", "🚗", "💵", "📱", "💥", "🗺️"}
    glyph = next((g for token in text.split() if (g := word_emoji(token)) in concrete), None)
    return text, glyph


def _covers_text(font: FreeTypeFont | BitmapFont, text: str) -> bool:
    def glyph(char: str) -> tuple[tuple[int, int], bytes]:
        mask = font.getmask(char, mode="L")
        return mask.size, bytes(cast("Iterable[int]", mask))

    # Pillow draws the face's .notdef glyph instead of doing system font fallback.
    # Compare actual masks against noncharacters; missing-glyph widths vary by font.
    missing = {glyph("\ufdd0"), glyph("\U0010ffff")}
    return all(glyph(char) not in missing for char in set(text) if not char.isspace())


def _card_font_path(text: str, size: int) -> Path | None:
    from PIL import ImageFont

    paths = [p for p in (
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ) if p.is_file()]
    for path in paths:
        try:
            font = ImageFont.truetype(str(path), size)
        except OSError:
            continue
        if _covers_text(font, text):
            return path
    if not paths and _covers_text(ImageFont.load_default(size), text):
        return None
    # Let libass choose system fallback fonts rather than baking tofu into a PNG.
    raise ValueError("no opening card font covers all hook glyphs")


def make_card(text: str, emoji: CardImage | None = None) -> CardImage:
    from PIL import Image, ImageDraw, ImageFont

    words = text.split()
    if not words:
        raise ValueError("opening card needs text")
    max_size = 68
    font_path = _card_font_path(text, max_size * SCALE)
    # Use at most two lines, and shrink instead of overflowing on long hooks.
    for size in range(max_size, 0, -2):
        font = ImageFont.truetype(str(font_path), size * SCALE) if font_path else ImageFont.load_default(size * SCALE)
        stroke = 0 if font_path else max(1, round(size * SCALE * .027))
        icon_size = round(size * SCALE * .82) if emoji is not None else 0
        icon_gap = round(size * SCALE * .18) if emoji is not None else 0
        spare = icon_size + icon_gap
        layouts = [[text]] + [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))]
        fitting = []
        for lines in layouts:
            bounds = [font.getbbox(line, stroke_width=stroke) for line in lines]
            widths = [r - l for l, _, r, _ in bounds]
            widths[-1] += spare
            if max(widths) <= (MAX_WIDTH - 2 * PAD_X) * SCALE:
                fitting.append((len(lines), abs(widths[0] - widths[-1]), lines, bounds, widths))
        if fitting:
            _, _, lines, bounds, widths = min(fitting, key=lambda option: option[:2])
            break
    else:
        raise ValueError("opening card text exceeds two lines")

    line_height = round(size * 1.12 * SCALE)
    width = max(widths) + 2 * PAD_X * SCALE
    height = len(lines) * line_height + 2 * PAD_Y * SCALE
    card = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=RADIUS * SCALE, fill="white")
    for i, (line, (left, top, _, bottom), line_width) in enumerate(zip(lines, bounds, widths)):
        x = (width - line_width) // 2
        mid = PAD_Y * SCALE + i * line_height + line_height // 2
        draw.text((x - left, mid - (top + bottom) // 2), line, font=font, fill="black", stroke_width=stroke)
        if i == len(lines) - 1 and emoji is not None:
            sprite = emoji.copy()
            sprite.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
            card.alpha_composite(sprite, (x + line_width - icon_size + (icon_size - sprite.width) // 2,
                                          mid - sprite.height // 2))
    return card.resize((round(width / SCALE), round(height / SCALE)), Image.Resampling.LANCZOS)
