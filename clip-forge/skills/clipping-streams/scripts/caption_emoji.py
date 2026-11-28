"""Emoji Pop-Out and Burst with Apple Color Emoji and a damped spring."""
from __future__ import annotations

import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image as Sprite

# Match explicit forms, not substrings of unrelated words.
WORDS = {word: glyph for glyph, forms in {
    "🔥": "feuer flamme flammen brennt brennen abgebrannt",
    "🚨": "polizei polizist polizisten polizistin cops blaulicht",
    "🚗": "auto autos wagen lieferwagen sportwagen car cars",
    "💵": "geld bargeld dollar euro euros geldschein money cash",
    "🎥": "video videos kamera kameras camera cameras",
    "👀": "schau schaut guck guckt watch watching",
    "😂": "lacht lachen gelacht funny laugh laughing",
    "📱": "handy smartphone telefon phone",
    "💥": "explosion explodiert crash unfall",
    "🗺️": "karte landkarte map",
}.items() for word in forms.split()}

BASE_W, BASE_H = 1080, 1920
CAPTION_Y = 1180
LINE_H = 100
PEAK_SIZE = 110
LIFT = 42
DRIFT_X = 56
GAP = 7
# Include spring overshoot in the overlay bounds.
SPRITE_LIMIT = math.ceil(PEAK_SIZE * 1.10)
BAND_WIDTH = 1000
BAND_TOP = CAPTION_Y - GAP - LIFT - SPRITE_LIMIT - 4
BAND_BOTTOM = CAPTION_Y + 2 * LINE_H + GAP + LIFT + SPRITE_LIMIT + 4
SHUTTER_FRAMES = .8
SHUTTER_SAMPLES = 5

# Local left, top, width, height and 8-bit opacity.
Pose = tuple[int, int, int, int, int]


class UnsupportedFrameRate(ValueError):
    """The emoji animation needs a positive numeric frame rate."""


@dataclass(frozen=True)
class Cue:
    glyph: str
    start: float
    end: float
    x: int
    corner: int  # Bit 0: right. Bit 1: below.
    lines: int
    onset_visible: bool = False

    @property
    def side(self) -> str:
        return "below" if self.corner & 2 else "above"


def word_emoji(word: str) -> str | None:
    return WORDS.get(re.sub(r"^\W+|\W+$", "", word.casefold()))


def apple_ready() -> bool:
    return sys.platform == "darwin" and shutil.which("swift") is not None


def pop_out_cues(glyphs: list[str], start: float, end: float, text_lines: list[str], seed: str,
                 previous_corners: set[int], *, onset_visible: bool = False) -> list[Cue]:
    # A burst occupies both corners of a diagonal. Neither may repeat from the previous event.
    eligible = [corner for corner in range(4) if corner not in previous_corners
                and (len(glyphs) == 1 or (corner ^ 3) not in previous_corners)]
    first = random.Random(seed).choice(eligible)  # Stable on rerender.
    cues = []
    for i, glyph in enumerate(glyphs):
        corner = first if i == 0 else first ^ 3  # Burst splits across opposite corners.
        below, right = bool(corner & 2), bool(corner & 1)
        text = text_lines[-1] if below else text_lines[0]
        # Approximate Arial Black's rendered width; clamp wide lines to leave room for drift.
        advance = sum(21 if c in " !.,:;Iil'" else 62 if c in "WMwm" else 45 for c in text)
        edge = min(advance / 2, BAND_WIDTH / 2 - SPRITE_LIMIT / 2 - DRIFT_X - 4)
        x = round(BASE_W / 2 + edge * (1 if right else -1))
        cues.append(Cue(glyph, start, end, x, corner, len(text_lines), onset_visible))
    return cues


def smooth(t: float) -> float:
    t = min(t, 1.)
    return t * t * t * (10 + t * (-15 + 6 * t))


def motion(elapsed: float, duration: float, *, onset_visible: bool = False) -> tuple[float, float, float]:
    """Brief spring overshoot, independent outward drift, and a soft dissolve."""
    if not 0 <= elapsed < duration:
        return 0., 0., 0.
    t = elapsed / min(.30, duration * .72)
    # Step response of an underdamped second-order spring, starting at rest.
    damping, frequency = 7.2, 8.3
    spring = 1 - math.exp(-damping * t) * (math.cos(frequency * t) + damping / frequency * math.sin(frequency * t))
    fade_in = 1. if onset_visible else elapsed / min(.09, duration / 3)
    opacity = smooth(min(fade_in, (duration - elapsed) / min(.15, duration / 3)))
    travel = .78 * smooth(elapsed / min(.22, duration * .60)) + .22 * smooth(elapsed / duration)
    initial_scale = .52 if onset_visible else .24
    return initial_scale + (1 - initial_scale) * spring, opacity, travel


def overlay_geometry(rconf: dict[str, Any]) -> tuple[int, int, int, int]:
    sx, sy = int(rconf["width"]) / BASE_W, int(rconf["height"]) / BASE_H
    band_w = max(2, round(BAND_WIDTH * sx / 2) * 2)
    band_h = max(2, round((BAND_BOTTOM - BAND_TOP) * sy / 2) * 2)
    return band_w, band_h, (int(rconf["width"]) - band_w) // 2, round(BAND_TOP * sy)


