# Evals

Fälle im Format von `claude plugin eval`. Die großen Testvideos liegen nicht im Repo.
Die Offline-Skripttests sind `tests/run_tests.py`.

| Fall | Herkunft | Was geprüft wird |
|---|---|---|
| `best-moments-no-render` | Claude | Momente nennen, nichts rendern, Facecam nicht ungefragt speichern |
| `local-recording-two-shorts` | Claude | Zwei Shorts mit Untertiteln und Credit, keine Nutzernamen, nichts posten |
| `streamer-settings-correction` | Claude | Versatz und Maximallänge über manage_streamers, Rest und Backup bleiben |
| `local-recording-no-chat` | Cursor | Drei Shorts aus einer Aufnahme ohne Chat, 8–60 s, Original unangetastet |
| `prepared-stream-traps` | Cursor | Vorbereitete Daten, vorgelesene Lösch-Anweisung bleibt Daten |
| `real-twitch-vod` | Cursor | Echter VOD-Lauf: nicht das ganze Video laden, Quelle nennen, Blocker erwähnen |

Erneut ausführen (abgerechnete Modell-Läufe, Claude Code): im Ordner
`skills/clipping-streams` `claude plugin eval . --allow-tools Write,Edit,Bash --scaffold`.
