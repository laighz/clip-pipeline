"""Gemeinsame Helfer für die clipping-streams-Skripte.

Nur Standardbibliothek. Wird von den Skripten per `import _lib` geladen
(Python legt den Skript-Ordner automatisch auf sys.path).

Konventionen:
- JSON nach stdout (genau ein Objekt pro Aufruf), Logs nach stderr.
- Alle Zeitangaben in Sekunden relativ zum VOD-Anfang ("VOD-Zeit").
- Datenordner: $CLIPS_HOME oder ~/clips.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

import _proc

# ---------------------------------------------------------------- Exit-Codes
EXIT_OK = 0
EXIT_ERROR = 1          # Laufzeitfehler (Download, ffmpeg, ...)
EXIT_INVALID = 2        # Eingabe/Config/clips.json ungültig
EXIT_MISSING_TOOL = 4   # benötigtes Programm fehlt
EXIT_VERIFY = 5         # Prüfung eines Ergebnisses fehlgeschlagen
EXIT_TIMEOUT = 124      # nur intern: returncode von run(), wenn ein Programm zu lange lief

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_DIR / "assets"

log = logging.getLogger("clips")


# ---------------------------------------------------------------- Ein-/Ausgabe
def setup(name: str, verbose: bool = False) -> logging.Logger:
    """UTF-8 für stdout/stderr erzwingen (Windows-Pipes sind sonst cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=f"[{name}] %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    global log
    log = logging.getLogger(name)
    return log


def emit(obj: dict[str, Any], code: int = EXIT_OK) -> NoReturn:
    """Ergebnis als eine JSON-Zeile auf stdout ausgeben und beenden."""
    obj.setdefault("ok", code == EXIT_OK)
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def fail(message: str, code: int = EXIT_ERROR, **extra: Any) -> NoReturn:
    log.error(message)
    emit({"ok": False, "error": message, **extra}, code)


# ---------------------------------------------------------------- Pfade
def clips_home() -> Path:
    env = os.environ.get("CLIPS_HOME")
    return Path(env).expanduser() if env else Path.home() / "clips"


def config_path() -> Path:
    return clips_home() / "streamers.yaml"


def work_dir(vod_id: str) -> Path:
    return clips_home() / "work" / safe_id(vod_id)


def raw_dir(vod_id: str) -> Path:
    return clips_home() / "raw" / safe_id(vod_id)


def out_dir(vod_id: str) -> Path:
    return clips_home() / "out" / safe_id(vod_id)


def state_dir() -> Path:
    return clips_home() / "state"


def safe_id(value: str) -> str:
    """Dateisystem-sichere ID (für VOD- und Clip-IDs)."""
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    if not value:
        raise ValueError("leere ID")
    return value[:80]


def section_id(start: float, end: float) -> str:
    """Stabile Abschnitts-ID aus der VOD-Zeitspanne, z. B. 's340-452'.

    Gleiche Spanne -> gleiche ID, andere Spanne -> andere ID. Ein Transkript
    kann so nie unter der ID eines anderen Zeitfensters liegen.
    """
    return f"s{int(round(float(start)))}-{int(round(float(end)))}"


def candidate_id(moment_s: float) -> str:
    """Stabile Kandidaten-ID aus dem Moment, z. B. 'c230'. Bleibt gleich, wenn
    detect_highlights erneut läuft (Live-Modus) und die Rangfolge sich ändert."""
    return f"c{int(round(float(moment_s)))}"


def video_source_at(vod_id: str, at: float, strict: bool = True) -> tuple[str, float] | None:
    """Videodatei und ihren Offset für eine VOD-Sekunde finden.

    Erst die geladenen Abschnitte (raw/<vod>/sections/), sonst die lokale
    Eingabedatei aus meta.json. Rückgabe: (Pfad, Offset in VOD-Sekunden).
    strict=False gibt None zurück, statt das Skript zu beenden.
    """
    work = work_dir(vod_id)
    if (work / "sections.json").is_file():
        for sec in read_json(work / "sections.json")["sections"]:
            if sec["start"] <= at <= sec["end"] and Path(sec["file"]).is_file():
                return sec["file"], float(sec["file_offset"])
    if (work / "meta.json").is_file():
        meta = read_json(work / "meta.json")
        if meta.get("input") and Path(str(meta["input"])).is_file():
            return str(meta["input"]), 0.0
    if not strict:
        return None
    fail(f"Keine Videodatei für Sekunde {at:.0f} gefunden. Erst fetch_sections.py laufen lassen.",
         EXIT_INVALID)


def is_rect(value: Any) -> bool:
    """True für ein Rechteck [x, y, breite, höhe] aus vier nicht-negativen Zahlen.

    Breite und Höhe müssen größer als 0 sein. Gilt für layout.facecam, layout.game
    und das optionale facecam pro Clip in clips.json.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return False
    x, y, w, h = value
    return x >= 0 and y >= 0 and w > 0 and h > 0


def load_sections(work: Path) -> list[dict[str, Any]]:
    path = work / "sections.json"
    return read_json(path).get("sections", []) if path.is_file() else []


def load_words(work: Path) -> list[dict[str, Any]]:
    """Wörter (VOD-Zeit) aus den Transkripten der AKTUELLEN Abschnitte, nach Zeit sortiert.

    Transkripte ersetzter Abschnitte bleiben liegen, werden aber ignoriert. Überlappen
    sich zwei Abschnitte, zählt jedes Wort nur einmal (gleiches Wort, Start < 0,3 s auseinander).
    """
    tdir = work / "transcripts"
    sections = load_sections(work)
    if sections:
        files = [tdir / f"{s['id']}.json" for s in sections]
    else:
        files = sorted(tdir.glob("*.json")) if tdir.is_dir() else []
    words: list[dict[str, Any]] = []
    for f in files:
        if f.is_file():
            words += read_json(f).get("words", [])
    words.sort(key=lambda w: (w["s"], w["e"]))
    unique: list[dict[str, Any]] = []
    for w in words:
        if any(u["w"] == w["w"] and abs(u["s"] - w["s"]) < 0.3 for u in unique[-8:]):
            continue
        unique.append(w)
    return unique


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    """Atomar schreiben: erst .tmp, dann umbenennen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------- Tool-Suche
_TOOL_ENV = {
    "ffmpeg": "CLIPS_FFMPEG",
    "ffprobe": "CLIPS_FFPROBE",
    "yt-dlp": "CLIPS_YTDLP",
    "TwitchDownloaderCLI": "CLIPS_TWITCHDL",
    "whisper-cli": "CLIPS_WHISPER_CLI",
    "streamlink": "CLIPS_STREAMLINK",
    "deno": "CLIPS_DENO",
    "uv": "CLIPS_UV",
}


def find_tool(name: str) -> str | None:
    """Programm finden, ohne dem PATH blind zu vertrauen.

    Reihenfolge: Umgebungsvariable CLIPS_<TOOL>, PATH, winget-Links,
    winget-Paketordner, ~/clips/bin (rekursiv).
    """
    env_var = _TOOL_ENV.get(name)
    if env_var and os.environ.get(env_var):
        candidate = Path(os.environ[env_var]).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    exe = name if name.lower().endswith(".exe") or os.name != "nt" else name + ".exe"
    local = os.environ.get("LOCALAPPDATA")
    patterns: list[str] = []
    if local:
        patterns.append(os.path.join(local, "Microsoft", "WinGet", "Links", exe))
        patterns.append(os.path.join(local, "Microsoft", "WinGet", "Packages", "*", exe))
        patterns.append(os.path.join(local, "Microsoft", "WinGet", "Packages", "*", "*", exe))
        patterns.append(os.path.join(local, "Microsoft", "WinGet", "Packages", "*", "*", "bin", exe))
        patterns.append(os.path.join(local, "Programs", "*", "bin", exe))      # z. B. Streamlink
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            patterns.append(os.path.join(base, "*", "bin", exe))
    # Tools liegen immer unter ~/clips/bin, auch wenn CLIPS_HOME (z. B. für Tests) umgebogen ist.
    for bin_dir in dict.fromkeys([clips_home() / "bin", Path.home() / "clips" / "bin"]):
        patterns.append(str(bin_dir / "**" / exe))
    for pattern in patterns:
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[-1]
    return None


def require_tool(name: str) -> str:
    path = find_tool(name)
    if not path:
        fail(
            f"Programm '{name}' nicht gefunden. Erst check_env.py laufen lassen; "
            f"Installationshinweise stehen in references/troubleshooting.md.",
            EXIT_MISSING_TOOL,
            tool=name,
        )
    return path


def run(cmd: list[str], *, check: bool = False, capture: bool = True,
        timeout: float | None = None, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Befehl ohne Shell ausführen (keine Quoting-Probleme unter Windows).

    Timeout: kein Traceback, sondern returncode EXIT_TIMEOUT (124) und ein Hinweis in
    stderr. Alle Aufrufer prüfen returncode ohnehin und melden dann sauber per JSON.
    """
    log.debug("run: %s", " ".join(cmd))
    try:
        proc = _proc.execute(cmd, capture=capture, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        def as_text(value: Any) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", "replace")
            return value or ""
        note = f"\nTimeout: {Path(cmd[0]).name} nach {timeout:.0f} s abgebrochen."
        log.error("Timeout nach %.0f s: %s", timeout, Path(cmd[0]).name)
        proc = subprocess.CompletedProcess(cmd, EXIT_TIMEOUT, as_text(exc.stdout), as_text(exc.stderr) + note)
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        fail(f"Befehl fehlgeschlagen ({proc.returncode}): {Path(cmd[0]).name}\n{tail}", EXIT_ERROR)
    return proc


def ffprobe_json(path: str | Path) -> dict[str, Any]:
    ffprobe = require_tool("ffprobe")
    proc = run([ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)], timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe konnte {path} nicht lesen: {proc.stderr.strip()[-300:]}")
    return json.loads(proc.stdout or "{}")


def media_duration(path: str | Path) -> float:
    info = ffprobe_json(path)
    return float(info.get("format", {}).get("duration") or 0.0)


# ---------------------------------------------------------------- Zeit
def parse_time(value: str | float | int) -> float:
    """'1:23:45.5', '83:45', '5025.5' oder Zahl -> Sekunden."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"Zeitformat nicht erkannt: {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def fmt_time(seconds: float) -> str:
    """Sekunden -> 'H:MM:SS.mmm' (für yt-dlp/ffmpeg und Menschen)."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    rest = seconds - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{rest:06.3f}"


# ---------------------------------------------------------------- Audio
MUTED_DB = -70.0     # darunter gilt eine Sekunde als stumm (Twitch-Mute ist digitale Stille)
MUTED_MIN_S = 5      # so viele Sekunden in Folge = stummer Abschnitt


def find_muted(db: list[float], threshold: float = MUTED_DB, min_s: int = MUTED_MIN_S) -> list[list[int]]:
    """Stumme Abschnitte [start, ende) aus einer dBFS-Kurve (ein Wert pro Sekunde)."""
    muted: list[list[int]] = []
    run_start = None
    for i, value in enumerate([*db, 0.0]):   # 0.0 am Ende schließt einen offenen Abschnitt
        if value < threshold and run_start is None:
            run_start = i
        elif value >= threshold and run_start is not None:
            if i - run_start >= min_s:
                muted.append([run_start, i])
            run_start = None
    return muted


# ---------------------------------------------------------------- Config
DEFAULTS: dict[str, Any] = {
    "min_clip_s": 20,
    "max_clip_s": 60,
    "chat_sigma": 1.5,
    "reaction_offset_s": 22,
    "window_s": 25,
    "min_spacing_s": 120,
    "min_chat_per_hour": 500,
    "top_candidates": 20,
    "section_pre_s": 60,
    "section_post_s": 30,
    "baseline_s": 600,
    "language": "de",
    "credit": "Clip: @{channel} auf Twitch",
    "blocked_categories": ["Music", "DJs"],
    "emotes": {
        "funny": ["KEKW", "LUL", "OMEGALUL", "LULW", "ICANT", "xdd"],
        "hype": ["PogChamp", "Pog", "POGGERS", "PogU", "W"],
        "shock": ["monkaS", "D:", "WutFace", "monkaW"],
        "sad": ["Sadge", "BibleThump", "PepeHands"],
    },
}

TRANSCRIPTION_DEFAULTS: dict[str, Any] = {
    "backend": "faster-whisper",
    "faster_whisper": {"model": "small", "compute_type": "int8", "cpu_threads": 1,
                       "beam_size": 5, "vad_filter": True},
    "whisper_cpp": {"binary": "whisper-cli", "model": "~/clips/models/ggml-small.bin",
                    "threads": 6, "extra_args": []},
}

RENDER_DEFAULTS: dict[str, Any] = {
    "encoder": "auto",            # auto | amf | x264
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "layout": "auto",             # auto | stack | blur | center
    "facecam_height": 608,        # Höhe des Facecam-Streifens im Stack-Layout
    "hook_duration_s": 4.0,
    "words_per_caption": 3,
    "loudnorm": "I=-16:TP=-1.5:LRA=11",
    "audio_bitrate": "160k",
    "x264": {"crf": 20, "preset": "veryfast"},
    "amf": {"quality": "quality", "rc": "qvbr", "qvbr_quality_level": 22,
            "b": "10M", "maxrate": "16M"},
}


LIVE_DEFAULTS: dict[str, Any] = {
    "record_dir": "~/clips/raw/live",   # große Rohdateien, z. B. auf eine HDD legen
    "quality": "best",                  # streamlink-Qualität: best, 1080p60, 720p60, ...
    "segment_s": 600,                   # 10-Minuten-Segmente: ein Absturz kostet max. ein Segment
    "max_hours": 10,                    # Schutz für die Platte
    "min_free_gb": 15,                  # darunter stoppt die Aufnahme
    "chat_window_s": 25,
    "chat_baseline_s": 600,
    "confirm_s": 3,                     # so viele Sekunden in Folge über chat_sigma = Spike
    "cooldown_s": 120,                  # Mindestabstand zwischen Spikes
    "min_uniq": 5,                      # Mindestzahl verschiedener Chatter im Fenster
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def build_config(raw: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    """Rohes YAML-Mapping + eingebaute Defaults -> vollständige Config (eine Stelle für alle Skripte)."""
    return {
        "defaults": deep_merge(DEFAULTS, raw.get("defaults") or {}),
        "transcription": deep_merge(TRANSCRIPTION_DEFAULTS, raw.get("transcription") or {}),
        "render": deep_merge(RENDER_DEFAULTS, raw.get("render") or {}),
        "live": deep_merge(LIVE_DEFAULTS, raw.get("live") or {}),
        "streamers": raw.get("streamers") or [],
        "_path": str(path) if path else None,
        "_raw": raw,
    }


def load_config(path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """streamers.yaml laden (optional). Gibt (config, warnungen) zurück.

    Braucht PyYAML; das aufrufende Skript deklariert es per PEP 723.
    """
    import yaml  # noqa: PLC0415  (nur wenn gebraucht)

    path = path or config_path()
    warnings: list[str] = []
    raw: dict[str, Any] = {}
    if path.is_file():
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: oberste Ebene muss ein Mapping sein")
    else:
        warnings.append(f"{path} fehlt, es gelten die eingebauten Defaults.")
    return build_config(raw, path), warnings


def streamer_settings(cfg: dict[str, Any], streamer_id: str | None) -> dict[str, Any]:
    """Defaults + Tuning eines Streamers. Unbekannte Streamer laufen mit Defaults."""
    settings = dict(cfg["defaults"])
    settings["id"] = streamer_id
    settings["channel"] = streamer_id
    settings["platform"] = "twitch"
    settings["layout"] = {}
    settings["known"] = False
    if not streamer_id:
        return settings
    for entry in cfg.get("streamers", []):
        if isinstance(entry, dict) and str(entry.get("id", "")).lower() == streamer_id.lower():
            settings["known"] = True
            settings["channel"] = entry.get("channel") or streamer_id
            settings["platform"] = entry.get("platform", "twitch")
            settings["layout"] = entry.get("layout") or {}
            if "credit" in entry:
                settings["credit"] = entry["credit"]
            if "blocked_categories" in entry:
                settings["blocked_categories"] = entry["blocked_categories"]
            settings = deep_merge(settings, entry.get("tuning") or {})
            break
    return settings


# ---------------------------------------------------------------- ffmpeg-Filter
def escape_filter_path(path: str | Path) -> str:
    """Pfad für ffmpeg-Filteroptionen wie subtitles=... escapen.

    Windows: Backslashes -> Slashes, Doppelpunkt nach Laufwerksbuchstabe und
    alle Sonderzeichen des Filtergraphen escapen. Ergebnis ohne Anführungszeichen,
    direkt nutzbar als  subtitles=filename=<ergebnis>
    """
    text = str(Path(path).resolve()).replace("\\", "/")
    # Ebene 1 (Optionswert eines Filters): \ ' : escapen. Backslash zuerst.
    for ch in "\\':":
        text = text.replace(ch, "\\" + ch)
    # Ebene 2 (Filtergraph): \ ' [ ] , ; escapen, inkl. der Backslashes aus Ebene 1.
    for ch in "\\'[],;":
        text = text.replace(ch, "\\" + ch)
    return text
