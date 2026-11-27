#!/usr/bin/env python3
"""Ein Durchlauf: meistgesehenen GTA-VI-Clip schneiden und höchstens einmal posten.

TikTok und Instagram über post_all.sh (SKIP=YT|X|SC). YouTube, X und Snapchat
macht dieser Lauf nicht. Schlägt die Facecam oder die Prüfung fehl, bleibt der
Clip liegen und der nächste Kandidat ist dran, maximal drei pro Durchlauf.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "clip-forge" / "skills" / "clipping-streams" / "scripts"
WARM = Path(__file__).resolve().parent / "warmup.py"
POST = ROOT / "live-ops" / "posting" / "post_all.sh"


def live_dir() -> Path:
    return Path(os.environ.get("CLIPS_LIVE", os.path.expanduser("~/.hermes/cache/scratch/live")))


def clips_home() -> Path:
    return Path(os.environ.get("CLIPS_HOME", os.path.expanduser("~/clips"))).expanduser()


def run(cmd: list[str], timeout: int = 900) -> tuple[int, dict, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    data: dict = {}
    if lines:
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError:
            data = {"_raw": lines[-1][:400]}
    return proc.returncode, data, proc.stderr[-500:]


def uv_script(name: str, *args: str, timeout: int = 900) -> tuple[int, dict, str]:
    uv = shutil.which("uv") or "uv"
    return run([uv, "run", "--quiet", str(SCRIPTS / name), *args], timeout=timeout)


def allow(key: str, log_name: str) -> bool:
    code, _data, _err = run([sys.executable, str(WARM), "--allow", key, "--live", str(live_dir()),
                             "--log", str(live_dir() / log_name)])
    return code == 0


def discover(args: argparse.Namespace) -> tuple[int, dict, str]:
    cmd = [sys.executable, str(SCRIPTS / "fetch_game_clips.py"), "--blocklist", str(live_dir() / "blocklist.txt")]
    if args.fixture:
        cmd += ["--fixture", args.fixture]
    if args.now:
        cmd += ["--now", str(args.now)]
    if args.game:
        cmd += ["--game", args.game]
    return run(cmd)


def clip_download_url(url: str) -> str:
    """Nur eine https-URL von Twitch, neu zusammengesetzt, ohne Query und Zugangsdaten."""
    parsed = urlsplit(str(url).strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"clips.twitch.tv", "www.twitch.tv"}:
        raise ValueError("nur https-URLs von clips.twitch.tv")
    if parsed.username or parsed.password or not parsed.path or ".." in parsed.path:
        raise ValueError("Clip-URL ungültig")
    return "https://" + host + parsed.path


def download(url: str, dest: Path) -> None:
    safe = clip_download_url(url)
    root = clips_home().resolve()
    target = dest.resolve()
    if target != root and root not in target.parents:
        raise ValueError("Ziel liegt ausserhalb des Clip-Ordners")
    target.parent.mkdir(parents=True, exist_ok=True)
    ytdlp = shutil.which("yt-dlp") or "yt-dlp"
    proc = subprocess.run([ytdlp, "-f", "bv*+ba/b", "--no-playlist", "--no-progress",
                           "-o", str(target), safe], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError((proc.stderr or proc.stdout or "Download fehlgeschlagen")[-400:])


def write_source(vod_id: str, clip: dict) -> None:
    work = clips_home() / "work" / vod_id
    work.mkdir(parents=True, exist_ok=True)
    payload = {
        "twitch_clip_id": clip.get("id"),
        "url": clip.get("url"),
        "video_id": clip.get("video_id"),
        "vod_offset": clip.get("vod_offset"),
        "login": clip.get("login"),
        "game_id": clip.get("game_id"),
        "game_name": clip.get("game_name"),
        "views": clip.get("views"),
    }
    (work / "source.json").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def hold(vod_id: str, cand: str, reason: str) -> None:
    folder = live_dir()
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"decision": "hold", "vod_id": vod_id, "cand": cand, "reason": reason},
                      ensure_ascii=False)
    with (folder / "game_clip.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def cut(vod_id: str, media: Path, login: str) -> tuple[bool, str]:
    code, data, err = uv_script("fetch_signals.py", "--input", str(media), "--vod-id", vod_id,
                                "--streamer", login, "--no-chat", "--no-clips", timeout=900)
    if code != 0:
        return False, data.get("error") or err or "fetch_signals"
    for name, args, timeout in (
        ("detect_highlights.py", ("--vod-id", vod_id), 300),
        ("fetch_sections.py", ("--vod-id", vod_id, "--whole"), 300),
        ("transcribe.py", ("--vod-id", vod_id, "--language", "de"), 1800),
        ("plan_game_clip.py", ("--vod-id", vod_id), 120),
    ):
        code, data, err = uv_script(name, *args, timeout=timeout)
        if code != 0:
            return False, data.get("error") or err or name
    code, data, err = uv_script("detect_facecam.py", "--vod-id", vod_id, "--clip", "clip01",
                                "--write-clip", timeout=300)
    if code != 0:
        return False, data.get("error") or data.get("status") or "facecam unsicher"
    code, data, err = uv_script("validate_clips.py", "--vod-id", vod_id)
    if code != 0:
        return False, "; ".join(data.get("errors") or []) or data.get("error") or "validate"
    try:
        code, _data, review_err = uv_script("review_caption_emoji.py", "--vod-id", vod_id, "--clip", "clip01",
                                           "--media", str(media), timeout=145)
        if review_err:
            print(review_err.strip(), file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired):
        code = 1
    if code != 0:
        print(f"{vod_id}: kein gültiges Emoji-Review; rendere ohne Caption-Pop-outs", file=sys.stderr)
    code, data, err = uv_script("render_clip.py", "--vod-id", vod_id, "--clip", "clip01",
                                "--reviewed-emoji-only", timeout=1800)
    if code != 0:
        return False, data.get("error") or err or "render"
    code, data, err = uv_script("verify_clip.py", "--vod-id", vod_id, "--clip", "clip01", "--loudness",
                                timeout=300)
    if code != 0:
        return False, data.get("error") or err or "verify"
    code, data, err = uv_script("log_clip.py", "--vod-id", vod_id)
    if code != 0:
        return False, data.get("error") or err or "log_clip"
    return True, ""


def evaluate(vod_id: str, cand: str, views: float) -> dict:
    work = str(clips_home() / "work" / vod_id) + os.sep
    code, data, err = run([sys.executable, str(Path(__file__).resolve().parent / "evaluate.py"),
                           vod_id, "clip01", "--cand", cand, "--standalone", "8", "--payoff", "8",
                           "--relevance", "9", "--views", str(views or 0), "--work", work])
    if code != 0 and not data:
        return {"decision": "hold", "reasons": [err or "evaluate"]}
    return data


def publish(vod_id: str) -> tuple[int, str]:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "USER") if key in os.environ}
    env["SKIP"] = "YT|X|SC"
    proc = subprocess.run(["bash", str(POST), vod_id, "clip01"], capture_output=True, text=True,
                          timeout=7200, env=env)
    return proc.returncode, (proc.stdout or proc.stderr)[-500:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Einen GTA-VI-Clip suchen, schneiden, höchstens einmal posten.")
    parser.add_argument("--fixture", default="", help="GQL-JSON statt Netz")
    parser.add_argument("--now", type=float, default=0)
    parser.add_argument("--game", default="")
    parser.add_argument("--dry-run", action="store_true", help="nur die Kandidaten, kein Download und kein Post")
    parser.add_argument("--max", type=int, default=3)
    args = parser.parse_args(argv)
    live_dir().mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(WARM), "--write", "--live", str(live_dir())])
    if not args.dry_run and not allow("TT", "tt_posts.log") and not allow("IG", "ig_posts.log"):
        print(json.dumps({"ok": True, "decision": "skip", "reason": "Warmup-Limit auf TikTok und Instagram"}))
        return 0
    code, data, err = discover(args)
    if code != 0:
        print(json.dumps({"ok": False, "error": data.get("error") or err}, ensure_ascii=False))
        return code
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "candidates": data.get("candidates") or [],
                          "rejected": data.get("rejected") or []}, ensure_ascii=False))
        return 0
    tried = []
    for clip in (data.get("candidates") or [])[: args.max]:
        login = clip.get("login") or "unknown"
        vod_id = "gta-" + "".join(ch if ch.isalnum() else "-" for ch in str(clip.get("id")))[:40].strip("-")
        media = clips_home() / "raw" / vod_id / "source.mp4"
        try:
            download(str(clip.get("url")), media)
        except (OSError, RuntimeError) as exc:
            hold(vod_id, clip.get("id") or "", str(exc))
            tried.append({"id": clip.get("id"), "decision": "hold", "reason": str(exc)})
            continue
        write_source(vod_id, clip)
        ok, reason = cut(vod_id, media, login)
        if not ok:
            hold(vod_id, str(clip.get("id")), reason)
            tried.append({"id": clip.get("id"), "decision": "hold", "reason": reason})
            continue
        cand = "c0"
        cand_path = clips_home() / "work" / vod_id / "candidates.json"
        if cand_path.is_file():
            cands = json.loads(cand_path.read_text(encoding="utf-8")).get("candidates") or []
            if cands:
                cand = max(cands, key=lambda c: c.get("score") or 0).get("id") or cand
        gate = evaluate(vod_id, cand, float(clip.get("views") or 0))
        if gate.get("decision") != "post":
            hold(vod_id, cand, "; ".join(gate.get("reasons") or ["hold"]))
            tried.append({"id": clip.get("id"), "vod_id": vod_id, "decision": "hold",
                          "reason": gate.get("reasons")})
            continue
        post_code, post_out = publish(vod_id)
        print(json.dumps({"ok": post_code == 0, "decision": "post", "vod_id": vod_id,
                          "clip": clip.get("id"), "post": post_out}, ensure_ascii=False))
        return 0 if post_code == 0 else 1
    print(json.dumps({"ok": True, "decision": "hold", "tried": tried}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
