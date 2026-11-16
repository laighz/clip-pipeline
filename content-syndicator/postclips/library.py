"""Welche Clips noch rausgehen, und was davon schon gepostet wurde."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from postclips.text import platform_texts, read_caption_file

PLATFORM_ORDER = ["youtube", "tiktok", "instagram", "snapchat", "x"]
SNAPCHAT_MAX_S = 60


class LoginRequired(RuntimeError):
    """Die Seite zeigt ein Login. Das Skript tippt kein Passwort ein."""


@dataclass
class Clip:
    vod_id: str
    clip_id: str
    video: Path
    sidecar_path: Path
    texts: dict[str, str]
    duration_s: float | None
    db_path: Path | None = None


@dataclass
class Job:
    clip_id: str
    platform: str
    action: str
    reason: str
    video: Path
    texts: dict[str, str]
    vod_id: str
    sidecar_path: Path
    db_path: Path | None = None


@dataclass
class RunResult:
    failed: int = 0
    posted: int = 0
    skipped: int = 0
    jobs: list[dict] = field(default_factory=list)


def discover_clips(home: Path, vod_id: str, only: set[str] | None = None) -> list[Clip]:
    out = home / "out" / vod_id
    if not out.is_dir():
        raise ValueError(f"{out} fehlt.")
    db = home / "clips.db"
    clips: list[Clip] = []
    for video in sorted(out.glob("clip*.mp4")):
        clip_id = video.stem
        if only and clip_id not in only:
            continue
        caption = video.with_suffix(".txt")
        if not caption.is_file():
            raise ValueError(f"{caption.name} fehlt neben {video.name}.")
        raw = read_caption_file(caption.read_text(encoding="utf-8"))
        clips.append(Clip(
            vod_id=vod_id,
            clip_id=clip_id,
            video=video,
            sidecar_path=out / f"{clip_id}.post.json",
            texts=platform_texts(raw),
            duration_s=_duration(video.with_suffix(".json")),
            db_path=db,
        ))
    return clips


def plan_posts(clips: list[Clip], platforms: list[str]) -> list[Job]:
    jobs: list[Job] = []
    for clip in clips:
        for platform in platforms:
            action, reason = "post", ""
            if platform == "snapchat" and clip.duration_s is not None and clip.duration_s > SNAPCHAT_MAX_S:
                action = "skip"
                reason = f"Snapchat Spotlight nur bis {SNAPCHAT_MAX_S} s, der Clip hat {clip.duration_s:g} s."
            jobs.append(Job(
                clip_id=clip.clip_id,
                platform=platform,
                action=action,
                reason=reason,
                video=clip.video,
                texts=clip.texts,
                vod_id=clip.vod_id,
                sidecar_path=clip.sidecar_path,
                db_path=clip.db_path,
            ))
    return jobs


def run_posts(clips: list[Clip], platforms: list[str], poster) -> RunResult:
    """Poster wird nur für noch offene Plattformen aufgerufen. Keine Pause dazwischen."""
    result = RunResult()
    for clip in clips:
        side = load_sidecar(clip.sidecar_path)
        side.setdefault("vod_id", clip.vod_id)
        side.setdefault("clip_id", clip.clip_id)
        posts: dict = side.setdefault("posts", {})
        for job in plan_posts([clip], platforms):
            previous = posts.get(job.platform) or {}
            if previous.get("status") in {"posted", "skipped"}:
                result.skipped += 1
                result.jobs.append(_row(job, previous.get("status", "skipped"), previous.get("error", "")))
                continue
            if job.action == "skip":
                posts[job.platform] = {"status": "skipped", "error": job.reason}
                result.skipped += 1
                result.jobs.append(_row(job, "skipped", job.reason))
                _persist(job, "skipped", job.reason, side)
                continue
            try:
                poster(job)
            except LoginRequired:
                posts[job.platform] = {"status": "failed", "error": "Anmeldung fehlt."}
                _persist(job, "failed", "Anmeldung fehlt.", side)
                raise
            except Exception as exc:
                message = str(exc)
                posts[job.platform] = {"status": "failed", "error": message}
                result.failed += 1
                result.jobs.append(_row(job, "failed", message))
                _persist(job, "failed", message, side)
                continue
            posts[job.platform] = {"status": "posted", "error": ""}
            result.posted += 1
            result.jobs.append(_row(job, "posted", ""))
            _persist(job, "posted", "", side)
    return result


def load_sidecar(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_sidecar(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_db(path: Path, vod_id: str, clip_id: str, platform: str, status: str, detail: str) -> None:
    """Ergänzt clips.db, wenn die Pipeline sie schon angelegt hat. Legt keine neue Datei an."""
    if not path.is_file():
        return
    con = sqlite3.connect(path)
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS posts (
                vod_id TEXT, clip_id TEXT, platform TEXT, status TEXT, detail TEXT, posted_at TEXT,
                PRIMARY KEY (vod_id, clip_id, platform))"""
        )
        con.execute(
            "INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?)",
            (vod_id, clip_id, platform, status, detail, time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        con.commit()
    finally:
        con.close()


def _duration(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "duration_s" not in data:
        return None
    try:
        return float(data["duration_s"])
    except (TypeError, ValueError):
        return None


def _persist(job: Job, status: str, detail: str, side: dict) -> None:
    save_sidecar(job.sidecar_path, side)
    if job.db_path is not None:
        record_db(job.db_path, job.vod_id, job.clip_id, job.platform, status, detail)


def _row(job: Job, status: str, detail: str) -> dict:
    return {
        "clip_id": job.clip_id,
        "platform": job.platform,
        "status": status,
        "detail": detail,
        "video": str(job.video),
    }
