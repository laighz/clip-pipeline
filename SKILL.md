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
uv, ffmpeg und die Skripte unter `<skill>`. Posten braucht eine einmalige Anmeldung im Browser. Keine Passwörter erfragen oder eintippen.

## Instructions
`<root>` ist der Ordner dieser Datei. `<skill>` ist `<root>/clip-forge/skills/clipping-streams`. Beide Namen sind Ordner, keine Shell-Variablen. Pfade mit `#` immer in Anführungszeichen setzen.

Zwei Ordner neben dieser Datei:

| Ordner | Was |
|---|---|
| [clip-forge](clip-forge/README.md) | Schneiden. Skill und Skripte in `clip-forge/skills/clipping-streams` |
| [content-syndicator](content-syndicator/SKILL.md) | Posten. Liest `~/clips/out/<vod-id>/clipXX.mp4` und die `clipXX.txt` daneben |

1. Lies `<skill>/SKILL.md` und folge ihr bis `log_clip`. Keine eigenen ffmpeg- oder yt-dlp-Befehle. Die harten Regeln dort gelten, außer der Satz zum Selbst-Posten: gepostet wird nur über content-syndicator.
2. Übergib Pfad, Hook, Titel und den Hinweis auf `clipXX.txt`.
3. Posten nur, wenn der User es in dieser Unterhaltung ausdrücklich will. Sonst den Befehl nur zeigen.

Skripte zum Schneiden, jedes über run_script:

```bash
uv run --quiet "<skill>/scripts/<name>.py" ...
```

Posten, im Ordner content-syndicator. Der erste Aufruf zeigt nur die Texte aus der TXT. `--publish` lädt hoch.

```bash
cd "<root>/content-syndicator"
uv run scripts/post_clips.py --vod-id <vod-id>
uv run scripts/post_clips.py --vod-id <vod-id> --publish
```

Browser einmal anmelden. `login.py` startet Google Chrome (sonst Edge oder Brave, oder `CLIPS_BROWSER`) ganz normal, ohne Automatisierung, mit dem eigenen Profil unter `~/clips/state/browser` und je einem Tab pro Plattform. Der User meldet sich selbst an und beendet den Browser mit Cmd+Q. Playwright-Chromium nicht zum Anmelden nehmen: Google lehnt dort ab („Dieser Browser ist unter Umständen nicht sicher“).

```bash
cd "<root>/content-syndicator"
uv run scripts/login.py            # Fenster zum Anmelden, wartet bis Cmd+Q
uv run scripts/login.py --check    # ohne Fenster: Exit 0 = alle angemeldet, 5 = "missing" nennt die fehlenden
```

Vor jedem `--publish` erst `--check`. Fehlt eine Plattform, dem User sagen, dass er sich am Rechner mit `login.py` anmelden muss, und diese Plattform per `--platforms` weglassen. Über WhatsApp geht die Anmeldung nicht.

YouTube, TikTok und Instagram bekommen den Text aus ihrem Block in `clipXX.txt`. X und Snapchat bekommen den TikTok-Text, gekürzt. Das Voice-over-Skript wird nicht gepostet und nicht ins Video gemischt.

## Examples
Schneiden, dann nur zeigen: `uv run scripts/post_clips.py --vod-id <vod-id>` im Ordner content-syndicator. Hochladen erst nach ausdrücklichem Wunsch mit `--publish`.

## Wenn post_clips.py scheitert (Stand Nov 2026)

Per Hand posten nur, wenn der User es ausdrücklich erlaubt. Dann Chrome mit dem Profil als Hintergrundprozess starten (`--user-data-dir=~/clips/state/browser --remote-debugging-port=9333`), per Playwright `connect_over_cdp` bedienen und nach jedem Schritt einen Screenshot ansehen.

- TikTok Studio: alten Entwurf mit „Discard“ verwerfen, Modal „automatic content checks“ mit Cancel und „Got it“ schließen. Danach greift `div[contenteditable]`. Post-Button: `button[data-e2e=post_video_button]`. „Only me / under review“ direkt nach dem Posten ist normal und wechselt danach zu Everyone.
- Instagram: neues UI, der Create-Button ist `svg[aria-label="Post"]`. Reels-Hinweis mit OK bestätigen, Crop auf „Original“, 2× Next, Hashtag-Dropdown durch Klick auf den Kontonamen schließen, dann Share.
- YouTube: `post_clips.py` meldet „posted“, legt die Shorts aber nur als privaten Entwurf ohne Titel an. Immer im Studio prüfen: Content → Tab *Shorts* (der Tab *Videos* zeigt keine Shorts). Entwurf öffnen, Titel (`#title-textarea #textbox`) und Beschreibung setzen, „Not made for kids“ wählen, 3× `#next-button`, `PUBLIC`, dann `#done-button`. Danach muss „Video published“ erscheinen.
- X: Klick auf `tweetButton` im Dialog funktioniert. Danach kann das Modal „Unlock more on X“ (graduated-access) kommen, der Post ist trotzdem raus. Video-Posts erscheinen erst nach der Verarbeitung (1–2 min) im Profil: vor einem zweiten Versuch mindestens 2 min warten, sonst entsteht ein Duplikat. Am Profil prüfen.
- Läuft Chrome per CDP mit dem Profil, scheitern `login.py --check` und `post_clips.py` („Profil ist schon in einem offenen Fenster in Benutzung“). Dann YouTube auch per Hand: `studio.youtube.com/channel/<id>/videos/upload?d=ud` öffnet direkt den Upload-Dialog.
- Snapchat (profile.snapchat.com → „Post to Snapchat“ → Spotlight): scheitert mit `PERMISSION_DENIED: Organization not spend ready`. Das ist eine Konto-/Business-Einstellung, die der User selbst klären muss.
- Captions nach dem Schema in clips.json schreiben.

## Troubleshooting
Ein error von `login.py --check` oder ein fail von `post_clips.py` stoppt diese Plattform. Die Meldung nennen, die Plattform weglassen, den Rest nicht raten. Validierung der Clips bleibt bei clipping-streams, bis dort Exit 0 kommt.

## Limitations
Live-Aufnahme nur auf ausdrücklichen Wunsch. Kein YouTube-VOD, kein Kick-VOD, kein bloßes Kürzen einer einzelnen Datei. Die Browser-Anmeldung geht nur am Rechner selbst.

## Über Messenger (WhatsApp, Telegram, …)

Läuft die Session über ein Gateway, sieht der User kein Terminal:

- Kurz Bescheid geben, bevor lange Schritte (Download, Transkription, Rendern) starten.
- Fertige Clips als Datei zurückschicken: `MEDIA:/absoluter/pfad/clipXX.mp4`, dazu Hook und Titel als Text.
- Die Browser-Anmeldung fürs Posten geht nur am Rechner selbst. Ist ein Konto nicht angemeldet, das sagen und nicht weiter versuchen.

## GTA-6-Lauf

Nur wenn `live-ops/live/game_clip_loop.sh` läuft. Alle zwei Stunden sucht `fetch_game_clips.py` die meistgesehenen Twitch-Clips von Grand Theft Auto VI, die zwischen 3 und 24 Stunden alt sind. Grand Theft Auto V stoppt den Lauf. Schnitt, Facecam, Captions und Prüfung bleiben die Skripte von clipping-streams. Gepostet wird höchstens ein Clip, nur TikTok und Instagram, über `post_all.sh` mit `SKIP=YT|X|SC`.

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
