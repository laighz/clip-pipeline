# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Kurzstatus für den Abschnitt "Stand" in SKILL.md: Config, Tools, laufende Aufnahmen, letzte VODs.

Liest nur, schreibt nichts, endet immer mit Exit 0 (darf den Skill-Start nie blockieren).
Aufruf: status.py [--brief]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import _lib   # nur Standardbibliothek, kein Einfluss auf die Startzeit


def clips_home() -> Path:
    return _lib.clips_home()   # eine Stelle für CLIPS_HOME / ~/clips


def vod_stage(work: Path, out_root: Path) -> dict[str, Any]:
    def n(pattern: str, base: Path = work) -> int:
        return len(list(base.glob(pattern))) if base.is_dir() else 0

    cands = 0
    if (work / "candidates.json").is_file():
        try:
            cands = len(json.loads((work / "candidates.json").read_text(encoding="utf-8"))["candidates"])
        except (OSError, ValueError, KeyError):
            cands = -1
    out = out_root / work.name
    # nur fertige Clips zählen: MP4 mit Sidecar, keine .part.mp4 eines abgebrochenen Renderns
    rendered = sum(1 for p in out.glob("*.mp4") if not p.name.endswith(".part.mp4")
                   and (out / f"{p.stem}.json").is_file()) if out.is_dir() else 0
    if rendered:
        nxt = "verify_clip/log_clip"
    elif (work / "clips.json").is_file():
        nxt = "validate_clips -> render_clip"
    elif n("transcripts/*.json"):
        nxt = "Transkripte lesen, clips.json schreiben"
    elif (work / "sections.json").is_file():
        nxt = "transcribe"
    elif cands:
        nxt = "fetch_sections"
    elif (work / "signals.json").is_file():
        nxt = "detect_highlights"
    else:
        nxt = "fetch_signals"
    return {"vod": work.name, "candidates": cands, "transcripts": n("transcripts/*.json"),
            "clips_planned": (work / "clips.json").is_file(), "rendered": rendered, "next": nxt,
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(work.stat().st_mtime))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Kurzstatus von clipping-streams (nur lesen, immer Exit 0).")
    parser.add_argument("--brief", action="store_true", help="nur die 3 neuesten VODs")
    args = parser.parse_args()
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {"ok": True}
    try:
        home = clips_home()
        cfg = home / "streamers.yaml"
        out["clips_home"] = str(home)
        streamers, no_facecam = [], []
        if cfg.is_file():
            text = cfg.read_text(encoding="utf-8")
            # Bewusst ohne PyYAML: status.py läuft bei jedem Skill-Start und soll ohne Abhängigkeiten
            # sofort starten. Das Format schreibt manage_streamers.py, daher reicht dieser einfache Parser.
            body = text.split("\nstreamers:", 1)[1] if "\nstreamers:" in text else ""
            for block in re.split(r"\n\s*-\s+id:\s*", "\n" + body)[1:]:
                sid = block.split()[0]
                streamers.append(sid)
                if re.search(r"facecam:\s*(null|~|auto)?\s*(#.*)?$", block, re.M):
                    no_facecam.append(sid)
        out["config"] = {"exists": cfg.is_file(), "streamers": streamers, "facecam_offen": no_facecam}
        env = home / "state" / "env.json"
        if env.is_file():
            data = json.loads(env.read_text(encoding="utf-8"))
            age_h = (time.time() - env.stat().st_mtime) / 3600
            out["env"] = {"encoder": data.get("encoder", {}).get("chosen"), "missing": data.get("missing"),
                          "alter_h": round(age_h, 1), "hinweis": "check_env.py erneut" if age_h > 24 * 7 else None}
        else:
            out["env"] = {"hinweis": "check_env.py noch nie gelaufen"}
        live = []
        for f in sorted((home / "state").glob("live_*.json")) if (home / "state").is_dir() else []:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                live.append({"id": d.get("rec_id"), "channel": d.get("channel"), "status": d.get("status")})
            except (OSError, ValueError):
                continue
        out["live"] = [x for x in live if x["status"] in ("recording", "starting")]
        works = sorted((p for p in (home / "work").glob("*") if p.is_dir()),
                       key=lambda p: p.stat().st_mtime, reverse=True) if (home / "work").is_dir() else []
        out["vods"] = [vod_stage(w, home / "out") for w in works[: 3 if args.brief else 10]]
        probe = home if home.exists() else home.parent
        out["free_gb"] = round(shutil.disk_usage(probe).free / 1024**3, 1)
    except Exception as exc:  # noqa: BLE001  (Status darf nie scheitern)
        out = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
