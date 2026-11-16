# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Rendert Clips aus clips.json als 1080x1920-MP4 mit Wort-Untertiteln, Hook und loudnorm.

Pro Clip:
  - jedes Segment (VOD-Zeit) wird ein eigener ffmpeg-Input mit -ss/-t (genauer Schnitt,
    da ohnehin neu kodiert wird), Cold Open zuerst, dann concat
  - Layout: stack (Facecam oben, Spiel unten; braucht layout.facecam in streamers.yaml),
    blur (ganzes Bild mittig auf unscharfem Hintergrund) oder center (mittiger 9:16-Ausschnitt)
  - Untertitel: ASS aus assets/caption-style.ass, aktives Wort gelb; Hook oben
  - Ton: loudnorm I=-16, 48 kHz AAC
  - Encoder: h264_amf (AMD) laut state/env.json, sonst libx264; scheitert AMF, automatisch libx264
Ausgabe: out/<vod>/<clip>.mp4 + <clip>.json (Metadaten) + <clip>.txt (Captions zum Kopieren).

Aufruf: render_clip.py --vod-id <id> [--clip clip01] [--encoder auto|amf|x264] [--force] [--dry-run]
Erst validate_clips.py (Exit 0), dann --dry-run, dann echt.
Exit: 0 ok, 1 ffmpeg-Fehler, 2 Eingabe ungültig, 4 Programm fehlt.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Any

import _lib


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


def build_ass(clip: dict[str, Any], words: list[dict], rconf: dict[str, Any],
              credit: str | None) -> tuple[str, float, int]:
    template = (_lib.ASSETS_DIR / "caption-style.ass").read_text(encoding="utf-8")
    events: list[str] = []
    out_t = 0.0
    n_words = 0
    per = int(rconf.get("words_per_caption", 3))
    upper = bool(rconf.get("caption_uppercase", False))
    for seg in clip["segments"]:
        a, b = float(seg["start"]), float(seg["end"])
        seg_words = [w for w in words if w["s"] >= a - 0.05 and w["e"] <= b + 0.05]
        groups: list[list[dict]] = []
        cur: list[dict] = []
        for w in seg_words:   # max. `per` Wörter, aber nach Satzzeichen immer neue Gruppe
            cur.append(w)
            if len(cur) >= per or w["w"].rstrip().endswith((".", "!", "?", "…")):
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        for gi, group in enumerate(groups):
            texts = [ass_text(w["w"].upper() if upper else w["w"]) for w in group]
            next_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
            for wi, w in enumerate(group):
                start = w["s"]
                if wi + 1 < len(group):
                    end = group[wi + 1]["s"]
                else:   # letztes Wort der Gruppe: bis zur nächsten Gruppe stehen lassen (max. 0,6 s Lücke)
                    end = min(next_start, w["e"] + 0.6) if next_start else min(b, w["e"] + 0.6)
                start, end = max(start, a), min(end, b)
                if end - start < 0.04:
                    continue
                parts = [("{\\c&H0000E5FF&}" + t + "{\\c&H00FFFFFF&}") if k == wi else t
                         for k, t in enumerate(texts)]
                events.append(f"Dialogue: 0,{ass_time(out_t + start - a)},{ass_time(out_t + end - a)},"
                              f"Caption,,0,0,0,,{' '.join(parts)}")
            n_words += len(group)
        out_t += b - a
    hook_s = min(float(rconf.get("hook_duration_s", 4.0)), out_t)
    if hook_s > 0:
        events.insert(0, f"Dialogue: 1,{ass_time(0)},{ass_time(hook_s)},Hook,,0,0,0,,{ass_text(clip['hook'])}")
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
    x = rconf.get("x264") or {}
    return ["-c:v", "libx264", "-preset", str(x.get("preset", "veryfast")), "-crf", str(x.get("crf", 20)),
            "-profile:v", "high"]


def find_section(sections: list[dict], a: float, b: float) -> dict:
    for s in sections:
        if float(s["start"]) - 0.05 <= a and b <= float(s["end"]) + 0.05:
            return s
    raise ValueError(f"Segment {a}-{b} liegt in keinem Abschnitt")


def build_cmd(ffmpeg: str, clip: dict[str, Any], sections: list[dict], ass_path: Path, out: Path,
              layout: str, rconf: dict[str, Any], facecam: list[int] | None, game: list[int] | None,
              encoder: str) -> list[str]:
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


