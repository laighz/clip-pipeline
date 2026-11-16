# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Prüft work/<vod>/clips.json (den Schnittplan), bevor gerendert wird.

Fehler (Exit 2, erst korrigieren, dann erneut prüfen):
  - JSON/Felder kaputt, doppelte oder unsichere Clip-IDs
  - Segment ausserhalb des VODs oder nicht durch einen geladenen Abschnitt gedeckt
  - Gesamtlänge unter 8 s oder über max_clip_s
  - Cold Open nicht an erster Stelle oder länger als 3 s
  - Überlappung zwischen Clips oder innerhalb eines Clips (Cold Open ausgenommen)
  - Hook, Titel oder eine der drei Captions fehlt / zu lang
Warnungen (Rendern geht trotzdem):
  - Schnitt mitten in einem Wort, keine Wörter im Clip, stummer Abschnitt,
    Kategorie mit Musik-Risiko, Credit-Zeile fehlt oder steht hinter den Hashtags,
    kein #Shorts für YouTube, kein Kommentar-Skript, stack ohne bekannte Facecam,
    Clip kürzer als min_clip_s (Moment ist vorbei: nicht mit Stille auffüllen).

Aufruf: validate_clips.py --vod-id <id> [--file clips.json]
Exit: 0 gültig (Warnungen möglich), 2 Fehler gefunden.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import _lib

