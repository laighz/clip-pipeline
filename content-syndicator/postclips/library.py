"""Welche Clips noch rausgehen, und was davon schon gepostet wurde."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from postclips import music
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
    requires_trend_music: bool = False


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


@dataclass(frozen=True)
class UploadMusic:
    track: music.Track
    platforms: frozenset[str]
    video: Path | None

    def summary(self) -> dict:
        return {
            "title": self.track.title,
            "artist": self.track.artist,
            "source_platform": self.track.source_platform,
            "checked_on": self.track.checked_on.isoformat(),
        }


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
        metadata = _clip_metadata(video.with_suffix(".json"))
        clips.append(Clip(
            vod_id=vod_id,
            clip_id=clip_id,
            video=video,
            sidecar_path=out / f"{clip_id}.post.json",
            texts=platform_texts(raw),
            duration_s=_duration(metadata),
            db_path=db,
            requires_trend_music=metadata.get("trend_music_upload") is True,
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


def prepare_upload_music(clips: list[Clip], platforms: list[str], *,
                         cache_dir: Path | None = None) -> dict[Path, UploadMusic]:
    """Validate a track against unposted targets and mix once for each clip on publish."""
    prepared = {}
    for clip in clips:
        sidecar = clip.video.with_suffix(".music.json")
        if not clip.requires_trend_music and not sidecar.exists() and not sidecar.is_symlink():
            continue
        posts = load_sidecar(clip.sidecar_path).get("posts", {})
        pending = [job.platform for job in plan_posts([clip], platforms)
                   if job.action == "post" and not _finished(posts, job.platform)]
        if not pending:
            continue
        settings = music.read_settings(clip.video)
        if settings is None:
            raise ValueError(f"{sidecar.name} fehlt für den Trendmusik-Upload von {clip.video.name}.")
        track = music.select(settings, pending)
        mixed = None
        if cache_dir is not None:
            mixed = music.mix_for_upload(clip.video, track, cache_dir,
                                         master_sha256=settings["master_sha256"])
        prepared[clip.video] = UploadMusic(track, frozenset(pending), mixed)
    return prepared


def _finished(posts: dict, platform: str) -> bool:
    return (posts.get(platform) or {}).get("status") in {"posted", "skipped"}


def run_posts(clips: list[Clip], platforms: list[str], poster, *,
              upload_music: dict[Path, UploadMusic] | None = None) -> RunResult:
    """Poster wird nur für noch offene Plattformen aufgerufen. Keine Pause dazwischen."""
    result = RunResult()
    for clip in clips:
        side = load_sidecar(clip.sidecar_path)
        side.setdefault("vod_id", clip.vod_id)
        side.setdefault("clip_id", clip.clip_id)
        posts: dict = side.setdefault("posts", {})
        for job in plan_posts([clip], platforms):
            previous = posts.get(job.platform) or {}
            if _finished(posts, job.platform):
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
                prepared = (upload_music or {}).get(job.video)
                upload_job = job
                if clip.requires_trend_music and prepared is None:
                    raise ValueError(f"{job.platform}: Musikmix fehlt für {clip.video.name}.")
                if prepared is not None:
                    if prepared.video is None or job.platform not in prepared.platforms:
                        raise ValueError(f"{job.platform}: Musikmix fehlt für dieses Upload-Ziel.")
                    upload_job = replace(job, video=prepared.video)
                poster(upload_job)
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
            track_info = {"trend_music": prepared.summary()} if prepared else {}
            posts[job.platform] = {"status": "posted", "error": "", **track_info}
            result.posted += 1
            result.jobs.append({**_row(job, "posted", ""), **track_info})
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


def _clip_metadata(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _duration(data: dict) -> float | None:
    try:
        return float(data.get("duration_s"))
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
