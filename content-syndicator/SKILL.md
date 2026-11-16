---
name: clip-pipeline
description: >-
  Ein Skill für den ganzen Weg. Findet Highlights in Twitch-VODs, eigenen
  Aufnahmen und, nur auf ausdrücklichen Wunsch, in einem laufenden
  Twitch-Stream, schneidet daraus vertikale Kurzclips und postet sie auf
  YouTube, TikTok, Instagram, Snapchat und X. Nutzen, sobald der User Clips,
  Highlights, Shorts, Reels, TikToks oder das Posten eines fertigen Clips will.
  Auch auf Englisch und ohne die Wörter Skill oder ffmpeg. Live-Aufnahme nur
  auf ausdrücklichen Wunsch. Nicht zum bloßen Kürzen eines einzelnen fertigen
  Videos und nicht für YouTube- oder Kick-VODs.
allowed-tools: Bash(uv run *) PowerShell(uv run *)
---

# Clip-Pipeline

Ein Einstieg. Zwei Ordner unter `C:\Users\morit\Documents\.code\clip-pipeline`:

| Ordner | Was |
|---|---|
| [clip-forge](clip-forge) | Schneiden. Skill und Skripte in `clip-forge\skills\clipping-streams` |
| [content-syndicator](content-syndicator) | Posten. Liest `~/clips/out/<vod-id>/clipXX.mp4` und die `clipXX.txt` daneben |

Diese Datei liegt eine Ebene darüber, in `clip-pipeline\SKILL.md`. Von hier aus sind beide Links Nachbarordner.

## Ablauf

1. Lies `clip-forge\skills\clipping-streams\SKILL.md` und folge ihr bis `log_clip`. Keine eigenen ffmpeg- oder yt-dlp-Befehle. Die harten Regeln dort gelten, außer der Satz zum Selbst-Posten: gepostet wird nur über content-syndicator.
2. Übergib Pfad, Hook, Titel und den Hinweis auf `clipXX.txt`.
3. Posten nur, wenn der User es in dieser Unterhaltung ausdrücklich will. Sonst den Befehl nur zeigen.

Skripte zum Schneiden, `<skill>` ist `C:\Users\morit\Documents\.code\clip-pipeline\clip-forge\skills\clipping-streams`:

```powershell
uv run --quiet "<skill>\scripts\<name>.py" ...
```

Posten, im Ordner content-syndicator. Der erste Aufruf zeigt nur die Texte aus der TXT. `--publish` lädt hoch.

```powershell
cd C:\Users\morit\Documents\.code\clip-pipeline\content-syndicator
uv run scripts/post_clips.py --vod-id <vod-id>
uv run scripts/post_clips.py --vod-id <vod-id> --publish
```

Chromium einmal: `uv run --with playwright python -m playwright install chromium`. Die Anmeldung macht der User im Fenster. Keine Passwörter erfragen oder eintippen. Das Profil liegt unter `~/clips/state/browser`.

YouTube, TikTok und Instagram bekommen den Text aus ihrem Block in `clipXX.txt`. X und Snapchat bekommen den TikTok-Text, gekürzt. Das Voice-over-Skript wird nicht gepostet und nicht ins Video gemischt.

## Einbinden

Claude, ein Skill namens `clip-pipeline`:

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\clip-pipeline" -Target "C:\Users\morit\Documents\.code\clip-pipeline"
```

Hermes, derselbe Ordner. Danach neue Session oder `/reset`. Slash-Befehl: `/clip-pipeline`.

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.hermes\skills\video"
New-Item -ItemType Junction -Path "$HOME\.hermes\skills\video\clip-pipeline" -Target "C:\Users\morit\Documents\.code\clip-pipeline"
```

Diese Datei muss dafür in `clip-pipeline\SKILL.md` liegen, nicht nur in `content-syndicator`. Liegt sie noch im Repo, eine Ebene nach oben kopieren:

```powershell
Copy-Item C:\Users\morit\Documents\.code\clip-pipeline\content-syndicator\SKILL.md C:\Users\morit\Documents\.code\clip-pipeline\SKILL.md
```
