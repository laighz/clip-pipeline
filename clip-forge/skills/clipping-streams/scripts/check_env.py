# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Prüft die Umgebung für clipping-streams und merkt sich das Ergebnis.

Prüft ffmpeg/ffprobe (inkl. libass, loudnorm), yt-dlp, TwitchDownloaderCLI, uv,
freien Speicher und einen Chromium-Browser. Auf Windows testet es h264_amf.
Auf macOS vergleicht es h264_videotoolbox mit libx264 und schreibt den
Gewinner nach <CLIPS_HOME>/state/env.json. Andere Systeme bleiben bei libx264,
ohne eine Mac-Messung zu behaupten. Whisper wird nur notiert, das Backend
bleibt faster-whisper.

Exit-Codes: 0 alles Nötige da, 4 Pflicht-Programm fehlt, 1 sonstiger Fehler.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import _lib

REQUIRED = ["ffmpeg", "ffprobe", "yt-dlp", "TwitchDownloaderCLI"]
OPTIONAL = ["uv", "whisper-cli", "streamlink", "deno"]
_MAC = sys.platform == "darwin"
HINTS = {
    "ffmpeg": "brew install ffmpeg" if _MAC else "winget install --id Gyan.FFmpeg -e",
    "ffprobe": "brew install ffmpeg" if _MAC else "winget install --id Gyan.FFmpeg -e",
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
        "h264_videotoolbox_listed": has(enc, "h264_videotoolbox"),
        "libx264": has(enc, "libx264"),
        "subtitles": has(flt, "subtitles"),
        "loudnorm": has(flt, "loudnorm"),
    }


def parse_ssim(text: str) -> float | None:
    """Liest den All-Wert aus der ffmpeg-SSIM-Zeile."""
    match = re.search(r"All:([0-9]+(?:\.[0-9]+)?)", text or "")
    if not match:
        return None
    return float(match.group(1))


def pick_winner(system: str, trials: dict) -> dict:
    """Wählt den Encoder aus schon gemessenen Läufen.

    Windows bleibt bei h264_amf, wenn der Lauf geklappt hat. macOS nimmt
    h264_videotoolbox nur, wenn er libx264 ähnlich ist (SSIM mindestens 0.99)
    und schneller war. Jedes andere System bleibt bei libx264.
    """
    amf = trials.get("amf") or {}
    vt = trials.get("videotoolbox") or {}
    x264 = trials.get("x264") or {}
    if system == "Windows":
        if amf.get("ok"):
            return {"amf_ok": True, "chosen": "amf", "detail": amf.get("detail") or "amf", "host": system}
        return {"amf_ok": False, "chosen": "x264", "host": system,
                "detail": amf.get("detail") or "h264_amf nicht nutzbar"}
    if system == "Darwin":
        if vt.get("ok") and x264.get("ok"):
            ssim = vt.get("ssim")
            faster = float(vt.get("seconds") or 1e9) < float(x264.get("seconds") or 1e9)
            if ssim is not None and ssim >= 0.99 and faster:
                return {"amf_ok": False, "chosen": "videotoolbox", "host": system,
                        "detail": (f"videotoolbox {vt['seconds']:.2f}s, SSIM {ssim:.3f} gegen libx264 "
                                   f"{x264['seconds']:.2f}s")}
            return {"amf_ok": False, "chosen": "x264", "host": system,
                    "detail": (f"libx264 bleibt (videotoolbox {vt.get('seconds')}s "
                               f"SSIM {ssim}, libx264 {x264.get('seconds')}s)")}
        if vt.get("ok") and not x264.get("ok"):
            return {"amf_ok": False, "chosen": "videotoolbox", "host": system,
                    "detail": vt.get("detail") or "libx264-Vergleich fehlgeschlagen"}
        return {"amf_ok": False, "chosen": "x264", "host": system,
                "detail": vt.get("detail") or "h264_videotoolbox nicht nutzbar"}
    return {"amf_ok": False, "chosen": "x264", "host": system,
            "detail": "kein macOS-Vergleich, libx264"}