# ------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clips aus clips.json als 1080x1920-MP4 rendern (Untertitel, Hook, loudnorm).",
        epilog="Exit: 0 ok, 1 ffmpeg-Fehler, 2 Eingabe ungültig, 4 Programm fehlt.",
    )
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--clip", help="nur diese Clip-ID(s), kommagetrennt")
    parser.add_argument("--encoder", choices=["auto", "amf", "x264"], help="überschreibt render.encoder")
    parser.add_argument("--force", action="store_true", help="vorhandene MP4 neu rendern")
    parser.add_argument("--dry-run", action="store_true", help="nur ffmpeg-Befehle zeigen, nichts schreiben")
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
    rconf = cfg["render"]
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
    words = _lib.load_words(work)

    ffmpeg = _lib.require_tool("ffmpeg")
    odir = _lib.out_dir(args.vod_id)
    rdir = work / "render"
    results = []
    for clip in clips:
        cid = _lib.safe_id(clip["id"])
        out = odir / f"{cid}.mp4"
        part = odir / f"{cid}.part.mp4"   # erst hierhin rendern, nach Erfolg umbenennen
        sidecar_path = odir / f"{cid}.json"
        # Pro Clip darf ein eigenes Facecam-Rechteck stehen: Overlays wandern
        # innerhalb eines Streams (z. B. zwei Cams, die die Ecke tauschen).
        clip_facecam = clip.get("facecam")
        cam = clip_facecam if _lib.is_rect(clip_facecam) else facecam
        layout = clip.get("layout") or rconf.get("layout", "auto")
        if layout == "auto":
            layout = "stack" if cam else "blur"
        if layout == "stack" and not cam:
            layout = "blur"
            warnings.append(f"{cid}: keine Facecam bekannt, nutze blur")
        ass_path = rdir / f"{cid}.ass"
        try:
            ass, total, n_words = build_ass(clip, words, rconf, credit)
            cmd = build_cmd(ffmpeg, clip, sections, ass_path, part, layout, rconf, cam, game, encoder)
        except (KeyError, TypeError, ValueError) as exc:
            _lib.fail(f"{cid}: clips.json passt nicht zu den Abschnitten ({exc}). Erst validate_clips.py.",
                      _lib.EXIT_INVALID)
        if args.dry_run:
            results.append({"id": cid, "layout": layout, "encoder": encoder, "duration_s": round(total, 2),
                            "words": n_words, "out": str(out), "ffmpeg": cmd})
            continue
        if out.is_file() and sidecar_path.is_file() and not args.force:
            results.append({"id": cid, "status": "schon da", "out": str(out)})
            continue
        if out.is_file() and not sidecar_path.is_file():
            warnings.append(f"{cid}: {out.name} ohne Sidecar (Rendern früher abgebrochen?), wird neu gerendert")
        rdir.mkdir(parents=True, exist_ok=True)
        odir.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(ass, encoding="utf-8")
        t0 = time.monotonic()
        used = encoder
        proc = _lib.run(cmd, timeout=1800)
        fallback = None
        if proc.returncode != 0 and encoder == "amf":
            fallback = (proc.stderr or "").strip()[-300:]
            log.warning("%s: h264_amf scheiterte, versuche libx264", cid)
            used = "x264"
            cmd = build_cmd(ffmpeg, clip, sections, ass_path, part, layout, rconf, cam, game, "x264")
            proc = _lib.run(cmd, timeout=1800)
        if proc.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)   # halbe Datei weg, damit sie nie als fertig gilt
            _lib.fail(f"{cid}: ffmpeg fehlgeschlagen: {(proc.stderr or '').strip()[-800:]}", _lib.EXIT_ERROR,
                      clip=cid)
        part.replace(out)
        fonts = font_problems(proc.stderr or "")
        if fonts:
            warnings.append(f"{cid}: Schrift-Probleme in den Untertiteln: {fonts[:3]}")
        took = round(time.monotonic() - t0, 1)
        sidecar = {
            "vod_id": args.vod_id, "clip_id": cid, "file": str(out), "duration_s": round(total, 3),
            "segments": clip["segments"], "candidate": clip.get("candidate"), "layout": layout,
            "encoder": used, "amf_fallback_reason": fallback, "render_seconds": took,
            "hook": clip.get("hook"), "title": clip.get("title"), "captions": clip.get("captions"),
            "commentary": clip.get("commentary"), "credit": credit, "streamer": meta.get("streamer"),
            "source_url": meta.get("url"), "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
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
                        "amf_fallback": bool(fallback)})
    for w in warnings:
        log.warning(w)
    _lib.emit({"vod_id": args.vod_id, "dry_run": args.dry_run, "clips": results, "warnings": warnings})


if __name__ == "__main__":
    main()
