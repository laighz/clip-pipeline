\---

name: clip-pipeline

description: >-

&#x20; Ein Skill für den ganzen Weg. Findet Highlights in Twitch-VODs, eigenen

&#x20; Aufnahmen und, nur auf ausdrücklichen Wunsch, in einem laufenden

&#x20; Twitch-Stream, schneidet daraus vertikale Kurzclips und postet sie auf

&#x20; YouTube, TikTok, Instagram, Snapchat und X. Nutzen, sobald der User Clips,

&#x20; Highlights, Shorts, Reels, TikToks oder das Posten eines fertigen Clips will.

&#x20; Auch auf Englisch und ohne die Wörter Skill oder ffmpeg. Live-Aufnahme nur

&#x20; auf ausdrücklichen Wunsch. Nicht zum bloßen Kürzen eines einzelnen fertigen

&#x20; Videos und nicht für YouTube- oder Kick-VODs.

allowed-tools: Bash(uv run \*) PowerShell(uv run \*)

\---



\# Clip-Pipeline



Ein Einstieg. Zwei Ordner unter `C:\\Users\\morit\\Documents\\.code\\clip-pipeline`:



| Ordner | Was |

|---|---|

| \[clip-forge](clip-forge) | Schneiden. Skill und Skripte in `clip-forge\\skills\\clipping-streams` |

| \[content-syndicator](content-syndicator) | Posten. Liest `\~/clips/out/<vod-id>/clipXX.mp4` und die `clipXX.txt` daneben |



Diese Datei liegt eine Ebene darüber, in `clip-pipeline\\SKILL.md`. Von hier aus sind beide Links Nachbarordner.



\## Ablauf



1\. Lies `clip-forge\\skills\\clipping-streams\\SKILL.md` und folge ihr bis `log\_clip`. Keine eigenen ffmpeg- oder yt-dlp-Befehle. Die harten Regeln dort gelten, außer der Satz zum Selbst-Posten: gepostet wird nur über content-syndicator.

2\. Übergib Pfad, Hook, Titel und den Hinweis auf `clipXX.txt`.

3\. Posten nur, wenn der User es in dieser Unterhaltung ausdrücklich will. Sonst den Befehl nur zeigen.



Skripte zum Schneiden, `<skill>` ist `C:\\Users\\morit\\Documents\\.code\\clip-pipeline\\clip-forge\\skills\\clipping-streams`:



```powershell

uv run --quiet "<skill>\\scripts\\<name>.py" ...

