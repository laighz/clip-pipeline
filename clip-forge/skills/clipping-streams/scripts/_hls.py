"""Direkter HLS-Download für Twitch-VODs, auch für VODs, die noch live laufen.

Warum: yt-dlp hängt bei laufenden VODs (Playlist ohne ENDLIST, wächst weiter) oft ohne Fehler.
Hier wird die Playlist einmal gelesen und die Segmente werden parallel per HTTP geholt.
Nur Standardbibliothek + ffmpeg. Aufrufer fangen HlsError ab und fallen auf yt-dlp zurück.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import _https
import _lib

WORKERS = 16
UA = {"User-Agent": "Mozilla/5.0"}


class HlsError(RuntimeError):
    pass


def playlist_url(ytdlp: str, url: str, fmt: str) -> str:
    """Playlist-URL der gewünschten Qualität (yt-dlp -g, lädt nichts)."""
    proc = _lib.run([ytdlp, "-g", "-f", fmt, "--no-playlist", url], timeout=90)
    lines = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip().startswith("http")]
    if proc.returncode != 0 or not lines:
        raise HlsError(f"keine Playlist-URL für {fmt}: {(proc.stderr or '').strip()[-200:]}")
    return lines[0]


def _get(url: str, timeout: int = 60, tries: int = 4) -> bytes:
    last: Exception | None = None
    for i in range(tries):
        try:
            return _https.read_https(url, headers=UA, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - Netzfehler jeder Art -> erneut versuchen
            last = e
            time.sleep(1 + i)
    raise HlsError(f"Download fehlgeschlagen: {url[-60:]} ({last})")


def parse(pl_url: str) -> list[tuple[str, float]]:
    """[(segment_url, dauer_s)] in Reihenfolge. Funktioniert mit und ohne ENDLIST."""
    text = _get(pl_url).decode("utf-8", "replace")
    segs: list[tuple[str, float]] = []
    dur = 0.0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line and not line.startswith("#"):
            segs.append((urljoin(pl_url, line), dur))
    if not segs:
        raise HlsError("Playlist ohne Segmente")
    return segs


def _fetch_all(segs: list[tuple[str, float]], tmp: Path) -> list[Path]:
    files = [tmp / f"{i:06d}.ts" for i in range(len(segs))]

    def one(i: int) -> None:
        files[i].write_bytes(_get(segs[i][0]))

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for f in [ex.submit(one, i) for i in range(len(segs))]:
            f.result()
    return files


def _concat(files: list[Path], out_ts: Path) -> None:
    with out_ts.open("wb") as o:
        for f in files:
            with f.open("rb") as i:
                shutil.copyfileobj(i, o)


def download_audio(ytdlp: str, ffmpeg: str, url: str, out: Path) -> Path:
    """Komplette Tonspur (VOD bis jetzt) als mono AAC .mp4."""
    pl = playlist_url(ytdlp, url, "Audio_Only/ba/worst")
    segs = parse(pl)
    _lib.log.info("HLS: %d Segmente (%.0f min), parallel ...", len(segs), sum(d for _, d in segs) / 60)
    with tempfile.TemporaryDirectory(prefix="hls_") as t:
        tmp = Path(t)
        files = _fetch_all(segs, tmp)
        ts = tmp / "all.ts"
        _concat(files, ts)
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = _lib.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(ts), "-vn",
                         "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out)], timeout=1800)
        if proc.returncode != 0 or not out.is_file():
            raise HlsError(f"ffmpeg: {(proc.stderr or '').strip()[-200:]}")
    return out


def download_section(ytdlp: str, ffmpeg: str, url: str, start: float, end: float, height: int, out: Path) -> Path:
    """Abschnitt [start, end] s als mp4, beginnt exakt bei `start` (Neu-Kodierung, 112 s sind schnell)."""
    fmt = f"bv*[height<={height}]+ba/b[height<={height}]/b"
    proc = _lib.run([ytdlp, "-g", "-f", fmt, "--no-playlist", url], timeout=90)
    urls = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip().startswith("http")]
    if proc.returncode != 0 or not urls:
        raise HlsError(f"keine Playlist-URLs: {(proc.stderr or '').strip()[-200:]}")
    with tempfile.TemporaryDirectory(prefix="hls_") as t:
        tmp = Path(t)
        parts: list[Path] = []
        first_start = 0.0
        for k, u in enumerate(urls):
            segs = parse(u)
            acc, pick, first = 0.0, [], None
            for su, d in segs:
                if acc + d > start and acc < end:
                    pick.append((su, d))
                    first = acc if first is None else first
                acc += d
            if not pick:
                raise HlsError(f"{start}-{end} s liegt außerhalb des VODs ({acc:.0f} s)")
            first_start = first or 0.0
            d_dir = tmp / f"p{k}"
            d_dir.mkdir()
            files = _fetch_all(pick, d_dir)
            ts = tmp / f"p{k}.ts"
            _concat(files, ts)
            parts.append(ts)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for p in parts:
            cmd += ["-ss", f"{max(start - first_start, 0):.3f}", "-i", str(p)]
        cmd += ["-t", f"{end - start:.3f}"]
        if len(parts) == 2:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out)]
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = _lib.run(cmd, timeout=900)
        if proc.returncode != 0 or not out.is_file():
            raise HlsError(f"ffmpeg: {(proc.stderr or '').strip()[-200:]}")
    return out
