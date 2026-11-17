# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Prüft die Umgebung für clipping-streams und merkt sich das Ergebnis.

Prüft ffmpeg/ffprobe (inkl. libass, loudnorm, h264_amf per echter
Test-Kodierung), yt-dlp, TwitchDownloaderCLI, uv, freien Speicher und einen
Chromium-Browser. Schreibt das Ergebnis nach <CLIPS_HOME>/state/env.json,
damit render_clip.py den Encoder nicht jedes Mal neu testen muss.

Exit-Codes: 0 alles Nötige da, 4 Pflicht-Programm fehlt, 1 sonstiger Fehler.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import _lib

REQUIRED = ["ffmpeg", "ffprobe", "yt-dlp", "TwitchDownloaderCLI"]
OPTIONAL = ["uv", "whisper-cli", "streamlink", "deno"]
_MAC = sys.platform == "darwin"
HINTS = {
    "ffmpeg": "brew install ffmpeg-full" if _MAC else "winget install --id Gyan.FFmpeg -e",
    "ffprobe": "brew install ffmpeg-full" if _MAC else "winget install --id Gyan.FFmpeg -e",
    "yt-dlp": "brew install yt-dlp" if _MAC else "winget install --id yt-dlp.yt-dlp -e",
    "TwitchDownloaderCLI": "GitHub-Release lay295/TwitchDownloader (CLI, "
                           + ("MacOSArm64" if _MAC else "Windows-x64") + ") nach ~/clips/bin/ entpacken",
    "uv": "brew install uv" if _MAC else "winget install --id astral-sh.uv -e",
    "whisper-cli": "nur für transcription.backend: whisper-cpp (Phase 1 optional)",
    "streamlink": "erst für den Live-Modus (Phase 2)",
    "deno": "erst für YouTube (Phase 2)",
}
MIN_FREE_GB = 20


def version_of(name: str, path: str) -> str | None:
    args = {
        "ffmpeg": ["-hide_banner", "-version"],
        "ffprobe": ["-hide_banner", "-version"],
        "TwitchDownloaderCLI": ["--version"],
    }.get(name, ["--version"])
    try:
        proc = _lib.run([path, *args], timeout=30)
    except Exception as exc:  # noqa: BLE001
        return f"? ({exc.__class__.__name__})"
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0][:120] if text else None


def ffmpeg_features(ffmpeg: str) -> dict:
    enc = _lib.run([ffmpeg, "-hide_banner", "-encoders"], timeout=30).stdout or ""
    flt = _lib.run([ffmpeg, "-hide_banner", "-filters"], timeout=30).stdout or ""

    def has(listing: str, token: str) -> bool:
        return any(len(p := line.split()) > 1 and p[1] == token for line in listing.splitlines())

    return {
        "h264_amf_listed": has(enc, "h264_amf"),
        "libx264": has(enc, "libx264"),
        "subtitles": has(flt, "subtitles"),
        "loudnorm": has(flt, "loudnorm"),
    }


def amf_test(ffmpeg: str) -> tuple[bool, str]:
    """Gelistet heißt nicht nutzbar: echte 1-s-Kodierung in 1080x1920."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "color=c=black:size=1080x1920:rate=30", "-t", "1",
           "-c:v", "h264_amf", "-quality", "quality", "-f", "null", "-"]
    start = time.monotonic()
    try:
        proc = _lib.run(cmd, timeout=90)
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"
    took = time.monotonic() - start
    if proc.returncode == 0:
        return True, f"ok in {took:.1f} s"
    return False, (proc.stderr or "").strip()[-300:]


def find_browser() -> str | None:
    candidates = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        candidates += [
            Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(base) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(base) / "Chromium" / "Application" / "chrome.exe",
        ]
    if sys.platform == "darwin":
        for app, binary in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"),
                            ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium")):
            for root in (Path("/Applications"), Path.home() / "Applications"):
                candidates.append(root / f"{app}.app" / "Contents" / "MacOS" / binary)
    for name in ("chrome", "msedge", "chromium", "chromium-browser", "google-chrome", "brave"):
        hit = shutil.which(name)
        if hit:
            candidates.insert(0, Path(hit))
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prüft Programme, Encoder und Speicher für clipping-streams.",
        epilog="Ausgabe: JSON auf stdout. Exit 0 = ok, 4 = Pflicht-Programm fehlt.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="nichts schreiben (state/env.json bleibt unverändert)")
    parser.add_argument("--skip-encode-test", action="store_true",
                        help="h264_amf nicht per Test-Kodierung prüfen")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("check_env", args.verbose)

    home = _lib.clips_home()
    tools: dict[str, dict] = {}
    missing: list[str] = []
    for name in REQUIRED + OPTIONAL:
        path = _lib.find_tool(name)
        tools[name] = {"path": path, "version": version_of(name, path) if path else None,
                       "required": name in REQUIRED}
        if not path:
            (missing if name in REQUIRED else []).append(name)
            level = log.error if name in REQUIRED else log.info
            level("%s fehlt -> %s", name, HINTS[name])

    features: dict = {}
    encoder = {"amf_ok": False, "chosen": "x264", "detail": "ffmpeg fehlt"}
    if tools["ffmpeg"]["path"]:
        features = ffmpeg_features(tools["ffmpeg"]["path"])
        if not features["subtitles"]:
            log.error("ffmpeg ohne libass (subtitles-Filter) -> Full-Build installieren")
            missing.append("ffmpeg:libass")
        if features["h264_amf_listed"] and not args.skip_encode_test:
            ok, detail = amf_test(tools["ffmpeg"]["path"])
            encoder = {"amf_ok": ok, "chosen": "amf" if ok else "x264", "detail": detail}
        elif features["h264_amf_listed"]:
            encoder = {"amf_ok": None, "chosen": "x264", "detail": "Test übersprungen"}
        else:
            encoder = {"amf_ok": False, "chosen": "x264", "detail": "h264_amf nicht im Build"}
        log.info("Encoder: %s (%s)", encoder["chosen"], encoder["detail"])

    probe_dir = home if home.exists() else home.parent
    usage = shutil.disk_usage(probe_dir)
    free_gb = round(usage.free / 1024**3, 1)
    if free_gb < MIN_FREE_GB:
        log.warning("Nur %.1f GB frei unter %s", free_gb, probe_dir)

    browser = find_browser()
    vulkan = Path(os.environ.get("WINDIR", "C:/Windows"), "System32", "vulkan-1.dll").is_file() \
        if os.name == "nt" else None

    result = {
        "ok": not missing,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": sys.version.split()[0],
        "clips_home": str(home),
        "clips_home_exists": home.exists(),
        "free_gb": free_gb,
        "tools": tools,
        "ffmpeg_features": features,
        "encoder": encoder,
        "browser": browser,
        "vulkan_runtime": vulkan,
        "missing": missing,
        "hints": {m.split(":")[0]: HINTS.get(m.split(":")[0]) for m in missing},
    }
    if not args.dry_run:
        _lib.write_json(_lib.state_dir() / "env.json", result)
    # stdout kurz halten: Pfade stehen in env.json.
    brief = {k: v for k, v in result.items() if k not in ("tools", "os")}
    brief["tools"] = {n: (t["version"] or "da") if t["path"] else None for n, t in tools.items()}
    brief["env_json"] = None if args.dry_run else str(_lib.state_dir() / "env.json")
    _lib.emit(brief, _lib.EXIT_OK if not missing else _lib.EXIT_MISSING_TOOL)


if __name__ == "__main__":
    main()
