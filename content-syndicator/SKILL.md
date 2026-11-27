---
name: content-syndicator
description: >-
  Use when posting finished clips to YouTube, TikTok, Instagram, Snapchat, or X.
metadata:
  author: Moritz
  tags: [clips, posting, youtube, tiktok]
allowed-tools: Bash(uv run *) PowerShell(uv run *) Read Write WebFetch env
---

# Content-Syndicator

## Purpose
Fertige Clips aus `~/clips/out/<vod-id>/` auf YouTube, TikTok, Instagram, Snapchat und X posten. Der Schnitt liegt bei clipping-streams. Dieser Skill schreibt keine neue Caption und mischt kein Voice-over ins Video.

## Prerequisites
uv und ein einmal installiertes Playwright-Chromium (`uv run --with playwright python -m playwright install chromium`). Die Anmeldung macht der User im Fenster. Keine Passwörter erfragen oder eintippen. Das Profil liegt unter `~/clips/state/browser`. Datenordner ist `~/clips`, sonst `CLIPS_HOME` oder `--home`.

## Instructions
Für Twitch-Clips aus `clip-pipeline` gilt die [Posting-Referenz der Pipeline](../live-ops/skills/clip-selection-and-posting/references/posting.md). Die folgenden Befehle sind für separate Aufträge mit Content-Syndicator, nicht als Fallback für diese Pipeline.

Jedes Skript über run_script starten, im Ordner dieser Datei. Der erste Aufruf zeigt nur die Texte aus `clipXX.txt`. `--publish` lädt hoch. Vor jedem `--publish` erst `login.py --check`. Fehlt eine Plattform, das sagen und sie per `--platforms` weglassen.

YouTube, TikTok und Instagram bekommen den Text aus ihrem Block. X und Snapchat bekommen den TikTok-Text, gekürzt.

## Available Scripts
| Script | Purpose | Arguments |
| --- | --- | --- |
| login.py | Fenster zum Anmelden, oder nur prüfen | `--check` |
| post_clips.py | Texte zeigen oder hochladen | `--vod-id`, optional `--publish`, `--clip`, `--platforms` |

## Examples
```bash
uv run scripts/login.py --check
uv run scripts/post_clips.py --vod-id <vod-id>
uv run scripts/post_clips.py --vod-id <vod-id> --publish
```

## Limitations
Kein Schneiden und keine Live-Aufnahme. Snapchat Spotlight nur bis 60 Sekunden. Das Voice-over-Skript unter `--- KOMMENTAR-SKRIPT ---` wird nicht gepostet.

## Troubleshooting
Ein error oder fail einer Plattform steht in `clipXX.post.json`. Diese Seite auslassen, die anderen weiterlaufen lassen. Login- oder Captcha-Fenster nicht schließen, bis der User sich selbst angemeldet hat. Mehr steht in [README.md](README.md).
