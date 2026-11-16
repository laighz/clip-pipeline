# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Prüft fertige Clips mit ffprobe (und optional die Lautheit).

Fehler (Exit 5): Datei fehlt/leer, kein H.264, nicht <width>x<height> (Standard 1080x1920),
nicht yuv420p, keine AAC-Tonspur mit 48 kHz, Dauer weicht > 0,35 s vom Plan ab,
länger als max_clip_s, fps weicht ab.
Warnung: integrierte Lautheit ausserhalb -16 ±2 LUFS (nur mit --loudness).

Aufruf: verify_clip.py --vod-id <id> [--clip clip01]     # Dateien aus out/<vod>/ (Soll-Dauer aus <clip>.json)
        verify_clip.py --file a.mp4 [--file b.mp4]        # beliebige Dateien, ohne Soll-Dauer
Exit: 0 alle ok, 5 mindestens ein Clip fehlerhaft, 2 nichts zu prüfen.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import _lib

DURATION_TOL = 0.35


def measure_lufs(path: Path) -> float | None:
    ffmpeg = _lib.require_tool("ffmpeg")
    proc = _lib.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                     "-af", "ebur128", "-f", "null", "-"], timeout=300)
    hits = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", proc.stderr or "")
    return float(hits[-1]) if hits else None


def check(path: Path, expected: float | None, rconf: dict[str, Any], max_s: float,
          loudness: bool) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    res: dict[str, Any] = {"file": str(path)}
    if not path.is_file() or path.stat().st_size == 0:
        return {**res, "ok": False, "errors": ["Datei fehlt oder ist leer"], "warnings": []}
    res["size_mb"] = round(path.stat().st_size / 1024**2, 2)
    try:
        info = _lib.ffprobe_json(path)
    except RuntimeError as exc:
        return {**res, "ok": False, "errors": [str(exc)], "warnings": []}
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or 0)
    res["duration_s"] = round(duration, 3)
    if not v:
        errors.append("keine Videospur")
    else:
        res["video"] = f"{v.get('codec_name')} {v.get('width')}x{v.get('height')} {v.get('pix_fmt')}"
        if v.get("codec_name") != "h264":
            errors.append(f"Videocodec {v.get('codec_name')}, erwartet h264")
        if (v.get("width"), v.get("height")) != (rconf["width"], rconf["height"]):
            errors.append(f"Auflösung {v.get('width')}x{v.get('height')}, erwartet {rconf['width']}x{rconf['height']}")
        if v.get("pix_fmt") != "yuv420p":
            errors.append(f"pix_fmt {v.get('pix_fmt')}, erwartet yuv420p")
        num, _, den = str(v.get("avg_frame_rate", "0/1")).partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0
        res["fps"] = round(fps, 2)
        if abs(fps - float(rconf["fps"])) > 0.5:
            errors.append(f"{fps:.2f} fps, erwartet {rconf['fps']}")
    if not a:
        errors.append("keine Tonspur")
    else:
        res["audio"] = f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch"
        if a.get("codec_name") != "aac":
            errors.append(f"Audiocodec {a.get('codec_name')}, erwartet aac")
        if str(a.get("sample_rate")) != "48000":
            errors.append(f"Abtastrate {a.get('sample_rate')}, erwartet 48000")
    if expected is not None and abs(duration - expected) > DURATION_TOL:
        errors.append(f"Dauer {duration:.2f} s, laut Plan {expected:.2f} s")
    if duration > max_s + 0.5:
        errors.append(f"Dauer {duration:.1f} s über max_clip_s {max_s}")
    if loudness and a:
        lufs = measure_lufs(path)
        res["lufs"] = lufs
        if lufs is None:
            warnings.append("Lautheit nicht messbar")
        elif abs(lufs - (-16)) > 2:
            warnings.append(f"Lautheit {lufs} LUFS, Ziel -16 ±2")
    return {**res, "ok": not errors, "errors": errors, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fertige Clips mit ffprobe prüfen (1080x1920, h264/yuv420p, AAC 48 kHz, Dauer).",
        epilog="Exit: 0 ok, 5 mindestens ein Clip fehlerhaft, 2 nichts zu prüfen.",
    )
    parser.add_argument("--vod-id", help="alle Clips aus out/<vod>/ prüfen")
    parser.add_argument("--clip", help="mit --vod-id: nur diese Clip-ID(s), kommagetrennt")
    parser.add_argument("--file", type=Path, action="append", help="einzelne Datei (mehrfach möglich)")
    parser.add_argument("--loudness", action="store_true", help="zusätzlich Lautheit (EBU R128) messen")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("verify_clip", args.verbose)

    cfg, _ = _lib.load_config()
    rconf = cfg["render"]
    targets: list[tuple[Path, float | None, float]] = []
    orphans: list[dict[str, Any]] = []
    if args.vod_id:
        meta_path = _lib.work_dir(args.vod_id) / "meta.json"
        meta = _lib.read_json(meta_path) if meta_path.is_file() else {}
        max_s = float(_lib.streamer_settings(cfg, meta.get("streamer"))["max_clip_s"])
        odir = _lib.out_dir(args.vod_id)
        wanted = {c.strip() for c in args.clip.split(",")} if args.clip else None
        for side in sorted(odir.glob("*.json")):
            data = _lib.read_json(side)
            if wanted and data.get("clip_id") not in wanted:
                continue
            targets.append((Path(data["file"]), float(data["duration_s"]), max_s))
        # MP4 ohne Sidecar = Rendern abgebrochen: nie stillschweigend überspringen
        for mp4 in sorted(odir.glob("*.mp4")):
            stem = mp4.name[: -len(".part.mp4")] if mp4.name.endswith(".part.mp4") else mp4.stem
            if wanted and stem not in wanted:
                continue
            if mp4.name.endswith(".part.mp4"):
                orphans.append({"file": str(mp4), "ok": False, "warnings": [],
                                "errors": ["Zwischendatei eines abgebrochenen Renderns: render_clip erneut laufen lassen"]})
            elif not (odir / f"{mp4.stem}.json").is_file():
                orphans.append({"file": str(mp4), "ok": False, "warnings": [],
                                "errors": ["kein Sidecar (Rendern abgebrochen?): render_clip --clip "
                                           f"{mp4.stem} erneut laufen lassen"]})
    for f in args.file or []:
        targets.append((f, None, float(cfg["defaults"]["max_clip_s"])))
    if not targets and not orphans:
        _lib.fail("Nichts zu prüfen: --vod-id mit gerenderten Clips oder --file angeben.", _lib.EXIT_INVALID)

    results = [check(p, exp, rconf, max_s, args.loudness) for p, exp, max_s in targets] + orphans
    for r in results:
        for e in r["errors"]:
            log.error("%s: %s", Path(r["file"]).name, e)
        for w in r["warnings"]:
            log.warning("%s: %s", Path(r["file"]).name, w)
    ok = all(r["ok"] for r in results)
    _lib.emit({"ok": ok, "checked": len(results), "failed": sum(not r["ok"] for r in results),
               "results": results}, _lib.EXIT_OK if ok else _lib.EXIT_VERIFY)


if __name__ == "__main__":
    main()
