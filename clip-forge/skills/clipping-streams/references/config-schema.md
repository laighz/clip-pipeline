# Config-Schema: ~/clips/streamers.yaml

Alles optional. Fehlende Werte nehmen die eingebauten Defaults (`scripts/_lib.py`).
Unbekannte Streamer laufen mit `defaults`. Prüfen: `validate_config.py [--streamer <id>]`.
Ändern: nur mit `manage_streamers.py` (behält Kommentare, Backup `.bak`, Prüfung danach).

## defaults (gelten für alle Streamer, pro Streamer per `tuning` überschreibbar)
| Schlüssel | Typ / Bereich | Default | Wirkung |
|---|---|---|---|
| min_clip_s | Zahl 5-180 | 20 | unter dieser Länge warnt validate_clips (nicht mit Stille auffüllen). Unter 8 s ist ein Fehler |
| max_clip_s | Zahl 5-180 | 60 | längster Clip; >60 s warnt (YouTube-Claim-Sperre) |
| chat_sigma | Zahl 0.1-10 | 1.5 | Chat-Spike ab so vielen robusten Std.-Abw. |
| reaction_offset_s | Zahl 0-60 | 22 | Chat-Verzögerung nach dem Moment |
| window_s | Zahl 5-120 | 25 | Fenster für Chat-Merkmale |
| min_spacing_s | Zahl | 120 | Mindestabstand zwischen Kandidaten |
| min_chat_per_hour | Zahl | 500 | darunter wiegt Audio stärker als Chat |
| top_candidates | Ganzzahl 1-100 | 20 | Länge von candidates.json |
| section_pre_s / section_post_s | Zahl | 60 / 30 | Videopuffer um den Moment |
| baseline_s | Zahl 60-7200 | 600 | rollende Baseline (Median/MAD) |
| language | de, en, auto | de | Whisper-Sprache |
| credit | Text | "Clip: @{channel} auf Twitch" | Quellenzeile, `{channel}` wird ersetzt |
| blocked_categories | Liste | [Music, DJs] | nur Warnung (Musik-/Claim-Risiko) |
| emotes | Kategorie -> Liste | funny, hype, shock, sad | Emote-Labels, groß/klein zählt |

## transcription
`backend: faster-whisper | whisper-cpp`
- `faster_whisper`: model (tiny/base/small/medium/large-v3), compute_type (int8), cpu_threads (1; höher verschluckt auf CPU/int8 Sätze), beam_size, vad_filter
- `whisper_cpp`: binary (Name oder Pfad zu whisper-cli), model (Pfad zu ggml-*.bin), threads, extra_args.
  validate_config prüft, dass Binary und Modell existieren. Siehe troubleshooting.md, Abschnitt whisper.cpp.

## render
encoder (auto/amf/x264), width/height (gerade, 1080x1920), fps (30), layout (auto/stack/blur/center),
facecam_height (608), hook_duration_s (4), words_per_caption (3), caption_uppercase (false),
burn_credit (false), loudnorm ("I=-16:TP=-1.5:LRA=11"), audio_bitrate, x264 {crf, preset},
amf {quality, rc, qvbr_quality_level, b, maxrate}.

## live (Weg C, derselbe Skill, nur auf ausdrücklichen Wunsch)
record_dir (Ordner für Segmente, gern auf HDD), quality (best), segment_s (600), max_hours (10),
min_free_gb (15), chat_window_s (25), chat_baseline_s (600), confirm_s (3), cooldown_s (120), min_uniq (5).

## streamers (Liste)
```yaml
- id: kanalname            # a-z 0-9 _ -  (Pflicht)
  platform: twitch         # Phase 1: nur twitch (youtube/kick -> Fehler)
  channel: kanalname       # Twitch-Login
  credit: "Clip: @kanalname auf Twitch"
  layout:
    facecam: [x, y, w, h]  # Quellpixel; null = offen (Nachfrage), false = keine Facecam
                           # Standard für alle Clips. "facecam" pro Clip in clips.json gewinnt
                           # (detect_facecam.py --write-clip), z. B. bei wandernden Overlays
    game: [x, y, w, h]     # optional, Spielausschnitt für stack; null = mittig
                           # manage_streamers.py akzeptiert auch gameplay=x,y,w,h (wird als game gespeichert)
  blocked_categories: [Music, DJs]
  tuning: {reaction_offset_s: 18, max_clip_s: 45, language: en, emotes: {funny: [KEKW, LUL]}}
```
Ein alter `permission`-Block wird ignoriert (keine Allowlist mehr).

## manage_streamers.py, Beispiele
```
manage_streamers.py list
manage_streamers.py add kanal --channel kanal --facecam 1440,810,480,270
manage_streamers.py set kanal facecam=keine
manage_streamers.py set kanal reaction_offset_s=15 max_clip_s=45 emotes.hype=Pog,W gameplay=0,400,1920,680
manage_streamers.py set kanal "credit=Clip: @kanal auf Twitch"
manage_streamers.py --dry-run set kanal chat_sigma=2.0     # nur zeigen
manage_streamers.py remove kanal
manage_streamers.py snapshot --vod-id <id> --at 1234       # Standbild mit Gitter
```
Ungültige Werte (z. B. `max_clip_s=500`, `facecam=1,2,3`) lehnt das Skript mit Exit 2 ab, die
Datei bleibt unverändert.
