# clipping-streams

Ein Agent-Skill, der die besten Momente aus Twitch-Streams und langen Aufnahmen
findet und daraus vertikale Kurzclips (1080×1920) mit Wort-Untertiteln, Hook und
Captions für YouTube Shorts, TikTok und Instagram Reels schneidet.

| Weg | Wann | Was passiert |
|---|---|---|
| A | Twitch-VOD | Ton, Chat-Zählwerte, Audio-Peaks und Zuschauer-Clips. Video nur für die gewählten Fenster. |
| B | eigene Datei, auch ohne Chat | Dieselbe Wertung auf der Tonspur. Kurze Aufnahmen können als ein Abschnitt transkribiert werden. |
| C | laufender Twitch-Stream | Nur wenn du ausdrücklich aufnehmen lässt. streamlink schreibt 10-Minuten-Segmente, der Chat wird live nach Spikes gescannt, fertige Segmente werden schon während des Streams geschnitten. |

Es wird nichts gepostet. Die Ergebnisse landen in `~/clips/out/<vod>/`.

Die Wertung ist für VOD und Live dieselbe: robuster z-Score (Median/MAD) auf Chat
und Stimmband-Lautheit, konfigurierte Emotes, Zuschauer-Clips. Gemutete Stellen
und Musik-Kategorien fallen raus. Unter 8 Sekunden wird ein Clip verworfen. Ist
der Moment früher vorbei als die Wunschlänge, bleibt der Clip kurz.

## Aufbau

```
skills/clipping-streams/
  SKILL.md           Anleitung: Wege A, B und C
  scripts/           eine JSON-Zeile auf stdout, Exit-Codes 0–5
  references/        Config, ffmpeg, Plattformregeln, Recht, Störungen
  assets/            streamers.example.yaml, Untertitel-Stil
  evals/             Fälle für claude plugin eval (ohne die großen Videos)
  tests/             Offline-Tests mit synthetischem Material
```

## Installation

Der Ordner `skills/clipping-streams` muss direkt unter dem Skills-Verzeichnis liegen.

```bash
ln -s /pfad/zum/repo/skills/clipping-streams ~/.claude/skills/clipping-streams
```

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\clipping-streams" -Target "C:\pfad\zum\repo\skills\clipping-streams"
```

Mit der skills-CLI, wenn dieses Repo die Quelle ist: `npx skills@1.5.16 add <repo> -a claude-code`.

## Voraussetzungen

| Werkzeug | Wofür |
|---|---|
| [uv](https://docs.astral.sh/uv/) | startet die Skripte, Python 3.11 bis unter 3.14 |
| ffmpeg mit libass | Schnitt, 9:16, Untertitel, Lautheit |
| yt-dlp | Tonspur und Abschnitte von Twitch |
| TwitchDownloaderCLI | Chat-Replay eines VODs. Ohne ihn läuft der Audio-Modus, `check_env.py` meldet ihn aber als fehlend |
| streamlink | nur Weg C |

Optional: AMD-GPU (`h264_amf`), sonst libx264. `scripts/check_env.py` prüft die
Werkzeuge und schreibt `~/clips/state/env.json`.

Whisper (`faster-whisper`, Modell `small`) lädt beim ersten Transkribieren etwa
460 MB. Das ist der Grund für die Python-Obergrenze 3.14.

## Daten

Code und Daten sind getrennt, damit ein Update des Skills nichts überschreibt.

```
~/clips/
├── streamers.yaml
├── work/<vod-id>/     Signale, Kandidaten, Abschnitte, Transkripte, clips.json
├── out/<vod-id>/      fertige Clips
├── raw/  state/  logs/
└── clips.db
```

Vorlage: `skills/clipping-streams/assets/streamers.example.yaml`.

## Tests

```bash
uv run skills/clipping-streams/tests/run_tests.py
uv run skills/clipping-streams/tests/run_tests.py --only render_clip verify_clip
```

Die Tests schreiben in einen eigenen Ordner, nie nach `~/clips`. Sprache der
Testclips: unter Windows die Systemstimme, unter Linux `espeak-ng`.

## Recht

Keine Rechtsberatung. Der Skill prüft nicht, ob du fremdes Material verwenden
darfst. Hintergrund: `skills/clipping-streams/references/legal-checklist.md`.