COLD_OPEN_MAX_S = 3.0
HARD_MIN_S = 8.0
PLATFORMS = ("youtube", "tiktok", "instagram")
ID_RE = re.compile(r"[a-z0-9_-]{1,40}")


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schnittplan clips.json prüfen (Zeiten, Längen, Überlappungen, Texte).",
        epilog="Exit: 0 gültig (Warnungen möglich), 2 Fehler. Erst bei Exit 0 rendern.",
    )
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--file", type=Path, help="Pfad zur clips.json (Standard: work/<vod>/clips.json)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("validate_clips", args.verbose)

    work = _lib.work_dir(args.vod_id)
    path = args.file or work / "clips.json"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        plan = _lib.read_json(path)
    except FileNotFoundError:
        _lib.fail(f"{path} fehlt. Claude schreibt clips.json nach dem Lesen der Transkripte.", _lib.EXIT_INVALID)
    except json.JSONDecodeError as exc:
        _lib.fail(f"{path}: kein gültiges JSON ({exc})", _lib.EXIT_INVALID)
    meta = _lib.read_json(work / "meta.json") if (work / "meta.json").is_file() else {}
    signals = _lib.read_json(work / "signals.json") if (work / "signals.json").is_file() else {}
    sections = _lib.read_json(work / "sections.json")["sections"] if (work / "sections.json").is_file() else []
    if not sections:
        errors.append("sections.json fehlt oder ist leer: erst fetch_sections.py laufen lassen")
    cfg, cfg_warn = _lib.load_config()
    warnings += cfg_warn
    st = _lib.streamer_settings(cfg, meta.get("streamer"))
    duration = float(meta.get("duration_s") or signals.get("duration_s") or 0)
    muted = (signals.get("audio") or {}).get("muted", [])
    words = _lib.load_words(work)
    credit = str(st.get("credit") or "").replace("{channel}", str(st.get("channel") or meta.get("channel") or ""))
    facecam_known = isinstance((st.get("layout") or {}).get("facecam"), list)

    clips = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips, list) or not clips:
        _lib.fail("clips.json braucht eine nicht-leere Liste 'clips'", _lib.EXIT_INVALID)
    if isinstance(plan, dict) and plan.get("vod_id") not in (None, args.vod_id):
        errors.append(f"vod_id in clips.json ({plan.get('vod_id')}) passt nicht zu --vod-id {args.vod_id}")

    seen: set[str] = set()
    main_ranges: list[tuple[str, float, float]] = []
    summary = []
    for i, clip in enumerate(clips):
        where = f"clips[{i}]"
        if not isinstance(clip, dict):
            errors.append(f"{where}: Objekt erwartet")
            continue
        cid = clip.get("id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            errors.append(f"{where}.id: nur a-z, 0-9, _ und - (max. 40), gefunden {cid!r}")
            cid = f"#{i}"
        elif cid in seen:
            errors.append(f"{cid}: id doppelt")
        seen.add(cid)
        segs = clip.get("segments")
        if not isinstance(segs, list) or not segs:
            errors.append(f"{cid}: 'segments' fehlt oder ist leer")
            continue
        total = 0.0
        own: list[tuple[float, float]] = []
        for k, seg in enumerate(segs):
            sw = f"{cid}.segments[{k}]"
            try:
                a, b = float(seg["start"]), float(seg["end"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{sw}: start/end als Zahlen (VOD-Sekunden) nötig")
                continue
            if a >= b:
                errors.append(f"{sw}: start {a} muss vor end {b} liegen")
                continue
            if a < 0 or (duration and b > duration + 0.5):
                errors.append(f"{sw}: {a}-{b} liegt ausserhalb des VODs (0-{duration:.0f})")
            if sections and not any(float(s["start"]) - 0.05 <= a and b <= float(s["end"]) + 0.05
                                    and Path(s["file"]).is_file() for s in sections):
                errors.append(f"{sw}: {a}-{b} ist durch keinen geladenen Abschnitt gedeckt "
                              f"(Abschnitte: {[(s['start'], s['end']) for s in sections]})")
            role = seg.get("role")
            if role == "cold_open":
                if k != 0:
                    errors.append(f"{sw}: cold_open nur als erstes Segment")
                if b - a > COLD_OPEN_MAX_S + 0.01:
                    errors.append(f"{sw}: cold_open {b - a:.1f} s, erlaubt max. {COLD_OPEN_MAX_S} s")
            else:
                for oa, ob in own:
                    if overlap((a, b), (oa, ob)) > 0.05:
                        errors.append(f"{sw}: überlappt ein anderes Segment desselben Clips")
                own.append((a, b))
                for other, oa, ob in main_ranges:
                    if overlap((a, b), (oa, ob)) > 0.5:
                        errors.append(f"{sw}: überlappt Clip {other} ({oa}-{ob})")
            total += b - a
            for w in words:   # Schnitt mitten im Wort?
                for edge in (a, b):
                    if w["s"] + 0.05 < edge < w["e"] - 0.05:
                        warnings.append(f"{sw}: Schnitt bei {edge} mitten im Wort '{w['w']}' "
                                        f"({w['s']}-{w['e']})")
            for ma, mb in muted:
                if overlap((a, b), (ma, mb)) > 0:
                    warnings.append(f"{sw}: enthält stummen Abschnitt {ma}-{mb}")
            for ba, bb, title in meta.get("blocked_ranges") or []:
                if ba is not None and bb is not None and overlap((a, b), (ba, bb)) > 0:
                    warnings.append(f"{sw}: Kategorie '{title}' (Musik/Claim-Risiko)")
        main_ranges += [(cid, a, b) for a, b in own]
        if total + 0.01 < HARD_MIN_S:
            errors.append(f"{cid}: Länge {total:.1f} s, unter {HARD_MIN_S:.0f} s trägt kein Short.")
        elif total + 0.01 < float(st["min_clip_s"]):
            warnings.append(
                f"{cid}: {total:.1f} s ist kürzer als min_clip_s {st['min_clip_s']:.0f} s. "
                "Ok, wenn der Moment vorbei ist – nicht mit Stille auffüllen."
            )
        elif total > float(st["max_clip_s"]) + 0.01:
            errors.append(
                f"{cid}: Länge {total:.1f} s über max_clip_s {st['max_clip_s']:.0f} s "
                "(bis Musik sicher erkannt wird: max. 60 s)."
            )
        if not any(any(float(s["start"]) <= w["s"] and w["e"] <= float(s["end"]) for w in words)
                   for s in segs if isinstance(s, dict) and "start" in s and "end" in s):
            warnings.append(f"{cid}: keine Transkript-Wörter im Clip, Untertitel bleiben leer")

        hook = clip.get("hook")
        if not isinstance(hook, str) or not hook.strip():
            errors.append(f"{cid}: 'hook' fehlt (Text oben in den ersten Sekunden)")
        elif len(hook) > 80:
            errors.append(f"{cid}: hook hat {len(hook)} Zeichen, max. 80")
        title = clip.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{cid}: 'title' fehlt")
        elif len(title) > 100:
            errors.append(f"{cid}: title hat {len(title)} Zeichen, max. 100 (YouTube)")
        caps = clip.get("captions")
        if not isinstance(caps, dict):
            errors.append(f"{cid}: 'captions' mit youtube, tiktok, instagram fehlt")
        else:
            for p in PLATFORMS:
                text = caps.get(p)
                if not isinstance(text, str) or not text.strip():
                    errors.append(f"{cid}.captions.{p} fehlt")
                    continue
                if credit and credit.lower() not in text.lower():
                    warnings.append(f"{cid}.captions.{p}: Credit-Zeile '{credit}' fehlt")
                elif credit and "#" in text and text.lower().index(credit.lower()) > text.index("#"):
                    warnings.append(f"{cid}.captions.{p}: Credit-Zeile steht hinter den Hashtags")
            yt = f"{caps.get('youtube', '')} {title or ''}"
            if "#shorts" not in yt.lower():
                warnings.append(f"{cid}: #Shorts fehlt in YouTube-Titel/-Caption")
        if not str(clip.get("commentary") or "").strip():
            warnings.append(f"{cid}: kein Kommentar-Skript (Eigenleistung fürs Voice-over)")
        layout = clip.get("layout")
        if layout not in (None, "auto", "stack", "blur", "center"):
            errors.append(f"{cid}: layout {layout!r} unbekannt (auto, stack, blur, center)")
        clip_cam = clip.get("facecam")
        if clip_cam is not None and not _lib.is_rect(clip_cam):
            errors.append(f"{cid}: facecam muss [x, y, breite, höhe] sein, nicht {clip_cam!r}")
        cam_known = facecam_known or _lib.is_rect(clip_cam)
        if layout == "stack" and not cam_known:
            warnings.append(f"{cid}: stack ohne Facecam (weder in clips.json noch in streamers.yaml), "
                            "es wird blur genutzt")
        summary.append({"id": cid, "duration_s": round(total, 2), "segments": len(segs)})

    warnings = list(dict.fromkeys(warnings))
    for w in warnings:
        log.warning(w)
    for e in errors:
        log.error(e)
    _lib.emit({"vod_id": args.vod_id, "file": str(path), "clips": summary, "errors": errors,
               "warnings": warnings}, _lib.EXIT_INVALID if errors else _lib.EXIT_OK)


if __name__ == "__main__":
    main()
