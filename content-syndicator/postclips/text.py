"""Captions stehen fertig in der TXT neben der MP4. Hier werden sie nur gelesen und gekürzt."""
from __future__ import annotations

import re

_HEADER = re.compile(r"^---\s*(.+?)\s*---\s*$")
_URL = re.compile(r"https?://\S+")


def read_caption_file(text: str) -> dict[str, str]:
    """Titel und die drei geschriebenen Captions. Das Voice-over-Skript bleibt draußen."""
    title = ""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    saw_header = False
    for line in text.splitlines():
        header = _HEADER.match(line.strip())
        if header:
            saw_header = True
            current = header.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is None and line.startswith("TITEL:"):
            title = line.split(":", 1)[1].strip()
            continue
        if current is not None:
            sections[current].append(line)
    if not saw_header:
        plain = text.strip()
        return {"title": plain, "youtube": plain, "tiktok": plain, "instagram": plain}

    def body(name: str) -> str:
        for key, lines in sections.items():
            if name in key:
                return "\n".join(lines).strip()
        return ""

    return {
        "title": title,
        "youtube": body("youtube"),
        "tiktok": body("tiktok"),
        "instagram": body("instagram"),
    }


def platform_texts(raw: dict[str, str]) -> dict[str, str]:
    """YouTube, TikTok und Instagram behalten ihren Text. X und Snapchat kürzen denselben Text."""
    youtube = raw.get("youtube") or ""
    tiktok = raw.get("tiktok") or ""
    instagram = raw.get("instagram") or ""
    title = (raw.get("title") or "").strip() or _first_line(youtube or tiktok or instagram)
    short = _one_line(_URL.sub(" ", tiktok or instagram or youtube or title))
    return {
        "youtube_title": truncate(title, 100),
        "youtube_description": youtube,
        "tiktok": tiktok,
        "instagram": instagram,
        "x": truncate(short, 280),
        "snapchat": truncate(short, 160),
    }


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        word = cut.rsplit(" ", 1)[0].strip()
        if len(word) >= limit // 2:
            return word
    return cut.strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
