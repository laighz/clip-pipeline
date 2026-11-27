# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Prüft ~/clips/streamers.yaml (Schema, Wertebereiche, Backend-Einstellungen).

Die Datei ist optional: Fehlt sie, gelten die eingebauten Defaults.
Unbekannte Streamer laufen mit den Defaults. Fehler (Exit 2) gibt es nur bei
kaputten Werten, die einen späteren Schritt scheitern lassen würden.

Beispiele:
  validate_config.py                       # ganze Datei prüfen
  validate_config.py --streamer xyz        # zusätzlich gemischte Einstellungen zeigen
  validate_config.py --init [--dry-run]    # Beispiel-Config anlegen, falls keine da ist
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Any

import _lib

PLATFORMS_NOW = {"twitch"}
PLATFORMS_LATER = {"youtube", "kick"}


def check_number(errors: list[str], where: str, value: Any, lo: float | None = None,
                 hi: float | None = None, integer: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: Zahl erwartet, gefunden {value!r}")
        return
    if integer and int(value) != value:
        errors.append(f"{where}: ganze Zahl erwartet, gefunden {value!r}")
    if lo is not None and value < lo:
        errors.append(f"{where}: {value} ist kleiner als {lo}")
    if hi is not None and value > hi:
        errors.append(f"{where}: {value} ist größer als {hi}")


def check_rect(errors: list[str], where: str, value: Any) -> None:
    if value is None or value is False or value == "auto":   # False = bewusst keine Facecam
        return
    if (not isinstance(value, list) or len(value) != 4
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        errors.append(f"{where}: erwartet [x, y, breite, höhe] als ganze Zahlen, "
                      f"null oder auto, gefunden {value!r}")
        return
    if value[0] < 0 or value[1] < 0 or value[2] <= 0 or value[3] <= 0:
        errors.append(f"{where}: x/y >= 0 und Breite/Höhe > 0 nötig, gefunden {value}")


def check_defaults(errors: list[str], warnings: list[str], where: str, d: dict[str, Any]) -> None:
    rules = {
        "min_clip_s": (5, 180, False), "max_clip_s": (5, 180, False),
        "chat_sigma": (0.1, 10, False), "reaction_offset_s": (0, 60, False),
        "window_s": (5, 120, False), "min_spacing_s": (0, 3600, False),
        "min_chat_per_hour": (0, 10**7, False), "top_candidates": (1, 100, True),
        "section_pre_s": (0, 600, False), "section_post_s": (0, 600, False),
        "baseline_s": (60, 7200, False),
    }
    for key, value in d.items():
        if key in rules:
            lo, hi, integer = rules[key]
            check_number(errors, f"{where}.{key}", value, lo, hi, integer)
        elif key not in _lib.DEFAULTS:
            warnings.append(f"{where}.{key}: unbekannter Schlüssel, wird ignoriert")
    if all(isinstance(d.get(k), (int, float)) for k in ("min_clip_s", "max_clip_s")):
        if d["min_clip_s"] >= d["max_clip_s"]:
            errors.append(f"{where}: min_clip_s ({d['min_clip_s']}) muss kleiner als "
                          f"max_clip_s ({d['max_clip_s']}) sein")
        if d["max_clip_s"] > 60:
            warnings.append(f"{where}.max_clip_s > 60: Shorts über 60 s mit Copyright-Claim "
                            "sperrt YouTube weltweit")
    if "language" in d and not (d["language"] is None or isinstance(d["language"], str)):
        errors.append(f"{where}.language: Text wie de, en oder auto erwartet")
    if "credit" in d and not isinstance(d["credit"], str):
        errors.append(f"{where}.credit: Text erwartet")
    if "blocked_categories" in d and not (isinstance(d["blocked_categories"], list)
                                         and all(isinstance(x, str) for x in d["blocked_categories"])):
        errors.append(f"{where}.blocked_categories: Liste von Texten erwartet")
    emotes = d.get("emotes")
    if emotes is not None:
        if not isinstance(emotes, dict) or not all(
                isinstance(v, list) and all(isinstance(x, str) for x in v) for v in emotes.values()):
            errors.append(f"{where}.emotes: erwartet Kategorie -> Liste von Emote-Namen")


def validate(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    raw = cfg["_raw"]
    for key in raw:
        if key not in ("defaults", "transcription", "render", "live", "streamers"):
            warnings.append(f"{key}: unbekannter Abschnitt, wird ignoriert")
    lv = cfg.get("live") or _lib.LIVE_DEFAULTS
    for key, lo, hi in (("segment_s", 30, 3600), ("max_hours", 0.1, 48), ("min_free_gb", 1, 10000),
                        ("chat_window_s", 5, 120), ("chat_baseline_s", 30, 7200), ("confirm_s", 1, 60),
                        ("cooldown_s", 0, 3600), ("min_uniq", 1, 10000)):
        check_number(errors, f"live.{key}", lv.get(key), lo, hi)
    if not isinstance(lv.get("record_dir"), str) or not lv["record_dir"].strip():
        errors.append("live.record_dir: Ordnerpfad als Text erwartet")
    else:
        rec = Path(lv["record_dir"]).expanduser()
        anchor = Path(rec.anchor) if rec.anchor else None
        if anchor and not anchor.exists():
            errors.append(f"live.record_dir: Laufwerk {rec.anchor} existiert nicht")

    check_defaults(errors, warnings, "defaults", raw.get("defaults") or {})
    merged = cfg["defaults"]
    if merged["min_clip_s"] >= merged["max_clip_s"]:
        errors.append("defaults: min_clip_s muss kleiner als max_clip_s sein")

    tr = cfg["transcription"]
    if tr.get("backend") not in ("faster-whisper", "whisper-cpp"):
        errors.append(f"transcription.backend: faster-whisper oder whisper-cpp, gefunden {tr.get('backend')!r}")
    fw = tr.get("faster_whisper") or {}
    if fw.get("compute_type") not in ("int8", "int8_float32", "float32", "int16", "float16", "int8_float16"):
        errors.append(f"transcription.faster_whisper.compute_type unbekannt: {fw.get('compute_type')!r}")
    check_number(errors, "transcription.faster_whisper.cpu_threads", fw.get("cpu_threads"), 1, 64, True)
    if tr.get("backend") == "whisper-cpp":
        wc = tr.get("whisper_cpp") or {}
        binary = wc.get("binary", "whisper-cli")
        if not (Path(binary).expanduser().is_file() or _lib.find_tool(binary)):
            errors.append(f"transcription.whisper_cpp.binary '{binary}' nicht gefunden "
                          "(Pfad eintragen oder whisper.cpp installieren)")
        model = Path(str(wc.get("model", ""))).expanduser()
        if not model.is_file():
            errors.append(f"transcription.whisper_cpp.model '{model}' existiert nicht")

    rd = cfg["render"]
    if rd.get("encoder") not in ("auto", "amf", "videotoolbox", "x264"):
        errors.append(f"render.encoder: auto, amf, videotoolbox oder x264, gefunden {rd.get('encoder')!r}")
    if rd.get("layout") not in ("auto", "stack", "blur", "center"):
        errors.append(f"render.layout: auto, stack, blur oder center, gefunden {rd.get('layout')!r}")
    for key in ("width", "height", "facecam_height"):
        check_number(errors, f"render.{key}", rd.get(key), 16, 4096, True)
        if isinstance(rd.get(key), int) and rd[key] % 2:
            errors.append(f"render.{key}: muss gerade sein (h264/yuv420p), gefunden {rd[key]}")
    if isinstance(rd.get("facecam_height"), int) and isinstance(rd.get("height"), int) \
            and rd["facecam_height"] >= rd["height"]:
        errors.append("render.facecam_height muss kleiner als render.height sein")
    check_number(errors, "render.fps", rd.get("fps"), 1, 60)
    check_number(errors, "render.hook_duration_s", rd.get("hook_duration_s"), 0, 60)
    check_number(errors, "render.words_per_caption", rd.get("words_per_caption"), 1, 8, True)
    if not isinstance(rd.get("caption_emoji_catalog", True), bool):
        errors.append("render.caption_emoji_catalog: true oder false erwartet")
    if rd.get("caption_emoji_mode", "reviewed") not in ("reviewed", "legacy"):
        errors.append("render.caption_emoji_mode: reviewed oder legacy erwartet")

    streamers = cfg["streamers"]
    if not isinstance(streamers, list):
        errors.append("streamers: Liste erwartet")
        return errors, warnings
    seen: set[str] = set()
    for i, entry in enumerate(streamers):
        where = f"streamers[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: Mapping erwartet")
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", sid):
            errors.append(f"{where}.id: nur a-z, 0-9, _ und -, gefunden {sid!r}")
            continue
        where = f"streamers[{sid}]"
        if sid in seen:
            errors.append(f"{where}: id doppelt vergeben")
        seen.add(sid)
        platform = entry.get("platform", "twitch")
        if platform in PLATFORMS_LATER:
            errors.append(f"{where}.platform: {platform} kommt erst in Phase 2, jetzt nur twitch")
        elif platform not in PLATFORMS_NOW:
            errors.append(f"{where}.platform: unbekannt ({platform!r}), erlaubt: twitch")
        if "channel" in entry and not isinstance(entry["channel"], str):
            errors.append(f"{where}.channel: Text erwartet")
        if "permission" in entry:
            warnings.append(f"{where}.permission: wird nicht mehr ausgewertet (keine Allowlist)")
        layout = entry.get("layout") or {}
        if not isinstance(layout, dict):
            errors.append(f"{where}.layout: Mapping erwartet")
        else:
            check_rect(errors, f"{where}.layout.facecam", layout.get("facecam"))
            check_rect(errors, f"{where}.layout.game", layout.get("game"))
            check_rect(errors, f"{where}.layout.gameplay", layout.get("gameplay"))
            if isinstance(layout.get("game"), list) and isinstance(layout.get("gameplay"), list) \
                    and list(layout["game"]) != list(layout["gameplay"]):
                errors.append(f"{where}.layout: game und gameplay widersprechen sich")
            if layout.get("facecam") == "auto":
                warnings.append(f"{where}.layout.facecam: auto (MediaPipe) ist noch nicht gebaut, "
                                "es wird das blur-Layout genutzt")
        tuning = entry.get("tuning") or {}
        if not isinstance(tuning, dict):
            errors.append(f"{where}.tuning: Mapping erwartet")
        else:
            check_defaults(errors, warnings, f"{where}.tuning", tuning)
        for key in entry:
            if key not in ("id", "platform", "channel", "credit", "layout", "blocked_categories",
                           "tuning", "permission", "games", "notes"):
                warnings.append(f"{where}.{key}: unbekannter Schlüssel, wird ignoriert")
    return errors, warnings


def init_text() -> str:
    """Beispiel-Config ohne echten Streamer-Eintrag (das Beispiel bleibt als Kommentar)."""
    text = (_lib.ASSETS_DIR / "streamers.example.yaml").read_text(encoding="utf-8")
    head, sep, tail = text.partition("\nstreamers:\n")
    if not sep:
        return text
    commented = "".join(("# " + line) if line.strip() else line for line in tail.splitlines(keepends=True))
    return (head + "\n# Streamer trägt manage_streamers.py ein (auch automatisch beim ersten VOD).\n"
            "# Beispiel für einen Eintrag:\n# streamers:\n" + commented.rstrip() + "\n\nstreamers: []\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prüft ~/clips/streamers.yaml. Keine Allowlist: unbekannte Streamer laufen mit Defaults.",
        epilog="Exit 0 = ok (Warnungen möglich), 2 = ungültige Werte.",
    )
    parser.add_argument("--config", type=Path, help="Pfad zur YAML (Standard: <CLIPS_HOME>/streamers.yaml)")
    parser.add_argument("--streamer", help="Streamer-ID: zeigt die gemischten Einstellungen")
    parser.add_argument("--init", action="store_true",
                        help="<CLIPS_HOME>/streamers.yaml aus assets/streamers.example.yaml anlegen (ohne Streamer), falls nicht vorhanden")
    parser.add_argument("--dry-run", action="store_true", help="mit --init: nur zeigen, nichts schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("validate_config", args.verbose)

    path = args.config or _lib.config_path()
    created = None
    if args.init:
        if path.exists():
            log.info("%s existiert schon, --init ändert nichts", path)
        elif args.dry_run:
            created = f"(dry-run) würde {path} anlegen"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            for sub in ("raw", "work", "out", "state", "logs", "bin"):
                (path.parent / sub).mkdir(exist_ok=True)
            path.write_text(init_text(), encoding="utf-8")
            created = str(path)

    try:
        cfg, warnings = _lib.load_config(path)
    except Exception as exc:  # noqa: BLE001  (YAML-Syntaxfehler usw.)
        _lib.fail(f"{path}: kann nicht gelesen werden: {exc}", _lib.EXIT_INVALID)
    errors, more = validate(cfg)
    warnings += more

    result: dict[str, Any] = {
        "config": str(path),
        "exists": path.exists(),
        "created": created,
        "streamers": [s.get("id") for s in cfg["streamers"] if isinstance(s, dict)],
        "transcription_backend": cfg["transcription"].get("backend"),
        "errors": errors,
        "warnings": warnings,
    }
    if args.streamer:
        settings = _lib.streamer_settings(cfg, args.streamer)
        if not settings["known"]:
            warnings.append(f"Streamer '{args.streamer}' nicht in der Config: es gelten die Defaults")
        result["settings"] = settings
    for w in warnings:
        log.warning(w)
    for e in errors:
        log.error(e)
    _lib.emit(result, _lib.EXIT_INVALID if errors else _lib.EXIT_OK)


if __name__ == "__main__":
    main()
