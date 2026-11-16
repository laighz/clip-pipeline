# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "numpy>=1.26", "ruamel.yaml>=0.18"]
# ///
"""Macht aus einer (laufenden oder beendeten) Live-Aufnahme einen Arbeitsordner wie bei einem VOD.

Danach laufen die clipping-streams-Skripte unverändert mit --vod-id <rec_id>:
  detect_highlights -> fetch_sections (nutzt die Segmente, kein Download) -> transcribe ->
  clips.json -> validate_clips -> render_clip -> verify_clip -> log_clip

Nimmt nur FERTIGE Segmente (aus segments.csv). Audio-Analyse pro Segment wird gecacht,
wiederholte Aufrufe während der Aufnahme sind also schnell.
Schreibt work/<rec_id>/meta.json und signals.json (Chat: nur Zählwerte aus chat_counts.jsonl).

Aufruf: live_prepare.py --rec-id <id>
Exit: 0 ok, 2 Aufnahme unbekannt oder noch kein Segment fertig.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import live_common
from live_common import _lib


def chat_signals(rec_dir: Path, n: int, window: int) -> dict[str, Any]:
    path = rec_dir / "chat_counts.jsonl"
    if not path.is_file():
        return {"available": False, "reason": "kein chat_counts.jsonl (Aufnahme ohne Chat?)"}
    keys = ("msgs", "short", "chars", "emote_any")
    arrays: dict[str, list] = {k: [0] * n for k in keys}
    uniq_trailing = [0] * n
    dup_trailing = [0.0] * n
    emotes: dict[str, list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        t = int(rec.get("t", -1))
        if not 0 <= t < n:
            continue
        for k in keys:
            arrays[k][t] = rec.get(k, 0)
        uniq_trailing[t] = rec.get("uniq_w", 0)
        dup_trailing[t] = rec.get("dup_w", 0.0)
        for cat, v in (rec.get("emotes") or {}).items():
            emotes.setdefault(cat, [0] * n)[t] = v
    half = window // 2   # Live-Fenster ist rückwärts [t-W, t]; VOD-Signale erwarten zentriert
    uniq = [uniq_trailing[min(n - 1, t + half)] for t in range(n)]
    dup = [dup_trailing[min(n - 1, t + half)] for t in range(n)]
    total = sum(arrays["msgs"])
    return {"available": True, "messages_total": total, "per_hour": round(total / max(n / 3600, 1e-9), 1),
            "window_s": window, **arrays, "emotes": emotes, "uniq_w": uniq, "dup_w": dup}


def main() -> None:
    parser = argparse.ArgumentParser(description="Live-Aufnahme -> Arbeitsordner wie bei einem VOD (meta.json, signals.json).",
                                     epilog="Exit: 0 ok, 2 Aufnahme unbekannt oder noch kein Segment fertig.")
    parser.add_argument("--rec-id", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("live_prepare", args.verbose)
    import fetch_signals  # noqa: PLC0415  (clipping-streams: analyse_audio)

    sf = live_common.state_file(args.rec_id)
    if not sf.is_file():
        _lib.fail(f"Aufnahme {args.rec_id} unbekannt", _lib.EXIT_INVALID)
    st = json.loads(sf.read_text(encoding="utf-8"))
    spec_path = _lib.state_dir() / f"live_{args.rec_id}.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {}
    rec_dir = Path(st["rec_dir"])
    segs = live_common.read_segments(rec_dir)
    if not segs:
        _lib.fail(f"noch kein Segment fertig (ein Segment dauert {spec.get('live', {}).get('segment_s', 600)} s)",
                  _lib.EXIT_INVALID)
    duration = segs[-1]["end"]
    n = int(math.ceil(duration)) + 1
    ffmpeg = _lib.require_tool("ffmpeg")
    work = _lib.work_dir(args.rec_id)
    cache = work / "audio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    db = [-120.0] * n
    for seg in segs:
        cf = cache / (Path(seg["file"]).stem + ".json")
        if cf.is_file():
            part = json.loads(cf.read_text(encoding="utf-8"))
        else:
            part = fetch_signals.analyse_audio(Path(seg["file"]), ffmpeg, seg["end"] - seg["start"])["db"]
            cf.write_text(json.dumps(part), encoding="utf-8")
        base = int(round(seg["start"]))
        for i, v in enumerate(part):
            if 0 <= base + i < n:
                db[base + i] = v
    muted = _lib.find_muted(db)
    window = int(spec.get("live", {}).get("chat_window_s", 25))
    chat = chat_signals(rec_dir, n, window)
    spikes = live_common.read_spikes(args.rec_id)
    meta = {"source": "live", "vod_id": args.rec_id, "streamer": st.get("streamer"), "channel": st.get("channel"),
            "title": f"Live-Aufnahme {st.get('channel')} {st.get('started_at', '')}", "url": None, "input": None,
            "duration_s": duration, "segments": segs, "categories": [], "blocked_ranges": [],
            "live_status": st.get("status"), "live_spikes": spikes, "ads": st.get("ads", []),
            "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    signals = {"vod_id": args.rec_id, "duration_s": duration, "chat": chat,
               "audio": {"available": True, "band_hz": [300, 3400], "db": db, "muted": muted},
               "clips": {"available": False, "reason": "live", "items": []}}
    _lib.write_json(work / "meta.json", meta)
    _lib.write_json(work / "signals.json", signals)
    log.info("%d Segmente bis %.0f s vorbereitet", len(segs), duration)
    _lib.emit({"rec_id": args.rec_id, "vod_id": args.rec_id, "segments": len(segs), "duration_s": duration,
               "live_status": st.get("status"), "spikes": len(spikes),
               "chat": {k: chat.get(k) for k in ("available", "messages_total", "per_hour")},
               "next": [f"detect_highlights.py --vod-id {args.rec_id}", f"fetch_sections.py --vod-id {args.rec_id}",
                        f"transcribe.py --vod-id {args.rec_id}"]})


if __name__ == "__main__":
    main()
