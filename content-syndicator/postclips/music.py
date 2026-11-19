"""Validate native editor handoffs and optionally mix an explicitly embedded track."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_SOURCE_HOSTS = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "snapchat": ("snapchat.com", "snap.com"),
    "facebook": ("facebook.com", "fb.watch"),
}
_SETTINGS_KEYS = {"schema_version", "catalog", "region", "timezone", "max_age_days", "volume", "master_sha256"}
_NATIVE_KEYS = {"schema_version", "mode", "master_sha256", "music_volume", "original_volume"}
_AUDIO_FORMATS = "aac,aiff,flac,mp3,mov,ogg,wav"
_MAX_CATALOG_BYTES = 128 * 1024
_MAX_TRACKS = 128
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MIX_VERSION = 1


@dataclass(frozen=True)
class Track:
    file: Path
    volume: float
    title: str
    artist: str
    source_platform: str
    source_url: str
    checked_on: date
    region: str
    cleared_for: tuple[str, ...]
    rights_reference: str

    def report(self) -> dict:
        return {"title": self.title, "artist": self.artist,
                "source_platform": self.source_platform, "source_url": self.source_url,
                "checked_on": self.checked_on.isoformat(), "region": self.region,
                "cleared_for": list(self.cleared_for), "rights_reference": self.rights_reference}


def _read_json(path: Path, max_bytes: int) -> dict:
    if not path.is_file():
        raise ValueError(f"Datei fehlt: {path}")
    with path.open("rb") as file:
        payload = file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Datei zu groß: {path}")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON-Objekt erwartet: {path}")
    return value


def _text(value, name: str, maximum: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{name}: Text mit 1–{maximum} Zeichen erwartet")
    return value.strip()


def _volume(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not .001 <= value <= 1:
        raise ValueError("volume muss zwischen 0.001 und 1 liegen.")
    return float(value)


def _validate_settings(data: dict) -> None:
    version = data.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("versionierter Trendmusik-Sidecar (schema_version 1 oder 2) erwartet")
    if not isinstance(data.get("master_sha256"), str) or not _SHA256.fullmatch(data["master_sha256"]):
        raise ValueError("master_sha256 des freigegebenen Videos fehlt oder ist ungültig")
    if version == 2:
        if data.keys() != _NATIVE_KEYS or data["mode"] != "native":
            raise ValueError("Sidecar der Version 2 erwartet mode native und beide Lautstärken")
        _volume(data["music_volume"])
        _volume(data["original_volume"])
        return
    if data.keys() != _SETTINGS_KEYS:
        raise ValueError("Musik-Katalog und Nutzungsdaten der Version 1 erwartet")
    path = _text(data["catalog"], "catalog", 4096)
    if path != data["catalog"] or "://" in path or not Path(path).is_absolute():
        raise ValueError("catalog muss ein absoluter lokaler Dateipfad sein")
    if not isinstance(data["region"], str) or not re.fullmatch("[A-Z]{2}", data["region"]):
        raise ValueError("region muss ein Länderkürzel wie DE sein")
    if data["timezone"] != _text(data["timezone"], "timezone", 100):
        raise ValueError("timezone muss eine gültige Zeitzone sein")
    ZoneInfo(data["timezone"])
    if type(data["max_age_days"]) is not int or not 0 <= data["max_age_days"] <= 30:
        raise ValueError("max_age_days muss eine Ganzzahl zwischen 0 und 30 sein")
    _volume(data["volume"])


def read_settings(video: Path) -> dict | None:
    """Load an optional opt-in sidecar without touching media."""
    sidecar = Path(video).with_suffix(".music.json")
    if not sidecar.exists() and not sidecar.is_symlink():
        return None
    try:
        data = _read_json(sidecar, 8192)
        _validate_settings(data)
    except (OSError, ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"Trendmusik-Sidecar ungültig ({sidecar.name}): {error}") from error
    return data


def verify_master(video: Path, master_sha256: str) -> None:
    if not isinstance(master_sha256, str) or not _SHA256.fullmatch(master_sha256):
        raise ValueError("SHA-256 des freigegebenen Masters fehlt oder ist ungültig.")
    if _digest(video) != master_sha256:
        raise ValueError("Der freigegebene Master wurde geändert. Reel neu freigeben und übergeben.")


def _official_url(platform: str, url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443) and not any(char.isspace() for char in url)
            and re.fullmatch(r"[a-z0-9.-]+", host) is not None
            and any(host == domain or host.endswith("." + domain)
                    for domain in _SOURCE_HOSTS[platform]))


def _candidate(item, catalog: Path, settings: dict, targets: set[str], today: date) -> Track | None:
    if not isinstance(item, dict) or item.get("region") != settings["region"]:
        return None
    try:
        checked_on = date.fromisoformat(_text(item.get("checked_on"), "checked_on", 10))
        if (checked_on.isoformat() != item["checked_on"]
                or not 0 <= (today - checked_on).days <= settings["max_age_days"]):
            return None
        scope = item.get("cleared_for")
        if not isinstance(scope, list) or any(not isinstance(p, str) or p not in _SOURCE_HOSTS for p in scope):
            return None
        if not targets.issubset(scope):
            return None
        source = _text(item.get("source_platform"), "source_platform", 16)
        url = _text(item.get("source_url"), "source_url", 2048)
        if source not in _SOURCE_HOSTS or not _official_url(source, url):
            return None
        filename = _text(item.get("file"), "file", 4096)
        if "://" in filename:
            return None
        path = (catalog.parent / filename).resolve()
        if not path.is_file():
            return None
        return Track(file=path, volume=float(settings["volume"]),
                     title=_text(item.get("title"), "title", 160), artist=_text(item.get("artist"), "artist", 160),
                     source_platform=source, source_url=url, checked_on=checked_on, region=settings["region"],
                     cleared_for=tuple(dict.fromkeys(scope)),
                     rights_reference=_text(item.get("rights_reference"), "rights_reference", 512))
    except (OSError, RuntimeError, ValueError):
        return None


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ValueError(f"{name} fehlt. FFmpeg für den Musikmix installieren.")
    return executable


def _run(command: list[str], timeout: int, operation: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, check=True)
    except subprocess.CalledProcessError as error:
        detail = " ".join((error.stderr or "").split())[-300:]
        raise ValueError(f"{operation} fehlgeschlagen: {detail}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"{operation} hat das Zeitlimit ({timeout} Sekunden) überschritten.") from error
    except OSError as error:
        raise ValueError(f"{operation} konnte nicht abgeschlossen werden: {error}") from error
    return result.stdout


def _probe(ffprobe: str, path: Path, formats: str) -> dict:
    # Reject playlists and URL protocols, even if a playlist is renamed as MP3.
    return json.loads(_run([ffprobe, "-v", "error", "-protocol_whitelist", "file",
                            "-format_whitelist", formats, "-show_entries",
                            "stream=codec_type,duration:format=duration", "-of", "json", str(path)],
                           8, f"Medienprüfung ({path.name})"))


def _duration(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0


def select(settings: dict, platforms: list[str], *, today: date | None = None) -> Track:
    """Return the newest playable track cleared for every requested upload."""
    targets = set(platforms)
    if not targets or not targets.issubset(_SOURCE_HOSTS):
        raise ValueError("Trendmusik benötigt Ziele aus YouTube, TikTok, Instagram, Snapchat und Facebook.")
    if today is None:
        today = datetime.now(ZoneInfo(settings["timezone"])).date()
    catalog = Path(settings["catalog"])
    setup = (f"In {catalog} eine Empfehlung für {settings['region']} mit lokaler Audioaufnahme "
             f"und Nutzungsfreigabe für {', '.join(sorted(targets))} eintragen.")
    try:
        items = _read_json(catalog, _MAX_CATALOG_BYTES).get("tracks")
        if not isinstance(items, list) or len(items) > _MAX_TRACKS:
            raise ValueError(f"Liste tracks mit höchstens {_MAX_TRACKS} Einträgen erwartet")
    except (OSError, ValueError) as error:
        raise ValueError(f"Trendmusik-Katalog nicht verwendbar. {setup}") from error
    candidates = [_candidate(item, catalog, settings, targets, today) for item in items]
    eligible = sorted((track for track in candidates if track is not None),
                      key=lambda track: track.checked_on, reverse=True)
    if eligible:
        ffprobe = _tool("ffprobe")
        for track in eligible:
            try:
                audio = _probe(ffprobe, track.file, _AUDIO_FORMATS)
            except ValueError:
                continue
            if (_duration(audio.get("format", {}).get("duration"))
                    and any(stream["codec_type"] == "audio" for stream in audio.get("streams", []))):
                return track
    raise ValueError(f"Keine passende Trendmusik (höchstens {settings['max_age_days']} Tage alt). {setup}")


def _digest(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"Mediendatei fehlt: {path}")
    try:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    except OSError as error:
        raise ValueError(f"Mediendatei nicht lesbar: {path}") from error


def _movie_info(ffprobe: str, video: Path) -> tuple[float, bool]:
    info = _probe(ffprobe, video, "mov")
    streams = info.get("streams", [])
    picture = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    if picture is None:
        raise ValueError(f"Videospur fehlt: {video.name}")
    seconds = _duration(picture.get("duration")) or _duration(info.get("format", {}).get("duration"))
    if not seconds:
        raise ValueError(f"Videodauer konnte nicht ermittelt werden: {video.name}")
    return seconds, any(stream["codec_type"] == "audio" for stream in streams)


def _audio_graph(seconds: float, volume: float, has_master_audio: bool) -> str:
    trim = f"apad,atrim=duration={seconds:.6f}"
    stereo = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS"
    fade = min(.3, seconds / 2)
    bed = (f"[1:a:0]{stereo},volume={volume},afade=t=in:d={fade:.6f},"
           f"afade=t=out:st={seconds - fade:.6f}:d={fade:.6f},{trim}[music]")
    limiter = "alimiter=limit=0.95:level=false:latency=true"
    if not has_master_audio:
        return f"{bed};[music]{limiter}[out]"
    return (f"{bed};[0:a:0]{stereo},{trim},asplit=2[master][key];"
            "[music][key]sidechaincompress=threshold=0.025:ratio=8:attack=15:release=250[ducked];"
            f"[master][ducked]amix=inputs=2:duration=first:normalize=0,{limiter}[out]")


def mix_for_upload(video: Path, track: Track, cache_dir: Path, *, master_sha256: str) -> Path:
    """Mix only audio. Keep the approved master and its encoded video stream."""
    video = Path(video).resolve()
    volume = _volume(track.volume)
    verify_master(video, master_sha256)
    track_digest = _digest(track.file)
    key = hashlib.sha256(f"{_MIX_VERSION}:{master_sha256}:{track_digest}:{volume}".encode()).hexdigest()
    cache_dir = Path(cache_dir).resolve()
    destination = cache_dir / f"{key}.mp4"
    ffprobe = _tool("ffprobe")
    seconds, has_master_audio = _movie_info(ffprobe, video)
    if destination.is_file() and not destination.is_symlink():
        try:
            cached_seconds, cached_audio = _movie_info(ffprobe, destination)
            if cached_audio and abs(cached_seconds - seconds) < .05:
                return destination
        except ValueError:
            pass

    ffmpeg = _tool("ffmpeg")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mix-", dir=cache_dir) as work:
            temporary = Path(work) / "upload.mp4"
            _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-xerror",
                  "-filter_complex_threads", "1", "-threads", "1", "-protocol_whitelist", "file",
                  "-format_whitelist", "mov", "-i", str(video),
                  "-stream_loop", "-1", "-threads", "1", "-protocol_whitelist", "file",
                  "-format_whitelist", _AUDIO_FORMATS, "-i", str(track.file),
                  "-filter_complex", _audio_graph(seconds, volume, has_master_audio),
                  "-map", "0:v:0", "-map", "[out]", "-map_metadata", "0", "-c:v", "copy", "-c:a", "aac",
                  "-b:a", "160k", "-threads", "1", "-movflags", "+faststart", str(temporary)],
                 120, "Trendmusik-Mix")
            mixed_seconds, mixed_audio = _movie_info(ffprobe, temporary)
            if not mixed_audio or abs(mixed_seconds - seconds) >= .05:
                raise ValueError("Upload-Kopie hat keine erwartete Videolänge und Audiospur.")
            if _digest(video) != master_sha256 or _digest(track.file) != track_digest:
                raise ValueError("Master oder Musikdatei wurde während des Mixes geändert. Upload erneut vorbereiten.")
            temporary.replace(destination)
    except OSError as error:
        raise ValueError(f"Trendmusik-Cache nicht beschreibbar: {cache_dir}") from error
    return destination