def encode_file(ffmpeg: str, codec_args: list[str], dest: Path) -> dict:
    """Eine Sekunde 1080x1920. Die Datei bleibt für den SSIM-Vergleich liegen."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", "color=c=black:size=1080x1920:rate=30", "-t", "1",
           *codec_args, "-pix_fmt", "yuv420p", str(dest)]
    start = time.monotonic()
    try:
        proc = _lib.run(cmd, timeout=90)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "seconds": 0.0, "detail": f"{exc.__class__.__name__}: {exc}"}
    took = time.monotonic() - start
    if proc.returncode == 0 and dest.is_file():
        return {"ok": True, "seconds": took, "detail": f"ok in {took:.1f} s"}
    return {"ok": False, "seconds": took, "detail": (proc.stderr or "").strip()[-300:]}


def pair_ssim(ffmpeg: str, left: Path, right: Path) -> float | None:
    proc = _lib.run([ffmpeg, "-hide_banner", "-i", str(left), "-i", str(right),
                     "-lavfi", "ssim", "-f", "null", "-"], timeout=60)
    return parse_ssim((proc.stderr or "") + (proc.stdout or ""))


def amf_test(ffmpeg: str) -> tuple[bool, str]:
    """Gelistet heißt nicht nutzbar: echte 1-s-Kodierung in 1080x1920."""
    with tempfile.TemporaryDirectory(prefix="clips-amf-") as tmp:
        trial = encode_file(ffmpeg, ["-c:v", "h264_amf", "-quality", "quality"], Path(tmp) / "amf.mp4")
    return bool(trial["ok"]), trial["detail"]


def darwin_trials(ffmpeg: str, features: dict) -> dict:
    trials: dict = {}
    with tempfile.TemporaryDirectory(prefix="clips-enc-") as tmp:
        root = Path(tmp)
        if features.get("libx264"):
            trials["x264"] = encode_file(
                ffmpeg, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"], root / "x264.mp4")
        else:
            trials["x264"] = {"ok": False, "seconds": None, "detail": "libx264 nicht im Build"}
        if features.get("h264_videotoolbox_listed"):
            trials["videotoolbox"] = encode_file(
                ffmpeg, ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-allow_sw", "0"], root / "vt.mp4")
        else:
            trials["videotoolbox"] = {"ok": False, "seconds": None, "detail": "h264_videotoolbox nicht im Build"}
        if trials["videotoolbox"].get("ok") and trials["x264"].get("ok"):
            trials["videotoolbox"]["ssim"] = pair_ssim(ffmpeg, root / "vt.mp4", root / "x264.mp4")
    return trials


def whisper_status(system: str) -> dict:
    """Nur eine Notiz. transcription.backend wird nicht geändert.

    Zeiten von einem anderen Rechner werden nicht übernommen, damit eine
    Linux-Umgebung keine Mac-Zahl ausgibt.
    """
    present = []
    if _lib.find_tool("whisper-cli"):
        present.append("whisper-cpp")
    if importlib.util.find_spec("mlx_whisper") is not None:
        present.append("mlx-whisper")
    status = {"measured": False, "changes_backend": False, "backend_stays": "faster-whisper",
              "present": present}
    if system != "Darwin":
        status["reason"] = "Messung nur auf macOS, keine Zahl von diesem Rechner"
        return status
    sample = _lib.state_dir() / "whisper_sample.json"
    if not sample.is_file():
        status["reason"] = "auf macOS keine Probe in state/whisper_sample.json"
        return status
    try:
        data = _lib.read_json(sample)
    except (OSError, ValueError) as exc:
        status["reason"] = f"Probe unlesbar: {exc.__class__.__name__}"
        return status
    status.update({"measured": True, "sample": data,
                   "reason": "nur Messung, Backend bleibt faster-whisper"})
    return status


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
                        help="Encoder nicht per Test-Kodierung prüfen")
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
    system = platform.system()
    encoder = {"amf_ok": False, "chosen": "x264", "detail": "ffmpeg fehlt", "host": system}
    if tools["ffmpeg"]["path"]:
        features = ffmpeg_features(tools["ffmpeg"]["path"])
        if not features["subtitles"]:
            log.error("ffmpeg ohne libass (subtitles-Filter) -> Full-Build installieren")
            missing.append("ffmpeg:libass")
        if args.skip_encode_test:
            encoder = {"amf_ok": None, "chosen": "x264", "detail": "Test übersprungen", "host": system}
        elif system == "Windows":
            if features["h264_amf_listed"]:
                ok, detail = amf_test(tools["ffmpeg"]["path"])
            else:
                ok, detail = False, "h264_amf nicht im Build"
            encoder = pick_winner(system, {"amf": {"ok": ok, "detail": detail}})
        elif system == "Darwin":
            encoder = pick_winner(system, darwin_trials(tools["ffmpeg"]["path"], features))
        else:
            encoder = pick_winner(system, {})
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
        "whisper": whisper_status(system),
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
