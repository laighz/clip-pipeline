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

Ein Einstieg. Zwei Ordner neben dieser Datei:

| Ordner | Was |
|---|---|
| [clip-forge](clip-forge) | Schneiden. Skill und Skripte in `clip-forge/skills/clipping-streams` |
| [content-syndicator](content-syndicator) | Posten. Liest `~/clips/out/<vod-id>/clipXX.mp4` und die `clipXX.txt` daneben |

## Pfade

`<root>` ist der Ordner dieser Datei:

- macOS (Moritz' Mac): `/Users/moritz/Documents/#code/clip-pipeline`
- Windows: `C:\Users\morit\Documents\.code\clip-pipeline`

`<skill>` ist `<root>/clip-forge/skills/clipping-streams`. Wo in der Unter-SKILL.md
`${CLAUDE_SKILL_DIR}` steht, ist genau dieser Pfad gemeint. Ist die Variable leer
(z.B. in Hermes), den absoluten Pfad einsetzen. Pfade immer in Anführungszeichen,
weil `#code` sonst in der Shell als Kommentar gelesen wird.

## Ablauf

1. Lies `<skill>/SKILL.md` und folge ihr bis `log_clip`. Keine eigenen ffmpeg- oder yt-dlp-Befehle. Die harten Regeln dort gelten, außer der Satz zum Selbst-Posten: gepostet wird nur über content-syndicator.
2. Übergib Pfad, Hook, Titel und den Hinweis auf `clipXX.txt`.
3. Posten nur, wenn der User es in dieser Unterhaltung ausdrücklich will. Sonst den Befehl nur zeigen.

Skripte zum Schneiden:

```bash
uv run --quiet "<skill>/scripts/<name>.py" ...
```

Posten, im Ordner content-syndicator. Der erste Aufruf zeigt nur die Texte aus der TXT. `--publish` lädt hoch.

```bash
cd "<root>/content-syndicator"
uv run scripts/post_clips.py --vod-id <vod-id>
uv run scripts/post_clips.py --vod-id <vod-id> --publish
```

Browser einmal anmelden. `login.py` startet Google Chrome (sonst Edge oder Brave, oder `CLIPS_BROWSER`) ganz normal, ohne Automatisierung, mit dem eigenen Profil unter `~/clips/state/browser` und je einem Tab pro Plattform. Der User meldet sich selbst an und beendet den Browser mit Cmd+Q. Keine Passwörter erfragen oder eintippen. Playwright-Chromium nicht zum Anmelden nehmen: Google lehnt dort ab („Dieser Browser ist unter Umständen nicht sicher“).

```bash
cd "<root>/content-syndicator"
uv run scripts/login.py            # Fenster zum Anmelden, wartet bis Cmd+Q
uv run scripts/login.py --check    # ohne Fenster: Exit 0 = alle angemeldet, 5 = "missing" nennt die fehlenden
```

Vor jedem `--publish` erst `--check`. Fehlt eine Plattform, dem User sagen, dass er sich am Mac mit `login.py` anmelden muss, und diese Plattform per `--platforms` weglassen. Über WhatsApp geht die Anmeldung nicht.

YouTube, TikTok und Instagram bekommen den Text aus ihrem Block in `clipXX.txt`. X und Snapchat bekommen den TikTok-Text, gekürzt. Das Voice-over-Skript wird nicht gepostet und nicht ins Video gemischt.

## Wenn post_clips.py scheitert (Stand Nov 2026)

Per Hand posten nur, wenn der User es ausdrücklich erlaubt. Dann Chrome mit dem Profil als Hintergrundprozess starten (`--user-data-dir=~/clips/state/browser --remote-debugging-port=9333`), per Playwright `connect_over_cdp` bedienen und nach jedem Schritt einen Screenshot ansehen.

- TikTok Studio: alten Entwurf mit „Discard“ verwerfen, Modal „automatic content checks“ mit Cancel und „Got it“ schließen. Danach greift `div[contenteditable]`. Post-Button: `button[data-e2e=post_video_button]`. „Only me / under review“ direkt nach dem Posten ist normal und wechselt danach zu Everyone.
- Instagram: neues UI, der Create-Button ist `svg[aria-label="Post"]`. Reels-Hinweis mit OK bestätigen, Crop auf „Original“, 2× Next, Hashtag-Dropdown durch Klick auf den Kontonamen schließen, dann Share.
- YouTube: `post_clips.py` meldet „posted“, legt die Shorts aber nur als privaten Entwurf ohne Titel an. Immer im Studio prüfen: Content → Tab *Shorts* (der Tab *Videos* zeigt keine Shorts). Entwurf öffnen, Titel (`#title-textarea #textbox`) und Beschreibung setzen, „Not made for kids“ wählen, 3× `#next-button`, `PUBLIC`, dann `#done-button`. Danach muss „Video published“ erscheinen.
- X: Klick auf `tweetButton` im Dialog funktioniert. Danach kann das Modal „Unlock more on X“ (graduated-access) kommen, der Post ist trotzdem raus. Video-Posts erscheinen erst nach der Verarbeitung (1–2 min) im Profil: vor einem zweiten Versuch mindestens 2 min warten, sonst entsteht ein Duplikat. Am Profil prüfen.
- Läuft Chrome per CDP mit dem Profil, scheitern `login.py --check` und `post_clips.py` („Profil ist schon in einem offenen Fenster in Benutzung“). Dann YouTube auch per Hand: `studio.youtube.com/channel/<id>/videos/upload?d=ud` öffnet direkt den Upload-Dialog.
- Snapchat (profile.snapchat.com → „Post to Snapchat“ → Spotlight): scheitert mit `PERMISSION_DENIED: Organization not spend ready`. Das ist eine Konto-/Business-Einstellung, die der User selbst klären muss.
- `references/platform-rules.md` fehlt in clip-forge. Captions nach dem Schema in clips.json schreiben.

## Über Messenger (WhatsApp, Telegram, …)

Läuft die Session über ein Gateway, sieht der User kein Terminal:

- Kurz Bescheid geben, bevor lange Schritte (Download, Transkription, Rendern) starten.
- Fertige Clips als Datei zurückschicken: `MEDIA:/absoluter/pfad/clipXX.mp4`, dazu Hook und Titel als Text.
- Die Browser-Anmeldung fürs Posten geht nur am Rechner selbst. Ist ein Konto nicht angemeldet, das sagen und nicht weiter versuchen.

## Einbinden

Claude, ein Skill namens `clip-pipeline`:

```bash
ln -s "/Users/moritz/Documents/#code/clip-pipeline" ~/.claude/skills/clip-pipeline
```

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\clip-pipeline" -Target "C:\Users\morit\Documents\.code\clip-pipeline"
```

Hermes, derselbe Ordner. Danach neue Session oder `/reset`. Slash-Befehl: `/clip-pipeline`.

```bash
mkdir -p ~/.hermes/skills/video
ln -s "/Users/moritz/Documents/#code/clip-pipeline" ~/.hermes/skills/video/clip-pipeline
```

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.hermes\skills\video"
New-Item -ItemType Junction -Path "$HOME\.hermes\skills\video\clip-pipeline" -Target "C:\Users\morit\Documents\.code\clip-pipeline"
```
