# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "ruamel.yaml>=0.18", "numpy>=1.26"]
# ///
"""Passt ~/clips/streamers.yaml automatisch an (Kommentare bleiben erhalten).

  manage_streamers.py list
  manage_streamers.py add <id> [--channel C] [--credit TEXT] [--language de] [--facecam x,y,w,h]
  manage_streamers.py set <id> facecam=1440,810,480,270 reaction_offset_s=20 emotes.funny=KEKW,LUL
  manage_streamers.py remove <id>
  manage_streamers.py snapshot --vod-id <id> --at <sekunde>     # Standbild mit Koordinatengitter

Jede Änderung: Backup nach streamers.yaml.bak, danach Prüfung mit validate_config;
bei Fehlern wird das Backup zurückgespielt (Exit 2). --dry-run zeigt nur den Vorher/Nachher-Stand.

Schlüssel für set: channel, credit, platform, facecam, game, blocked_categories und alle
Tuning-Werte (reaction_offset_s, chat_sigma, min_clip_s, max_clip_s, language, emotes.<kategorie>, ...).
Werte: Zahl, Text, Komma-Liste, none/null zum Entfernen.
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

import _lib

TOP_KEYS = {"channel", "credit", "platform", "blocked_categories"}
LAYOUT_KEYS = {"facecam", "game", "gameplay"}
ID_RE = re.compile(r"[a-z0-9_-]{1,64}")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    return y


def load_doc(path: Path) -> CommentedMap:
    if path.is_file():
        with open(path, encoding="utf-8") as fh:
            doc = _yaml().load(fh)
        doc = doc if isinstance(doc, CommentedMap) else CommentedMap()
    else:
        doc = CommentedMap()
    if not isinstance(doc.get("streamers"), list) or not doc["streamers"]:
        doc["streamers"] = CommentedSeq()   # "streamers: []" wäre sonst Flow-Stil für alle Einträge
    return doc


def dump_doc(doc: CommentedMap) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def find(doc: CommentedMap, sid: str) -> CommentedMap | None:
    for entry in doc["streamers"]:
        if isinstance(entry, dict) and str(entry.get("id", "")).lower() == sid:
            return entry
    return None


def flow(values: list[Any]) -> CommentedSeq:
    seq = CommentedSeq(values)
    seq.fa.set_flow_style()
    return seq


def parse_value(key: str, raw: str) -> Any:
    text = raw.strip()
    if text.lower() in ("none", "null", ""):
        return None
    if key in LAYOUT_KEYS:
        if text == "auto":
            return "auto"
        if text.lower() in ("false", "keine", "kein"):
            return False   # bewusst keine Facecam -> blur-Layout, keine weitere Nachfrage
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4 or not all(re.fullmatch(r"\d+", p) for p in parts):
            raise ValueError(f"{key}: erwartet x,y,breite,höhe als ganze Zahlen, gefunden {raw!r}")
        return flow([int(p) for p in parts])
    if key == "blocked_categories" or key.startswith("emotes."):
        return flow([p.strip() for p in text.split(",") if p.strip()])
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d*", text):
        return float(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def apply(entry: CommentedMap, key: str, value: Any) -> None:
    def put(mapping: CommentedMap, k: str, v: Any) -> None:
        if v is None:
            mapping.pop(k, None)
        else:
            mapping[k] = v

    if key == "gameplay":
        key = "game"
    if key in TOP_KEYS:
        put(entry, key, value)
    elif key in LAYOUT_KEYS:
        entry.setdefault("layout", CommentedMap())
        put(entry["layout"], key, value)
        if key == "facecam" and (isinstance(value, list) or value is False):
            entry.ca.items.pop("id", None)   # Hinweis "Facecam noch offen" ist erledigt
    elif key.startswith("emotes."):
        tuning = entry.setdefault("tuning", CommentedMap())
        emotes = tuning.setdefault("emotes", CommentedMap())
        put(emotes, key.split(".", 1)[1], value)
    elif key in _lib.DEFAULTS:
        put(entry.setdefault("tuning", CommentedMap()), key, value)
    else:
        raise ValueError(f"unbekannter Schlüssel {key!r}. Erlaubt: {sorted(TOP_KEYS | LAYOUT_KEYS)}, "
                         f"emotes.<kategorie> und {sorted(k for k in _lib.DEFAULTS if k != 'emotes')}")


def new_entry(sid: str, channel: str | None, credit: str | None, auto: bool) -> CommentedMap:
    entry = CommentedMap()
    entry["id"] = sid
    entry["platform"] = "twitch"
    entry["channel"] = channel or sid
    if credit:
        entry["credit"] = credit
    entry["layout"] = CommentedMap([("facecam", None), ("game", None)])
    if auto:
        entry.yaml_add_eol_comment("automatisch angelegt, Facecam noch offen", "id")
    return entry


def validate_text(text: str) -> tuple[list[str], list[str]]:
    import validate_config  # noqa: PLC0415  (gleicher Ordner)
    import yaml  # noqa: PLC0415

    raw = yaml.safe_load(text) or {}
    return validate_config.validate(_lib.build_config(raw))


def write(path: Path, doc: CommentedMap, dry_run: bool) -> dict[str, Any]:
    text = dump_doc(doc)
    errors, warnings = validate_text(text)
    if errors:
        _lib.fail("Änderung abgelehnt, die Config wäre ungültig: " + "; ".join(errors),
                  _lib.EXIT_INVALID, errors=errors)
    if dry_run:
        return {"written": False, "warnings": warnings}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copyfile(path, path.with_suffix(".yaml.bak"))
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return {"written": True, "backup": str(path.with_suffix(".yaml.bak")) if path.with_suffix(".yaml.bak").exists() else None,
            "warnings": warnings}


def plain(entry: Any) -> Any:
    if isinstance(entry, dict):
        return {str(k): plain(v) for k, v in entry.items()}
    if isinstance(entry, list):
        return [plain(v) for v in entry]
    return entry


def add_streamer(sid: str, channel: str | None = None, credit: str | None = None,
                 extra: dict[str, str] | None = None, auto: bool = False, dry_run: bool = False,
                 path: Path | None = None) -> dict[str, Any]:
    """Legt einen Streamer an (idempotent). Auch von fetch_signals.py genutzt."""
    sid = sid.lower()
    if not ID_RE.fullmatch(sid):
        raise ValueError(f"ungültige Streamer-ID {sid!r}: nur a-z, 0-9, _ und -")
    path = path or _lib.config_path()
    doc = load_doc(path)
    existing = find(doc, sid)
    if existing is not None:
        return {"action": "exists", "id": sid, "entry": plain(existing), "written": False}
    entry = new_entry(sid, channel, credit, auto)
    for key, raw in (extra or {}).items():
        apply(entry, key, parse_value(key, raw))
    doc["streamers"].append(entry)
    result = write(path, doc, dry_run)
    return {"action": "added", "id": sid, "entry": plain(entry), **result}


# ------------------------------------------------------------------ Snapshot
def snapshot(vod_id: str, at: float, grid: int) -> dict[str, Any]:
    work = _lib.work_dir(vod_id)
    source, offset = None, 0.0
    if (work / "sections.json").is_file():
        for sec in _lib.read_json(work / "sections.json")["sections"]:
            if sec["start"] <= at <= sec["end"] and Path(sec["file"]).is_file():
                source, offset = sec["file"], float(sec["file_offset"])
                break
    if source is None and (work / "meta.json").is_file():
        meta = _lib.read_json(work / "meta.json")
        if meta.get("input"):
            source = meta["input"]
    if source is None:
        _lib.fail(f"Keine Videodatei für Sekunde {at} gefunden. Erst fetch_sections.py laufen lassen.",
                  _lib.EXIT_INVALID)
    ffmpeg = _lib.require_tool("ffmpeg")
    info = _lib.ffprobe_json(source)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    width, height = int(video.get("width") or 1920), int(video.get("height") or 1080)
    font = _lib.escape_filter_path(Path(_lib.os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf")
    filters = [f"drawgrid=w={grid}:h={grid}:t=2:c=yellow@0.7"]
    for x in range(0, width, grid):
        filters.append(f"drawtext=fontfile={font}:text={x}:x={x + 4}:y=4:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6")
    for y in range(grid, height, grid):
        filters.append(f"drawtext=fontfile={font}:text={y}:x=4:y={y + 4}:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6")
    out = work / f"snapshot_{int(at)}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at - offset):.3f}",
           "-i", str(source), "-frames:v", "1", "-vf", ",".join(filters), "-q:v", "3", str(out)]
    proc = _lib.run(cmd, timeout=120)
    if proc.returncode != 0 or not out.is_file():
        _lib.fail(f"Snapshot fehlgeschlagen: {proc.stderr.strip()[-400:]}", _lib.EXIT_ERROR)
    return {"snapshot": str(out), "source_size": [width, height], "grid_px": grid,
            "hint": "Bild ansehen, Facecam als x,y,breite,höhe in Quellpixeln ablesen, mit dem User "
                    "bestätigen, dann: manage_streamers.py set <id> facecam=x,y,w,h"}


# ------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(
        description="streamers.yaml automatisch anpassen (Kommentare bleiben, Backup .bak, Prüfung danach).",
        epilog="Exit: 0 ok, 2 ungültige Eingabe oder Config, 1 Fehler.",
    )
    parser.add_argument("--config", type=Path, help="Pfad zur YAML (Standard: <CLIPS_HOME>/streamers.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="alle Streamer zeigen")
    p_add = sub.add_parser("add", help="Streamer anlegen (idempotent)")
    p_add.add_argument("id")
    p_add.add_argument("--channel")
    p_add.add_argument("--credit")
    p_add.add_argument("--language")
    p_add.add_argument("--facecam", help="x,y,breite,höhe")
    p_set = sub.add_parser("set", help="Werte setzen: key=value ...")
    p_set.add_argument("id")
    p_set.add_argument("pairs", nargs="+", metavar="key=value")
    p_rm = sub.add_parser("remove", help="Streamer-Eintrag entfernen (Backup bleibt)")
    p_rm.add_argument("id")
    p_snap = sub.add_parser("snapshot", help="Standbild mit Koordinatengitter für die Facecam-Messung")
    p_snap.add_argument("--vod-id", required=True)
    p_snap.add_argument("--at", required=True, help="VOD-Zeit (Sekunden oder H:MM:SS)")
    p_snap.add_argument("--grid", type=int, default=120)
    args = parser.parse_args()
    _lib.setup("manage_streamers", args.verbose)
    path = args.config or _lib.config_path()

    try:
        if args.cmd == "list":
            doc = load_doc(path)
            _lib.emit({"config": str(path), "exists": path.is_file(),
                       "streamers": [plain(e) for e in doc["streamers"] if isinstance(e, dict)]})
        if args.cmd == "snapshot":
            _lib.emit(snapshot(args.vod_id, _lib.parse_time(args.at), args.grid))
        sid = args.id.lower()
        if args.cmd == "add":
            extra = {k: v for k, v in (("language", args.language), ("facecam", args.facecam)) if v}
            _lib.emit({"config": str(path), **add_streamer(sid, args.channel, args.credit, extra,
                                                           dry_run=args.dry_run, path=path)})
        doc = load_doc(path)
        entry = find(doc, sid)
        if entry is None:
            _lib.fail(f"Streamer '{sid}' steht nicht in {path}. Erst: manage_streamers.py add {sid}",
                      _lib.EXIT_INVALID)
        before = plain(entry)
        if args.cmd == "remove":
            doc["streamers"].remove(entry)
            _lib.emit({"config": str(path), "action": "removed", "id": sid, "before": before,
                       **write(path, doc, args.dry_run)})
        for pair in args.pairs:
            if "=" not in pair:
                _lib.fail(f"'{pair}': erwartet key=value", _lib.EXIT_INVALID)
            key, raw = pair.split("=", 1)
            apply(entry, key.strip(), parse_value(key.strip(), raw))
        _lib.emit({"config": str(path), "action": "updated", "id": sid, "before": before,
                   "after": plain(entry), **write(path, doc, args.dry_run)})
    except ValueError as exc:
        _lib.fail(str(exc), _lib.EXIT_INVALID)


if __name__ == "__main__":
    sys.exit(main())
