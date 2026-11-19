# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Lädt Video nur für die Kandidatenfenster und schreibt sections.json.

Jeder Abschnitt merkt sich seinen VOD-Versatz (file_offset): Sekunde 0 der
Datei = Sekunde file_offset im VOD. Alle späteren Zeiten bleiben VOD-Zeit.

Twitch: yt-dlp --download-sections mit --force-keyframes-at-cuts (exakte
        Schnitte, kostet Rechenzeit), Dateien nach raw/<vod>/sections/ (nie löschen).
Lokal (--input bei fetch_signals): kein Download, Abschnitte zeigen auf die
        Originaldatei mit file_offset 0.

Aufruf: fetch_sections.py --vod-id <id> [--candidates c01,c03] [--max 8] [--whole] [--dry-run]
Exit: 0 ok, 1 Download-Fehler, 2 Eingabe fehlt, 4 Programm fehlt.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import _lib

MERGE_GAP_S = 5


def whole_section(vod_id: str, dry_run: bool) -> None:
    """Kurze lokale Aufnahme: ein Abschnitt über die ganze Datei, Original unangetastet."""
    work = _lib.work_dir(vod_id)
    meta_path = work / "meta.json"
    if not meta_path.is_file():
        _lib.fail(f"{meta_path} fehlt. Erst fetch_signals.py --input laufen lassen.", _lib.EXIT_INVALID)
    meta = _lib.read_json(meta_path)
    src = meta.get("input")
    if meta.get("source") != "local" or not src or not Path(src).is_file():
        _lib.fail("--whole braucht eine lokale Aufnahme (fetch_signals.py --input).", _lib.EXIT_INVALID)
    duration = float(meta.get("duration_s") or _lib.media_duration(src))
    if duration <= 0:
        _lib.fail(f"Dauer nicht lesbar: {src}", _lib.EXIT_ERROR)
    sec = {
        "id": _lib.section_id(0, duration),
        "start": 0.0,
        "end": round(duration, 3),
        "candidates": [],
        "mode": "local",
        "file": str(Path(src).resolve()),
        "file_offset": 0.0,
        "status": "lokal",
    }
    if not dry_run:
        _lib.write_json(work / "sections.json", {"sections": [sec]})
    _lib.emit({"vod_id": vod_id, "mode": "local-whole", "dry_run": dry_run, "sections": [sec]})


