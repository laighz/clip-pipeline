---
name: clip-pipeline
description: >-
  Use when turning Twitch VODs into vertical Shorts. Not for a single trim.
metadata:
  author: Moritz
  tags: [twitch, clips, shorts, video]
allowed-tools: Bash(uv run *) PowerShell(uv run *) Read Write WebFetch env
---

# Clip-Pipeline

## Purpose
Ein Einstieg für den ganzen Weg: Highlights in Twitch-VODs, eigenen Aufnahmen und, nur auf ausdrücklichen Wunsch, in einem laufenden Twitch-Stream finden, daraus vertikale Kurzclips schneiden und sie auf YouTube, TikTok, Instagram, Snapchat und X posten. Auch auf Englisch und ohne die Wörter Skill oder ffmpeg. Live-Aufnahme nur auf ausdrücklichen Wunsch. Nicht zum bloßen Kürzen eines einzelnen fertigen Videos und nicht für YouTube- oder Kick-VODs.

## Prerequisites
uv und ffmpeg. Die benötigten Skripte und Voraussetzungen stehen in den verlinkten Referenzen.

## Instructions
`<root>` ist der Ordner dieser Datei. Pfade mit `#` in Anführungszeichen setzen.

Nur die Referenz lesen, die zum Auftrag passt:

- [Aufnahme und Live-Modus](live-ops/skills/clip-selection-and-posting/references/mode.md)
- [Highlights suchen und bewerten](live-ops/skills/clip-selection-and-posting/references/selection.md)
- [Schneiden, rendern und validieren](clip-forge/skills/clipping-streams/SKILL.md): dem Workflow bis `log_clip` folgen; nach dem Rendern den Post-Schritt separat behandeln.
- [Veröffentlichen und Veröffentlichung prüfen](live-ops/skills/clip-selection-and-posting/references/posting.md)
- [Speicher aufräumen und Janitor prüfen](live-ops/skills/clip-selection-and-posting/references/cleanup.md)
- [Ergebnisse über WhatsApp und `/status`](live-ops/skills/clip-selection-and-posting/references/communication.md)
- [Änderungen versionieren](live-ops/skills/clip-selection-and-posting/references/versioning.md)

Bei einer Übergabe [STATUS.md](STATUS.md) lesen. Sie hält nur offene Punkte fest; für Betriebsregeln, Veröffentlichungen und Verlauf gelten die dort verlinkten Quellen.

## GTA-6-Lauf

Nur wenn `live-ops/live/game_clip_loop.sh` läuft. Alle zwei Stunden sucht `fetch_game_clips.py` die meistgesehenen Twitch-Clips von Grand Theft Auto VI, die zwischen 3 und 24 Stunden alt sind. Grand Theft Auto V stoppt den Lauf. Schnitt, Facecam, Captions und Prüfung bleiben die Skripte von clipping-streams. Gepostet wird höchstens ein Clip, nur TikTok und Instagram; der Veröffentlichungsweg steht unter [Posten](live-ops/skills/clip-selection-and-posting/references/posting.md).

Um 13:00 Europe/Berlin schreibt `live-ops/live/warmup.sh` den Kontostand. In den ersten 14 Tagen nach `warmup_since` gilt ein engeres Tageslimit. Likes, Kommentare und Folgen macht der User.

## Einbinden

`<root>` ist der Ordner dieser Datei. Claude, ein Skill namens `clip-pipeline`:

```bash
ln -s "<root>" ~/.claude/skills/clip-pipeline
```

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\clip-pipeline" -Target "<root>"
```

Hermes, derselbe Ordner. Danach neue Session oder `/reset`. Slash-Befehl: `/clip-pipeline`.

```bash
mkdir -p ~/.hermes/skills/video
ln -s "<root>" ~/.hermes/skills/video/clip-pipeline
```

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.hermes\skills\video"
New-Item -ItemType Junction -Path "$HOME\.hermes\skills\video\clip-pipeline" -Target "<root>"
```