def sprite_pose(cue: Cue, now: float, sx: float, sy: float, left: int, top: int,
                size: tuple[int, int]) -> Pose | None:
    scale, alpha, travel = motion(now - cue.start, cue.end - cue.start, onset_visible=cue.onset_visible)
    opacity = round(alpha * 255)
    if not opacity:
        return None
    factor = PEAK_SIZE * min(sx, sy) * scale / max(size)
    w, h = (max(1, round(n * factor)) for n in size)
    direction = 1 if cue.corner & 1 else -1
    x = round((cue.x + direction * DRIFT_X * travel) * sx - left - w / 2)
    if cue.side == "above":
        y = round((CAPTION_Y - GAP - LIFT * travel) * sy - top - h)
    else:
        y = round((CAPTION_Y + LINE_H * cue.lines + GAP + LIFT * travel) * sy - top)
    return x, y, w, h, opacity


def sample_sprite(sprite: Sprite, poses: list[Pose | None]) -> tuple[Sprite, tuple[int, int]] | None:
    """Average a short shutter exposure in premultiplied alpha, only around the sprite."""
    from PIL import Image

    visible = [p for p in poses if p is not None]
    if not visible:
        return None

    def scaled(w: int, h: int, opacity: int) -> Sprite:
        layer = sprite.resize((w, h), Image.Resampling.LANCZOS)
        if opacity < 255:
            layer.putalpha(layer.getchannel("A").point([round(a * opacity / 255) for a in range(256)]))
        return layer

    x, y, w, h, _ = visible[0]
    if all(p[:4] == (x, y, w, h) for p in visible):
        opacity = round(sum(p[4] for p in visible) / len(poses))
        return scaled(w, h, opacity), (x, y)

    left, top = min(p[0] for p in visible), min(p[1] for p in visible)
    right, bottom = max(p[0] + p[2] for p in visible), max(p[1] + p[3] for p in visible)
    clear = Image.new("RGBa", (right - left, bottom - top))
    exposure = clear
    for i, pose in enumerate(poses, 1):
        sample = clear
        if pose is not None:
            x, y, w, h, opacity = pose
            sample = clear.copy()
            sample.paste(scaled(w, h, opacity).convert("RGBa"), (x - left, y - top))
        exposure = Image.blend(exposure, sample, 1 / i)
    return exposure.convert("RGBA"), (left, top)


def load_apple_sprites(glyphs: list[str], assets: Path, scratch: Path) -> dict[str, Sprite]:
    """Rasterize Apple Color Emoji glyphs without substituting another font."""
    from PIL import Image

    sprites = {}
    with tempfile.TemporaryDirectory(dir=scratch, prefix="apple-emoji-") as tmp:
        subprocess.run(["swift", str(assets / "apple-emoji.swift"), tmp, *glyphs],
                       check=True, capture_output=True, timeout=45)
        for i, glyph in enumerate(glyphs):
            path = Path(tmp) / f"{i}.png"
            if not path.is_file():
                continue  # Swift skips glyphs not covered by the local Apple font.
            with Image.open(path) as source:
                rgba = source.convert("RGBA")
                bounds = rgba.getchannel("A").getbbox()
                if bounds:
                    sprites[glyph] = rgba.crop(bounds)
    return sprites


def render_overlay(ffmpeg: str, cues: list[Cue], rconf: dict[str, Any], output: Path, assets: Path,
                   *, sprites: dict[str, Sprite] | None = None) -> list[Cue]:
    """Rasterize only the transparent band, then encode it losslessly with alpha."""
    from PIL import Image

    width, height, left, top = overlay_geometry(rconf)
    sx, sy = int(rconf["width"]) / BASE_W, int(rconf["height"]) / BASE_H
    fps_arg = str(rconf["fps"])
    try:
        fps = float(Fraction(fps_arg))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise UnsupportedFrameRate(f"render.fps={fps_arg!r}") from exc
    if not 0 < fps < math.inf:
        raise UnsupportedFrameRate(f"render.fps={fps_arg!r}")
    # Trail behind the current pose; never show an emoji before the spoken keyword.
    offsets = [SHUTTER_FRAMES / fps * (1 - i / (SHUTTER_SAMPLES - 1)) for i in range(SHUTTER_SAMPLES)]
    cues = sorted(cues, key=lambda cue: cue.start)
    if sprites is None:
        sprites = load_apple_sprites(list(dict.fromkeys(c.glyph for c in cues)), assets, output.parent)
    cues = [c for c in cues if c.glyph in sprites]
    if not cues:
        return []
    blank = Image.new("RGBA", (width, height))
    empty_frame = blank.tobytes()
    frames = math.ceil(max(c.end for c in cues) * fps)
    first = 0
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{width}x{height}", "-r", fps_arg, "-i", "pipe:0", "-an",
           "-c:v", "qtrle", "-pix_fmt", "argb", str(output)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        assert proc.stdin is not None
        for frame_no in range(frames):
            now = frame_no / fps
            while first < len(cues) and cues[first].end <= now:
                first += 1
            frame = None
            for i in range(first, len(cues)):
                cue = cues[i]
                if cue.start > now + 1e-9:  # absorb floating-point round-off at frame boundaries
                    break
                if cue.end <= now:
                    continue
                sprite = sprites[cue.glyph]
                if cue.onset_visible and now - 1 / fps < cue.start - 1e-9:
                    # The first eligible frame uses the fully visible opening pose, even
                    # when a low frame rate places it in the cue's fade-out interval.
                    poses = [sprite_pose(cue, cue.start, sx, sy, left, top, sprite.size)]
                else:
                    poses = [sprite_pose(cue, now - offset, sx, sy, left, top, sprite.size) for offset in offsets]
                exposure = sample_sprite(sprite, poses)
                if exposure is None:
                    continue
                if frame is None:
                    frame = blank.copy()
                frame.alpha_composite(*exposure)
            proc.stdin.write(empty_frame if frame is None else frame.tobytes())
        proc.stdin.close()
        if proc.wait(timeout=15) != 0:
            raise OSError("ffmpeg could not encode the emoji overlay")
    return cues
