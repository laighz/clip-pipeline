# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "Pillow>=10.1", "text2num==3.1.0"]
# ///
"""Rendert Clips aus clips.json als 1080x1920-MP4 mit Wort-Untertiteln, Hook und loudnorm.

Pro Clip:
  - jedes Segment (VOD-Zeit) wird ein eigener ffmpeg-Input mit -ss/-t (genauer Schnitt,
    da ohnehin neu kodiert wird), Cold Open zuerst, dann concat
  - Layout: stack (Facecam oben, Spiel unten; braucht layout.facecam in streamers.yaml),
    blur (ganzes Bild mittig auf unscharfem Hintergrund) oder center (mittiger 9:16-Ausschnitt)
  - Untertitel: ASS mit aktiver Wortfarbe; auf macOS animierte Apple Emojis; weißer Hook-Titel am Anfang
  - Ton: loudnorm I=-16, 48 kHz AAC
  - Encoder: h264_amf (Windows) oder h264_videotoolbox (macOS) laut state/env.json, sonst libx264.
    Scheitert der Hardware-Encoder, automatisch libx264
Ausgabe: out/<vod>/<clip>.mp4 + <clip>.json (Metadaten) + <clip>.txt (Captions zum Kopieren).

Aufruf: render_clip.py --vod-id <id> [--clip clip01] [--encoder auto|amf|videotoolbox|x264] [--force] [--dry-run]
Erst validate_clips.py (Exit 0), dann --dry-run, dann echt.
Exit: 0 ok, 1 ffmpeg-Fehler, 2 Eingabe ungültig, 4 Programm fehlt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import subprocess
import tempfile
import time
import zlib
from contextlib import suppress
from itertools import groupby
from pathlib import Path
from typing import Any

import _lib
import caption_emoji
import caption_emoji_plan
import caption_numbers
import emoji_catalog
import hook_card


# ------------------------------------------------------------------ ASS
def ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_text(text: str) -> str:
    """Override-Zeichen entschärfen: { } und Backslash würden ASS-Befehle auslösen."""
    return re.sub(r"[{}\\]", "", text).replace("\n", " ").strip()


PHRASE_COLORS = ("0000E5FF", "0060EF72", "007A62FF")  # yellow, green, coral (ASS BGR)
PHRASE_WIDTH = 18
ATTACH_RIGHT = frozenset((
    "am an auf aus bei bis durch für gegen hinter im in ins mit nach neben ohne seit über um unter "
    "vom von vor während wegen zu zum zur der die das den dem des ein eine einem einen einer eines "
    "kein keine keinem keinen keiner keines diesem diesen diese dieser dieses mein meine dein deine "
    "und oder sowie the a at from into of on over to under with without and or"
).split())


def break_penalty(left: str) -> int:
    """Prefer not to leave a preposition, determiner or connector dangling."""
    if left.endswith((",", ":", ";")):
        return 0
    return int(re.sub(r"^\W+|\W+$", "", left.casefold()) in ATTACH_RIGHT)


def line_break(texts: list[str]) -> int:
    """Zero means one line, a positive index splits two lines, -1 is too wide."""
    if len(" ".join(texts)) <= PHRASE_WIDTH:
        return 0
    options = []
    for i in range(1, len(texts)):
        a, b = len(" ".join(texts[:i])), len(" ".join(texts[i:]))
        if max(a, b) <= PHRASE_WIDTH:
            options.append((break_penalty(texts[i - 1]), abs(a - b), i))
    return min(options)[2] if options else -1


def group_subtitles(words: list[dict], limit: int, two_lines: bool) -> list[list[dict]]:
    """Balance caption groups within sentences without relaxing display limits."""
    groups: list[list[dict]] = []
    limit = max(1, limit)

    def append_sentence(start: int, end: int) -> None:
        texts = [ass_text(w["w"].upper()) for w in words[start:end]]
        n = len(texts)
        costs = [(0, 0, 0)] * (n + 1)  # dangling at group boundaries, groups, dangling within groups
        next_breaks = [n] * n
        for i in range(n - 1, -1, -1):
            options = []
            for j in range(i + 1, min(n, i + limit) + 1):
                split = line_break(texts[i:j]) if two_lines else 0
                if split < 0 and j > i + 1:
                    break
                dangling, count, wraps = costs[j]
                options.append((dangling + (break_penalty(texts[j - 1]) if j < n else 0),
                                count + 1, wraps + (break_penalty(texts[i + split - 1]) if split > 0 else 0), -j))
            dangling, count, wraps, minus_j = min(options)
            costs[i] = (dangling, count, wraps)
            next_breaks[i] = -minus_j
        i = 0
        while i < n:
            j = next_breaks[i]
            groups.append(words[start + i:start + j])
            i = j

    start = 0
    for i, w in enumerate(words, 1):
        if (i == len(words) or w.get("caption_sentence_end", w["w"].rstrip().endswith(caption_numbers.SENTENCE_END))
                or words[i]["s"] - w["e"] > caption_numbers.PAUSE_S):
            append_sentence(start, i)
            start = i
    return groups


def build_ass(clip: dict[str, Any], words: list[dict], rconf: dict[str, Any],
              credit: str | None, *, emoji_cues: list[caption_emoji.Cue] | None = None,
              burn_hook: bool = True, language: str = "de",
              candidate_glyphs: set[str] | None = None,
              unavailable_glyphs: set[str] | None = None,
              duck_until: float = 0.) -> tuple[str, float, int]:
    template = (_lib.ASSETS_DIR / "caption-style.ass").read_text(encoding="utf-8")
    events: list[str] = []
    out_t = 0.0
    n_words = 0
    styled = emoji_cues is not None and rconf.get("caption_emojis", True)
    per = int(rconf.get("emoji_words_per_caption", 5) if styled else rconf.get("words_per_caption", 3))
    upper = bool(rconf.get("caption_uppercase", styled))

    phrase = 0
    previous_corners: set[int] = set()
    last_shown: dict[str, float] = {}
    last_review_cue = -math.inf
    legacy = (caption_emoji_plan.FIELD not in clip and rconf.get("caption_emoji_mode") == "legacy")
    reviewed = {}
    if styled and caption_emoji_plan.FIELD in clip and clip[caption_emoji_plan.FIELD] != []:
        try:
            reviewed = caption_emoji_plan.validate_markers(clip[caption_emoji_plan.FIELD],
                                                           caption_emoji_plan.word_context(clip, words))
        except (ValueError, OSError, EOFError, KeyError, zlib.error) as exc:
            logging.getLogger(__name__).warning("Emoji-Review wird ignoriert: %s", exc)
    for seg_i, seg in enumerate(clip["segments"]):
        a, b = float(seg["start"]), float(seg["end"])
        seg_words = [w for w in words if a - .05 <= w["s"] < b and a < w["e"] <= b + .05]
        if out_t < duck_until:
            seg_words = [w for w in seg_words if out_t + max(a, w["s"]) - a >= duck_until]
        visible_starts = {w["s"] for w in seg_words} if seg_i in reviewed else set()
        n_words += len(seg_words)
        display = [w for lang, run in groupby(seg_words, key=lambda w: w.get("transcript_language") or language)
                   for w in caption_numbers.display_words(list(run), lang, bounds=(a, b))]
        groups = group_subtitles(display, per, styled)
        hits = dict(emoji_catalog.hits(display, caption_emoji.WORDS, bounds=(a, b), default_language=language,
                                       catalog_enabled=rconf.get("caption_emoji_catalog", True),
                                       unavailable=unavailable_glyphs, candidates=candidate_glyphs)) if styled and legacy else {}
        group_offset = 0
        for gi, group in enumerate(groups):
            texts = [ass_text(w["w"].upper() if upper else w["w"]) for w in group]
            next_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
            group_end = min(b, group[-1]["e"] + .6, next_start if next_start is not None else b)
            split = line_break([t.upper() for t in texts]) if styled else 0
            color = PHRASE_COLORS[phrase % len(PHRASE_COLORS)] if styled else PHRASE_COLORS[0]
            style = "PhraseCaption" if styled else "Caption"
            matches: list[tuple[str, float, float]] = []
            if styled and split >= 0 and legacy:
                for wi, w in enumerate(group):
                    glyph = hits.get(group_offset + wi)
                    start = out_t + max(a, w["s"]) - a
                    end = min(start + .78, out_t + group_end - a)
                    if glyph and end - start >= .12 and start - last_shown.get(glyph, -10) >= 2.5:
                        matches.append((glyph, start, end))
                        last_shown[glyph] = start
                    if len(matches) == 2:
                        break
            elif styled and split >= 0 and visible_starts:
                for source_start, glyph in reviewed.get(seg_i, ()):
                    if source_start not in visible_starts or not group[0]["s"] <= source_start < group_end:
                        continue
                    start = out_t + source_start - a
                    end = min(start + .78, out_t + group_end - a)
                    if end - start < .12:
                        continue
                    if candidate_glyphs is not None:
                        candidate_glyphs.add(glyph)
                    if (glyph in (unavailable_glyphs or ()) or start - last_review_cue < caption_emoji_plan.MIN_GAP
                            or start - last_shown.get(glyph, -math.inf) < 2.5):
                        continue
                    matches.append((glyph, start, end))
                    last_review_cue = last_shown[glyph] = start
            if emoji_cues is not None and matches:
                lines = [" ".join(texts[:split]), " ".join(texts[split:])] if split > 0 else [" ".join(texts)]
                seed = f"{clip.get('id')}:{a}:{phrase}:{' '.join(texts)}"
                # Preserve the legacy burst, but never delay an explicit word anchor.
                for event in ([matches] if legacy else [[match] for match in matches]):
                    start, end = event[-1][1:]
                    cues = caption_emoji.pop_out_cues([m[0] for m in event], start, end, lines, seed, previous_corners)
                    emoji_cues.extend(cues)
                    previous_corners = {cue.corner for cue in cues}
            for wi, w in enumerate(group):
                start = w["s"]
                if wi + 1 < len(group):
                    end = group[wi + 1]["s"]
                else:   # letztes Wort der Gruppe: bis zur nächsten Gruppe stehen lassen (max. 0,6 s Lücke)
                    end = min(next_start, w["e"] + 0.6) if next_start else min(b, w["e"] + 0.6)
                start, end = max(start, a), min(end, b)
                if end - start < 0.04:
                    continue
                parts = [(f"{{\\c&H{color}&}}" + t + "{\\c&H00FFFFFF&}") if k == wi else t
                         for k, t in enumerate(texts)]
                caption = r"\N".join((" ".join(parts[:split]), " ".join(parts[split:]))) if split > 0 else " ".join(parts)
                events.append(f"Dialogue: 0,{ass_time(out_t + start - a)},{ass_time(out_t + end - a)},"
                              f"{style},,0,0,0,,{caption}")
            group_offset += len(group)
            phrase += 1
        out_t += b - a
    hook_s = min(float(rconf.get("hook_duration_s", 4.0)), out_t)
    if burn_hook and hook_s > 0:
        text, _ = hook_card.title_parts(clip.get("hook") or "", emojis=False)
        if text:
            events.insert(0, f"Dialogue: 1,{ass_time(0)},{ass_time(hook_s)},Hook,,0,0,0,,{ass_text(text)}")
    if credit and rconf.get("burn_credit"):
        events.append(f"Dialogue: 1,{ass_time(0)},{ass_time(out_t)},Credit,,0,0,0,,{ass_text(credit)}")
    return template.rstrip() + "\n" + "\n".join(events) + "\n", out_t, n_words


# ------------------------------------------------------------------ Filter
def layout_filter(idx: int, layout: str, rconf: dict[str, Any], facecam: list[int] | None,
                  game: list[int] | None) -> str:
    W, H, fps = int(rconf["width"]), int(rconf["height"]), rconf["fps"]
    # Twitch-VODs kommen teils als Full-Range (yuvj420p). scale konvertiert nach
    # TV-Range, sonst bleibt das Tag erhalten und verify_clip schlaegt fehl.
    tail = f"setsar=1,fps={fps},scale=in_range=auto:out_range=tv,format=yuv420p[v{idx}]"
    if layout == "stack" and facecam:
        fh = int(rconf["facecam_height"])
        gh = H - fh
        fx, fy, fw, fhh = facecam
        cam = f"crop={fw}:{fhh}:{fx}:{fy},scale={W}:{fh}:force_original_aspect_ratio=increase,crop={W}:{fh}"
        if game:
            gx, gy, gw, ghh = game
            gam = f"crop={gw}:{ghh}:{gx}:{gy},scale={W}:{gh}:force_original_aspect_ratio=increase,crop={W}:{gh}"
        else:
            gam = f"scale={W}:{gh}:force_original_aspect_ratio=increase,crop={W}:{gh}"
        return (f"[{idx}:v]split=2[cam{idx}][game{idx}];[cam{idx}]{cam}[c{idx}];[game{idx}]{gam}[g{idx}];"
                f"[c{idx}][g{idx}]vstack=inputs=2,{tail}")
    if layout == "center":
        return f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{tail}"
    # blur: Hintergrund klein weichzeichnen (schnell), dann hochskalieren
    return (f"[{idx}:v]split=2[bg{idx}][fg{idx}];"
            f"[bg{idx}]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
            f"boxblur=12:2,scale={W}:{H},eq=brightness=-0.08[b{idx}];"
            f"[fg{idx}]scale={W}:-2[f{idx}];[b{idx}][f{idx}]overlay=(W-w)/2:(H-h)/2,{tail}")


def encoder_args(name: str, rconf: dict[str, Any]) -> list[str]:
    if name == "amf":
        a = rconf.get("amf") or {}
        return ["-c:v", "h264_amf", "-quality", str(a.get("quality", "quality")), "-rc", str(a.get("rc", "qvbr")),
                "-qvbr_quality_level", str(a.get("qvbr_quality_level", 22)), "-b:v", str(a.get("b", "10M")),
                "-maxrate", str(a.get("maxrate", "16M"))]
    if name == "videotoolbox":
        v = rconf.get("videotoolbox") or {}
        return ["-c:v", "h264_videotoolbox", "-b:v", str(v.get("b", "8M")), "-allow_sw", "0"]
    x = rconf.get("x264") or {}
    return ["-c:v", "libx264", "-preset", str(x.get("preset", "veryfast")), "-crf", str(x.get("crf", 20)),
            "-profile:v", "high"]


def find_section(sections: list[dict], a: float, b: float) -> dict:
    for s in sections:
        if float(s["start"]) - 0.05 <= a and b <= float(s["end"]) + 0.05:
            return s
    raise ValueError(f"Segment {a}-{b} liegt in keinem Abschnitt")


def render_input_digest(clip, sections, words, rconf, layout, cam, game, language, credit):
    """Bind the reproducible picture, original captions and cut to their render sidecar."""
    inputs = []
    for seg in clip["segments"]:
        section = find_section(sections, float(seg["start"]), float(seg["end"]))
        path = Path(section["file"]).resolve(strict=True)
        st = path.stat()
        inputs.append((str(path), st.st_size, st.st_mtime_ns, section["file_offset"]))
    visual = {k: clip.get(k) for k in ("segments", "hook", "id")}
    # In legacy mode an explicit empty review disables keyword triggers; absence does not.
    if caption_emoji_plan.FIELD in clip and (clip[caption_emoji_plan.FIELD] or rconf.get("caption_emoji_mode") == "legacy"):
        visual[caption_emoji_plan.FIELD] = clip[caption_emoji_plan.FIELD]
    payload = json.dumps([visual, inputs, words, rconf, layout, cam, game, language, credit],
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_cmd(ffmpeg: str, clip: dict[str, Any], sections: list[dict], ass_path: Path, out: Path,
              layout: str, rconf: dict[str, Any], facecam: list[int] | None, game: list[int] | None,
              encoder: str, emoji_overlay: Path | None = None,
              hook_overlay: tuple[Path, float] | None = None) -> list[str]:
    inputs: list[str] = []
    graph: list[str] = []
    for i, seg in enumerate(clip["segments"]):
        a, b = float(seg["start"]), float(seg["end"])
        sec = find_section(sections, a, b)
        local = max(0.0, a - float(sec["file_offset"]))
        inputs += ["-ss", f"{local:.3f}", "-t", f"{b - a:.3f}", "-i", sec["file"]]
        graph.append(layout_filter(i, layout, rconf, facecam, game))
        graph.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    n = len(clip["segments"])
    if n > 1:
        graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
        vlab, alab = "vc", "ac"
    else:
        vlab, alab = "v0", "a0"
    if emoji_overlay is not None:
        inputs += ["-i", str(emoji_overlay)]
        _, _, x, y = caption_emoji.overlay_geometry(rconf)
        graph.append(f"[{vlab}][{n}:v]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0[with_emoji]")
        vlab = "with_emoji"
    if hook_overlay is not None:
        image, seconds = hook_overlay
        inputs += ["-i", str(image)]
        i = n + int(emoji_overlay is not None)
        width, height = int(rconf["width"]), int(rconf["height"])
        y = round(hook_card.TOP * height / caption_emoji.BASE_H)
        graph.append(f"[{i}:v]format=rgba,scale=max(2\\,round(iw*{width}/{caption_emoji.BASE_W})):-1[hook_card]")
        graph.append(f"[{vlab}][hook_card]overlay=x=(W-w)/2:y={y}:enable='lt(t,{seconds:.6f})'[with_hook]")
        vlab = "with_hook"
    graph.append(f"[{vlab}]subtitles=filename={_lib.escape_filter_path(ass_path)}[vout]")
    graph.append(f"[{alab}]loudnorm={rconf['loudnorm']},aresample=48000[aout]")
    return [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", *inputs,
            "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            *encoder_args(encoder, rconf), "-pix_fmt", "yuv420p", "-r", str(rconf["fps"]),
            "-c:a", "aac", "-b:a", str(rconf.get("audio_bitrate", "160k")), "-ar", "48000",
            "-movflags", "+faststart", str(out)]


def font_problems(stderr: str) -> list[str]:
    return [ln.strip() for ln in stderr.splitlines()
            if re.search(r"fontselect.*(failed|no font)|Glyph 0x[0-9A-Fa-f]+ not found|Error opening font", ln)]


def discard_emoji_overlay(path: Path) -> None:
    """Auxiliary file cleanup must not interrupt the video render."""
    with suppress(OSError):
        if not path.is_dir():
            path.unlink(missing_ok=True)


# ------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clips aus clips.json als 1080x1920-MP4 rendern (Untertitel, Hook, loudnorm).",
        epilog="Exit: 0 ok, 1 ffmpeg-Fehler, 2 Eingabe ungültig, 4 Programm fehlt.",
    )
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--clip", help="nur diese Clip-ID(s), kommagetrennt")
    parser.add_argument("--encoder", choices=["auto", "amf", "videotoolbox", "x264"],
                        help="überschreibt render.encoder")
    parser.add_argument("--force", action="store_true", help="vorhandene MP4 neu rendern")
    parser.add_argument("--dry-run", action="store_true", help="nur ffmpeg-Befehle zeigen, nichts schreiben")
    parser.add_argument("--reviewed-emoji-only", action="store_true",
                        help="auch bei legacy nur kontextgeprüfte Caption-Pop-outs zulassen")
    parser.add_argument("--voiceover-window", "--voiceover-captions", type=Path,
                        help="JSON mit start/end/duck_until; unterdrückt geduckte Original-Untertitel (ein Clip)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("render_clip", args.verbose)

    work = _lib.work_dir(args.vod_id)
    for name in ("clips.json", "sections.json"):
        if not (work / name).is_file():
            _lib.fail(f"{work / name} fehlt.", _lib.EXIT_INVALID)
    plan = _lib.read_json(work / "clips.json")
    sections = _lib.read_json(work / "sections.json")["sections"]
    meta = _lib.read_json(work / "meta.json") if (work / "meta.json").is_file() else {}
    cfg, warnings = _lib.load_config()
    st = _lib.streamer_settings(cfg, meta.get("streamer"))
    language = str(st.get("language") or "de")
    rconf = cfg["render"]
    if args.reviewed_emoji_only and rconf.get("caption_emoji_mode") == "legacy":
        rconf = {**rconf, "caption_emoji_mode": "reviewed"}
    use_apple_emoji = rconf.get("caption_emojis", True) and caption_emoji.apple_ready()
    layout_cfg = st.get("layout") or {}
    facecam = layout_cfg.get("facecam")
    facecam = facecam if isinstance(facecam, list) else None
    game = layout_cfg.get("game")
    if not isinstance(game, list):
        game = layout_cfg.get("gameplay")
    game = game if isinstance(game, list) else None
    credit = str(st.get("credit") or "").replace("{channel}", str(st.get("channel") or meta.get("channel") or ""))

    encoder = args.encoder or rconf.get("encoder", "auto")
    if encoder == "auto":
        env_file = _lib.state_dir() / "env.json"
        if env_file.is_file():
            encoder = _lib.read_json(env_file).get("encoder", {}).get("chosen", "x264")
        else:
            encoder = "x264"
            warnings.append("state/env.json fehlt (check_env.py laufen lassen), nutze libx264")

    clips = plan.get("clips", [])
    if args.clip:
        wanted = {c.strip() for c in args.clip.split(",")}
        clips = [c for c in clips if c.get("id") in wanted]
        if not clips:
            _lib.fail(f"Clip(s) {sorted(wanted)} nicht in clips.json", _lib.EXIT_INVALID)
    duck_until = 0.
    if args.voiceover_window:
        try:
            if len(clips) != 1:
                raise ValueError("Voice-over-Unterlagen benötigen genau einen Clip")
            data = _lib.read_json(args.voiceover_window)
            start, end, duck_until = (float(data[k]) for k in ("start", "end", "duck_until"))
            length = sum(float(s["end"]) - float(s["start"]) for s in clips[0]["segments"])
            if not all(map(math.isfinite, (start, end, duck_until))) or not 0 <= start < end <= duck_until < length:
                raise ValueError("ungültiger Voice-over-Zeitbereich")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            _lib.fail(f"Voice-over-Untertitel: {exc}", _lib.EXIT_INVALID)
    words = _lib.load_words(work, include_language=True)

    ffmpeg = _lib.require_tool("ffmpeg")
    odir = _lib.out_dir(args.vod_id)
    rdir = work / "render"
    results = []
    for clip in clips:
        cid = _lib.safe_id(clip["id"])
        # Inline lists have no revision binding. Only use a verified sidecar.
        clip = dict(clip)
        if caption_emoji_plan.FIELD in clip:
            clip[caption_emoji_plan.FIELD] = []
        if use_apple_emoji:
            try:
                emoji_review = _lib.read_json(caption_emoji_plan.review_path(work, cid))
                clip[caption_emoji_plan.FIELD] = caption_emoji_plan.receipt_markers(emoji_review, clip, words)
            except FileNotFoundError:
                pass
            except (ValueError, OSError, EOFError, KeyError, zlib.error) as exc:
                warnings.append(f"{cid}: Emoji-Review wird ignoriert: {exc}")
                clip[caption_emoji_plan.FIELD] = []
        out = odir / f"{cid}.mp4"
        part = odir / f"{cid}.part.mp4"   # erst hierhin rendern, nach Erfolg umbenennen
        sidecar_path = odir / f"{cid}.json"
        # Pro Clip darf ein eigenes Facecam-Rechteck stehen: Overlays wandern
        # innerhalb eines Streams (z. B. zwei Cams, die die Ecke tauschen).
        clip_facecam = clip.get("facecam")
        cam = clip_facecam if _lib.is_rect(clip_facecam) else facecam
        clip_game = clip.get("game")
        use_game = clip_game if _lib.is_rect(clip_game) else game
        layout = clip.get("layout") or rconf.get("layout", "auto")
        if layout == "auto":
            layout = "stack" if cam else "blur"
        if layout == "stack" and not cam:
            layout = "blur"
            warnings.append(f"{cid}: keine Facecam bekannt, nutze blur")
        ass_path = rdir / f"{cid}.ass"
        emoji_cues: list[caption_emoji.Cue] | None = [] if use_apple_emoji else None
        candidate_glyphs: set[str] = set()
        try:
            hook_s = float(rconf.get("hook_duration_s", 4.0))
            hook_text, hook_glyph = hook_card.title_parts(clip.get("hook") or "",
                                                        emojis=rconf.get("hook_emojis", True))
            use_hook_card = hook_s > 0 and bool(hook_text)
            ass, total, n_words = build_ass(clip, words, rconf, credit, emoji_cues=emoji_cues,
                                            burn_hook=not use_hook_card, language=language,
                                            candidate_glyphs=candidate_glyphs, duck_until=duck_until)
            overlay = rdir / f"{cid}.emoji.mov" if emoji_cues else None
            hook_s = min(hook_s, total)
            intended_hook = (rdir / f"{cid}.hook.png", hook_s) if use_hook_card else None
            cmd = build_cmd(ffmpeg, clip, sections, ass_path, part, layout, rconf, cam, use_game, encoder,
                            overlay, intended_hook)
        except (KeyError, TypeError, ValueError) as exc:
            _lib.fail(f"{cid}: clips.json passt nicht zu den Abschnitten ({exc}). Erst validate_clips.py.",
                      _lib.EXIT_INVALID)
        if args.dry_run:
            results.append({"id": cid, "layout": layout, "encoder": encoder, "duration_s": round(total, 2),
                            "words": n_words, "caption_emojis": len(emoji_cues or ()), "out": str(out), "ffmpeg": cmd})
            continue
        try:
            input_digest = render_input_digest(clip, sections, words, rconf, layout, cam, use_game, language, credit)
        except OSError as exc:
            _lib.fail(f"{cid}: Quellabschnitt nicht verfügbar: {exc}", _lib.EXIT_ERROR, clip=cid)
        # Keep existing unmixed cache keys; never reuse a render with the old VO text.
        cache_key = hashlib.sha256(json.dumps([2 if duck_until else 1, input_digest, use_apple_emoji, duck_until or None,
                                               args.reviewed_emoji_only]).encode("utf-8")).hexdigest()
        if out.is_file() and not args.force:
            try:
                prior = _lib.read_json(sidecar_path)
            except (OSError, ValueError):
                prior = None
            if isinstance(prior, dict) and prior.get("render_cache_key") == cache_key:
                results.append({"id": cid, "status": "schon da", "out": str(out)})
                continue
            if out.with_suffix(".vo-ready").exists() or out.with_suffix(".novo.mp4").exists():
                _lib.fail(f"{cid}: Render-Eingaben geändert; bestehender VO-Mix bleibt erhalten. Neue Revision anlegen.",
                          _lib.EXIT_INVALID, clip=cid)
            warnings.append(f"{cid}: Render-Cache passt nicht zu den aktuellen Eingaben; rendere neu")
        rdir.mkdir(parents=True, exist_ok=True)
        odir.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        hook_image = None
        if use_hook_card:
            sprite = None
            if hook_glyph and caption_emoji.apple_ready():
                try:
                    sprite = caption_emoji.load_apple_sprites([hook_glyph], _lib.ASSETS_DIR, rdir).get(hook_glyph)
                    if sprite is None:
                        warnings.append(f"{cid}: Hook-Emoji nicht verfügbar, Titelkarte bleibt ohne Emoji")
                except (OSError, ImportError, subprocess.SubprocessError):
                    warnings.append(f"{cid}: Hook-Emoji nicht verfügbar, Titelkarte bleibt ohne Emoji")
            try:
                card = hook_card.make_card(hook_text, sprite)
                with tempfile.NamedTemporaryFile(dir=rdir, prefix=f"{cid}.hook-", suffix=".png", delete=False) as f:
                    hook_image = Path(f.name)
                    card.save(f, format="PNG")
            except (OSError, ValueError, ImportError) as exc:
                warnings.append(f"{cid}: Titelkarte nicht verfügbar ({exc}), nutze ASS-Hook")
                if hook_image is not None:
                    discard_emoji_overlay(hook_image)
                    hook_image = None
                if emoji_cues is not None:
                    emoji_cues.clear()
                ass, total, n_words = build_ass(clip, words, rconf, credit, emoji_cues=emoji_cues,
                                                language=language, duck_until=duck_until)
        hook_overlay = (hook_image, hook_s) if hook_image is not None else None
        if overlay is not None and emoji_cues:
            try:
                if overlay.is_dir():
                    raise IsADirectoryError(f"{overlay} ist ein Verzeichnis")
                sprites = caption_emoji.load_apple_sprites(sorted(candidate_glyphs), _lib.ASSETS_DIR, rdir)
                missing = candidate_glyphs.difference(sprites)
                if missing:
                    warnings.append(f"{cid}: {len(missing)} Emoji-Motiv(e) von Apple Color Emoji nicht unterstützt")
                    emoji_cues = []
                    ass, total, n_words = build_ass(clip, words, rconf, credit, emoji_cues=emoji_cues,
                                                    burn_hook=hook_image is None, language=language,
                                                    unavailable_glyphs=missing, duck_until=duck_until)
                if emoji_cues:
                    caption_emoji.render_overlay(ffmpeg, emoji_cues, rconf, overlay, _lib.ASSETS_DIR, sprites=sprites)
                else:
                    overlay = None
            except (OSError, ImportError, subprocess.SubprocessError, caption_emoji.UnsupportedFrameRate) as exc:
                warnings.append(f"{cid}: Apple Emojis nicht verfügbar ({type(exc).__name__}: {exc}); "
                                "rendere mit Standarduntertiteln")
                discard_emoji_overlay(overlay)
                overlay = None
                emoji_cues = None
                ass, total, n_words = build_ass(clip, words, rconf, credit,
                                                burn_hook=hook_image is None, language=language, duck_until=duck_until)
        try:
            ass_path.write_text(ass, encoding="utf-8")
            used = encoder
            cmd = build_cmd(ffmpeg, clip, sections, ass_path, part, layout, rconf, cam, use_game, used,
                            overlay, hook_overlay)
            proc = _lib.run(cmd, timeout=1800)
            fallback = None
            if proc.returncode != 0 and encoder in ("amf", "videotoolbox"):
                fallback = (proc.stderr or "").strip()[-300:]
                log.warning("%s: %s scheiterte, versuche libx264", cid, encoder)
                used = "x264"
                cmd = build_cmd(ffmpeg, clip, sections, ass_path, part, layout, rconf, cam, use_game, used,
                                overlay, hook_overlay)
                proc = _lib.run(cmd, timeout=1800)
        finally:
            for auxiliary in (overlay, hook_image):
                if auxiliary is not None:
                    discard_emoji_overlay(auxiliary)
        if proc.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)   # halbe Datei weg, damit sie nie als fertig gilt
            _lib.fail(f"{cid}: ffmpeg fehlgeschlagen: {(proc.stderr or '').strip()[-800:]}", _lib.EXIT_ERROR,
                      clip=cid)
        with part.open('rb') as media:
            original_render_sha256 = hashlib.file_digest(media, 'sha256').hexdigest()
        part.replace(out)
        fonts = font_problems(proc.stderr or "")
        if fonts:
            warnings.append(f"{cid}: Schrift-Probleme in den Untertiteln: {fonts[:3]}")
        took = round(time.monotonic() - t0, 1)
        sidecar = {
            "vod_id": args.vod_id, "clip_id": cid, "file": str(out), "duration_s": round(total, 3),
            "segments": clip["segments"], "candidate": clip.get("candidate"), "layout": layout,
            "render_input_sha256": input_digest,
            "render_cache_key": cache_key,
            "original_render_sha256": original_render_sha256,
            "encoder": used, "amf_fallback_reason": fallback, "render_seconds": took,
            "hook": clip.get("hook"), "title": clip.get("title"), "captions": clip.get("captions"),
            "commentary": clip.get("commentary"), "credit": credit, "streamer": meta.get("streamer"),
            "source_url": meta.get("url"), "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if args.reviewed_emoji_only:
            sidecar["reviewed_emoji_only"] = True
        _lib.write_json(sidecar_path, sidecar)
        caps = clip.get("captions") or {}
        txt = [f"TITEL: {clip.get('title', '')}", ""]
        for p in ("youtube", "tiktok", "instagram"):
            txt += [f"--- {p.upper()} ---", str(caps.get(p, "")), ""]
        txt += ["--- KOMMENTAR-SKRIPT (Voice-over) ---", str(clip.get("commentary", ""))]
        (odir / f"{cid}.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
        log.info("%s: %.1f s Clip in %.1f s gerendert (%s, %s)", cid, total, took, used, layout)
        results.append({"id": cid, "status": "gerendert", "out": str(out), "encoder": used, "layout": layout,
                        "duration_s": round(total, 2), "words": n_words, "render_seconds": took,
                        "caption_emojis": len(emoji_cues or ()),
                        "amf_fallback": bool(fallback)})
    for w in warnings:
        log.warning(w)
    _lib.emit({"vod_id": args.vod_id, "dry_run": args.dry_run, "clips": results, "warnings": warnings})


if __name__ == "__main__":
    main()