def merge_windows(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kandidatenfenster nach Zeit sortieren und überlappende/nahe Fenster verbinden."""
    windows = sorted(([float(c["section"][0]), float(c["section"][1]), c["id"]] for c in cands),
                     key=lambda w: w[0])
    merged: list[dict[str, Any]] = []
    for start, end, cid in windows:
        if merged and start <= merged[-1]["end"] + MERGE_GAP_S:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["candidates"].append(cid)
        else:
            merged.append({"start": start, "end": end, "candidates": [cid]})
    return merged


def plan_sections(existing: list[dict[str, Any]], windows: list[dict[str, Any]]
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Neue Fenster mit den schon vorhandenen Abschnitten zusammenführen.

    - Liegt ein Fenster ganz in einem vorhandenen Abschnitt, wird nichts neu geladen.
    - Überlappt es vorhandene Abschnitte, entsteht ein neuer Abschnitt über die
      Vereinigung; die alten gelten als ersetzt (Rohdateien bleiben liegen).
    Rückgabe: (alle aktuellen Abschnitte, davon neu zu erzeugende, IDs ersetzter Abschnitte).
    """
    current = [dict(s) for s in existing]
    todo: list[dict[str, Any]] = []
    superseded: list[str] = []
    for w in windows:
        host = next((s for s in current if float(s["start"]) - 0.05 <= w["start"]
                     and w["end"] <= float(s["end"]) + 0.05), None)
        if host:
            host["candidates"] = list(dict.fromkeys([*host.get("candidates", []), *w["candidates"]]))
            continue
        touching = [s for s in current if float(s["start"]) <= w["end"] + MERGE_GAP_S
                    and w["start"] <= float(s["end"]) + MERGE_GAP_S]
        new = {"start": min([w["start"], *(float(s["start"]) for s in touching)]),
               "end": max([w["end"], *(float(s["end"]) for s in touching)]),
               "candidates": list(dict.fromkeys([*(c for s in touching for c in s.get("candidates", [])),
                                                 *w["candidates"]]))}
        new["id"] = _lib.section_id(new["start"], new["end"])
        for s in touching:
            current.remove(s)
            if s in todo:
                todo.remove(s)
            else:
                superseded.append(s["id"])
        current.append(new)
        todo.append(new)
    current.sort(key=lambda s: float(s["start"]))
    return current, todo, superseded


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Video nur für Kandidatenfenster laden -> work/<vod>/sections.json",
        epilog="Exit: 0 ok, 1 Download-Fehler, 2 Eingabe fehlt, 4 Programm fehlt.",
    )
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--candidates", help="kommagetrennte Kandidaten-IDs (Standard: die besten --max)")
    parser.add_argument("--max", type=int, default=8, help="höchstens so viele Kandidaten (Standard 8)")
    parser.add_argument("--height", type=int, default=1080, help="maximale Videohöhe (Standard 1080)")
    parser.add_argument("--stub-url", help="nur für Tests: Abschnitte per yt-dlp aus dieser URL laden (z. B. file:///...)")
    parser.add_argument("--whole", action="store_true",
                        help="lokale Aufnahme: ein Abschnitt über die ganze Datei (kurze Aufnahmen, Volltranskript)")
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts laden oder schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("fetch_sections", args.verbose)
    if args.whole:
        whole_section(args.vod_id, args.dry_run)

    work = _lib.work_dir(args.vod_id)
    for name in ("candidates.json", "meta.json"):
        if not (work / name).is_file():
            _lib.fail(f"{work / name} fehlt. Erst fetch_signals.py und detect_highlights.py laufen lassen.",
                      _lib.EXIT_INVALID)
    meta = _lib.read_json(work / "meta.json")
    cands = _lib.read_json(work / "candidates.json")["candidates"]
    if args.candidates:
        wanted = {c.strip() for c in args.candidates.split(",") if c.strip()}
        unknown = wanted - {c["id"] for c in cands}
        if unknown:
            _lib.fail(f"unbekannte Kandidaten: {sorted(unknown)}", _lib.EXIT_INVALID)
        cands = [c for c in cands if c["id"] in wanted]
    else:
        cands = cands[: args.max]
    if not cands:
        _lib.emit({"vod_id": args.vod_id, "sections": [], "warnings": ["keine Kandidaten"]})

    url = args.stub_url or meta.get("url")
    local = meta.get("source") == "local" and not args.stub_url
    live = meta.get("source") == "live" and not args.stub_url
    warnings: list[str] = []
    planned: list[str] = []

    mode = "local" if local else ("live" if live else "download")
    windows = merge_windows(cands)
    segs = meta.get("segments") or []
    if live:
        if not segs:
            _lib.fail("meta.json enthält keine Segmente. Erst live_prepare.py laufen lassen.", _lib.EXIT_INVALID)
        for w in windows:   # nur bis zum Ende des letzten fertigen Segments
            w["end"] = min(w["end"], segs[-1]["end"])
        late = [w for w in windows if w["end"] <= w["start"]]
        for w in late:
            warnings.append(f"Kandidaten {w['candidates']}: liegen nach dem letzten fertigen Segment, übersprungen")
        windows = [w for w in windows if w["end"] > w["start"]]
    # Vorhandene Abschnitte behalten (nur solche, deren Datei noch existiert).
    existing = [s for s in _lib.load_sections(work) if s.get("file") and Path(s["file"]).is_file()
                and s.get("mode") == mode and s.get("id") == _lib.section_id(s["start"], s["end"])]
    sections, todo, superseded = plan_sections(existing, windows)
    for sec in todo:
        sec["mode"] = mode

    if local:
        for sec in todo:
            sec.update({"file": meta["input"], "file_offset": 0.0, "status": "lokal"})
    elif live:
        # Live-Aufnahme: fertige .ts-Segmente direkt nutzen, über Segmentgrenzen per Stream-Copy verbinden
        ffmpeg = _lib.require_tool("ffmpeg")
        for sec in todo:
            cover = [s for s in segs if s["end"] > sec["start"] and s["start"] < sec["end"]]
            if len(cover) == 1:
                sec.update({"file": cover[0]["file"], "file_offset": cover[0]["start"], "status": "segment"})
                continue
            out = _lib.raw_dir(args.vod_id) / "sections" / f"{sec['id']}.ts"
            sec.update({"file": str(out), "file_offset": cover[0]["start"], "status": "verbunden"})
            if args.dry_run or out.is_file():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", "concat:" + "|".join(s["file"] for s in cover),
                   "-map", "0:v?", "-map", "0:a?", "-c", "copy", str(out)]
            proc = _lib.run(cmd, timeout=600)
            if proc.returncode != 0 or not out.is_file():
                _lib.fail(f"Segmente für {sec['id']} nicht verbindbar: {proc.stderr.strip()[-400:]}", _lib.EXIT_ERROR)
    else:
        ytdlp = _lib.require_tool("yt-dlp")
        ffmpeg = _lib.require_tool("ffmpeg")
        target = _lib.raw_dir(args.vod_id) / "sections"
        for sec in todo:
            out = target / f"{sec['id']}.mp4"
            cmd = [ytdlp, "-f", f"bv*[height<={args.height}]+ba/b[height<={args.height}]/b",
                   "--download-sections", f"*{_lib.fmt_time(sec['start'])}-{_lib.fmt_time(sec['end'])}",
                   "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
                   "--ffmpeg-location", ffmpeg, "--no-playlist", "--no-progress", "-o", str(out), url]
            if url.startswith("file:"):
                cmd.insert(1, "--enable-file-urls")
            sec.update({"file": str(out), "file_offset": float(sec["start"])})
            if args.dry_run:
                planned.append(" ".join(cmd))
                continue
            if out.is_file():
                sec["status"] = "schon da"
            else:
                target.mkdir(parents=True, exist_ok=True)
                log.info("lade %s (%s-%s) ...", sec["id"], _lib.fmt_time(sec["start"]), _lib.fmt_time(sec["end"]))
                proc = _lib.run(cmd, timeout=900)
                if (proc.returncode != 0 or not out.is_file()) and not url.startswith("file:"):
                    # yt-dlp hängt bei noch laufenden VODs: direkter HLS-Download als Ersatz
                    try:
                        import _hls
                        log.warning("yt-dlp fehlgeschlagen/Timeout, nutze HLS-Direktdownload für %s", sec["id"])
                        for junk in out.parent.glob(out.name + "*"):
                            junk.unlink(missing_ok=True)
                        _hls.download_section(ytdlp, ffmpeg, url, float(sec["start"]), float(sec["end"]), args.height, out)
                        proc = subprocess.CompletedProcess(cmd, 0, "", "")
                    except Exception as e:  # noqa: BLE001
                        log.warning("HLS-Ersatz fehlgeschlagen: %s", e)
                if proc.returncode != 0 or not out.is_file():
                    _lib.fail(f"Download von {sec['id']} fehlgeschlagen: {proc.stderr.strip()[-600:]}",
                              _lib.EXIT_ERROR, section=sec["id"])
                sec["status"] = "geladen"
            actual = _lib.media_duration(out)
            sec["file_duration"] = round(actual, 2)
            if abs(actual - (sec["end"] - sec["start"])) > 2:
                warnings.append(f"{sec['id']}: Länge {actual:.1f} s statt {sec['end'] - sec['start']:.1f} s, "
                                "VOD-Versatz evtl. ungenau")

    if args.dry_run:
        _lib.emit({"dry_run": True, "vod_id": args.vod_id, "sections": sections, "new": [s["id"] for s in todo],
                   "superseded": superseded, "would_run": planned, "would_write": str(work / "sections.json")})
    _lib.write_json(work / "sections.json", {"vod_id": args.vod_id, "source": meta.get("source", "download"),
                                            "sections": sections})
    if superseded:
        log.info("ersetzt (Rohdateien bleiben liegen): %s", superseded)
    for w in warnings:
        log.warning(w)
    _lib.emit({"vod_id": args.vod_id, "count": len(sections),
               "sections": [{k: s.get(k) for k in ("id", "start", "end", "candidates", "status")} for s in sections],
               "new": [s["id"] for s in todo], "superseded": superseded,
               "warnings": warnings, "file": str(work / "sections.json")})


if __name__ == "__main__":
    main()
