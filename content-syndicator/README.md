# Cross-Poster für clipping-streams

Als einen Skill: [SKILL.md](SKILL.md) nach `C:\Users\morit\Documents\.code\clip-pipeline\SKILL.md` kopieren. Der Ordner `clip-pipeline` ist dann der einzige Skill, in Claude und in Hermes.

Die Pipeline schneidet die Clips und legt sie ab. Dieses Kommando postet sie danach auf YouTube, TikTok, Instagram, Snapchat und X.

Die Caption wird nicht neu geschrieben. Sie steht schon in `clipXX.txt` neben der MP4:

```
out/<vod-id>/clip01.mp4
out/<vod-id>/clip01.txt
```

YouTube nimmt die Zeile `TITEL:` und den Block `--- YOUTUBE ---`. TikTok und Instagram nehmen ihre eigenen Blöcke. X und Snapchat bekommen denselben TikTok-Text, nur gekürzt (280 bzw. 160 Zeichen) und ohne Links. Das Voice-over-Skript unter `--- KOMMENTAR-SKRIPT ---` wird nicht gepostet.

Es gibt kein Abo und keinen Monatsdeckel. Zwischen den Clips wird nicht gewartet.

## Einmal anmelden

Das Skript öffnet Chromium mit einem Profil unter `~/clips/state/browser`. Du loggst dich dort selbst bei den fünf Seiten ein. Passwörter werden nicht gelesen und nicht eingetippt. Ein Agent, der das Kommando startet, braucht sie auch nicht.

Beim nächsten Lauf sitzen die Anmeldungen im Profil. Erscheint trotzdem ein Login oder ein Captcha, bleibt das Fenster offen.

## Start

Datenordner ist `~/clips`, bei dir `C:\Users\morit\clips`. Sonst `CLIPS_HOME` oder `--home`.

```bash
uv run --with playwright python -m playwright install chromium
uv run scripts/post_clips.py --vod-id 2929727033
uv run scripts/post_clips.py --vod-id 2929727033 --publish
```

Der erste Aufruf zeigt nur, was rausgehen würde, und schreibt nichts. `--publish` lädt hoch. Schon gepostete Plattformen stehen in `clipXX.post.json` und werden übersprungen. Eine Seite, deren Layout sich geändert hat, wird als Fehler notiert, die anderen laufen weiter.

```bash
uv run scripts/post_clips.py --vod-id 2929727033 --publish --clip clip01
uv run scripts/post_clips.py --vod-id 2929727033 --publish --platforms youtube,tiktok,instagram,x
```

Snapchat Spotlight nimmt nur Videos bis 60 Sekunden. Länger steht das in der JSON der Pipeline (`duration_s`), dann bleibt Snapchat aus.

Liegt `clips.db` schon im Datenordner, kommt eine Tabelle `posts` dazu. Die Datei wird nicht neu angelegt.

## Was schiefgehen kann

Die Upload-Seiten ändern ihre Buttons. Dann bricht diese eine Plattform ab, der Text in `clipXX.post.json` sagt, welcher Klick gefehlt hat. Der Browser wird nicht versteckt, und Sperren werden nicht umgangen.

Voice-over mischt das Kommando nicht ins Video. Wenn du es drauf haben willst, ersetzt du die MP4, löschst den Eintrag in `clipXX.post.json` und startest `--publish` noch einmal.
