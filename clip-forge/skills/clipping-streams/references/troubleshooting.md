# Troubleshooting

## Programme fehlen (Exit 4)
`check_env.py` zeigt, was fehlt. Installation nur nach Rückfrage beim User:
- ffmpeg/ffprobe: `winget install --id Gyan.FFmpeg -e` (Full-Build: libass, h264_amf)
- yt-dlp: `winget install --id yt-dlp.yt-dlp -e`, Update: `winget upgrade --id yt-dlp.yt-dlp -e`
  (Twitch ändert oft etwas: bei Download-Fehlern zuerst yt-dlp aktualisieren)
- TwitchDownloaderCLI: GitHub-Release lay295/TwitchDownloader, Datei `TwitchDownloaderCLI-<ver>-Windows-x64.zip`,
  nach `~/clips/bin/TwitchDownloaderCLI/` entpacken (kein winget-Paket)
- streamlink: `winget install --id Streamlink.Streamlink -e` (ID mit Großbuchstaben!)
- uv: `winget install --id astral-sh.uv -e`
Nach winget ist der PATH erst in neuen Shells aktuell. Die Skripte finden Tools trotzdem
(PATH, WinGet\Links, WinGet\Packages, %LOCALAPPDATA%\Programs\*\bin, ~/clips/bin). Notfalls
Pfad per Umgebungsvariable: CLIPS_FFMPEG, CLIPS_FFPROBE, CLIPS_YTDLP, CLIPS_TWITCHDL, CLIPS_STREAMLINK.

## fetch_signals
- "Keine Twitch-VOD-ID erkannt": URL-Form `https://www.twitch.tv/videos/<zahl>`.
- yt-dlp 403 / Sub-only-VOD: Das VOD ist nur für Abonnenten. Streamer um die Datei bitten
  und `--input` nutzen.
- Chat-Download scheitert: Lauf geht mit Audio weiter (Warnung). TwitchDownloaderCLI fragt
  ohne `--collision Overwrite` interaktiv nach. Das Skript setzt das, nicht entfernen.
- Helix-Clips: brauchen TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET (kostenlose App auf dev.twitch.tv,
  Client-Credentials). Ohne: Clip-Bonus entfällt, sonst nichts. (ungetestet)

## detect_highlights liefert Unsinn
- Chat schwach (`mode: audio`): normal bei kleinen Streams. Audio trägt dann die Grundlast.
- Kandidaten immer zu früh/spät: `reaction_offset_s` für den Streamer anpassen (manage_streamers set).
- Zu viele/zu wenige: `chat_sigma` hoch/runter, `top_candidates`.

## transcribe
- Erster Lauf lädt ~460 MB (Hugging Face, Systran/faster-whisper-small) in den Benutzer-Cache.
- faster-whisper 1.2.1 + neues PyAV ("unexpected keyword argument 'metadata_errors'"):
  umgangen, weil transcribe.py das WAV selbst liest. Taucht der Fehler trotzdem auf: faster-whisper aktualisieren.
- Falsche Sprache: `manage_streamers.py set <id> language=en` oder `--language auto`.
- Sätze fehlen, obwohl sie im Ton sind: `cpu_threads` steht über 1. Auf CPU mit int8 wird die
  Suche dann nichtdeterministisch und verschluckt Wörter. Standard ist 1.

## whisper.cpp mit Vulkan (AMD RX 5700) einrichten (noch ungetestet)
1. Vulkan-Build von whisper.cpp besorgen (Release mit `vulkan` im Namen oder selbst bauen mit
   `-DGGML_VULKAN=1`, braucht Vulkan SDK). Nach `~/clips/bin/whisper.cpp/` entpacken.
2. Modell `ggml-small.bin` nach `~/clips/models/` laden.
3. Flags prüfen: `whisper-cli --help` muss `-ojf` (JSON full), `-of`, `-l`, `-np`, `-t` kennen.
4. Config: `transcription.backend: whisper-cpp`, `whisper_cpp.binary: ~/clips/bin/whisper.cpp/whisper-cli.exe`.
5. Test: `transcribe.py --vod-id <id> --backend whisper-cpp --force` und Wortzeiten mit dem
   faster-whisper-Ergebnis vergleichen (Selbsttest `tests/run_tests.py --only transcribe`).

## validate_clips Exit 2
Die Meldungen nennen Clip, Segment und Grund. Häufig: Segment ausserhalb geladener Abschnitte
(-> `fetch_sections.py --candidates <cid>`), Länge ausserhalb min/max, Überlappung zweier Clips.

## render_clip Exit 1
- `--dry-run` ansehen. AMF-Fehler: es wird automatisch libx264 versucht (`amf_fallback` im Ergebnis).
- "Unable to open ...ass": Pfad-Escaping. Nur über `_lib.escape_filter_path`.
- Schrift-Warnung: Schriftname in `assets/caption-style.ass` ist nicht installiert.

## verify_clip Exit 5
Fehlertext nennt die Abweichung (Auflösung, Codec, Abtastrate, Dauer). Dauer weicht ab:
meist ist die Abschnittsdatei kürzer als geplant (Download unvollständig) -> fetch_sections erneut.
`pix_fmt yuvj420p`: die Quelle ist Full-Range (manche Twitch-VODs). render_clip konvertiert seit
der Range-Korrektur nach TV-Range; tritt es trotzdem auf, mit `--force` neu rendern.

## detect_facecam
- Exit 5 `unsicher`: keine brauchbare Referenz (Profilbild ist Meme/Logo oder zeigt kein Gesicht).
  Snapshot ansehen, User fragen, `--confirm <nr>` - danach liegt `~/clips/faces/<id>.jpg`.
- Exit 5 `kein-gesicht`: Streamer gerade nicht im Bild. Anderen Zeitpunkt (`--at`) probieren.
- Falsche Person erkannt: `~/clips/faces/<id>.jpg` löschen und neu bestätigen.
- Rechteck zu knapp/zu groß: die Kantensuche nimmt an, dass die Cam in einer Ecke klebt. Bei
  freischwebenden Cams mit dem Raster-Snapshot (`manage_streamers.py snapshot`) nachmessen.
- Exit 4: Modell nicht ladbar oder Prüfsumme falsch. Dateien aus der Fehlermeldung von Hand
  nach `~/clips/models/` legen.

## Live (Weg C)
- Exit 3 bei record_start: Kanal ist offline.
- Status `abgestürzt`: Worker-Prozess weg, Log `~/clips/logs/live_<id>.log` lesen. Fertige
  Segmente bleiben nutzbar (live_prepare).
- Twitch verlangt manchmal ein Client-Integrity-Token: streamlink nutzt dafür Chrome/Edge
  (`--webbrowser-executable`). Fehlermeldung im Log suchen.
- Werbepausen: streamlink filtert Werbung, es entstehen Lücken. Gezählte Pausen stehen in
  `record_status.py` (`ads`). Zeitachse Chat/Video kann um die Pausenlänge abweichen.
