# ffmpeg-Rezepte (so baut render_clip.py die Befehle)

Zum Debuggen: `render_clip.py --vod-id <id> --clip <clipid> --dry-run` zeigt den kompletten Befehl
als Liste. Nicht von Hand nachbauen und ausführen, sondern Ursache in Config oder clips.json beheben.

## Aufbau
Pro Segment ein Input mit genauem Schnitt (Neukodierung macht `-ss` vor `-i` exakt):
`-ss <lokal> -t <dauer> -i <abschnittsdatei>`, lokal = VOD-Zeit - file_offset (sections.json).
Dann Layout pro Input -> `concat=n=N:v=1:a=1` -> `subtitles=filename=<escaped ass>` -> Ausgabe.
Ton: `aresample=48000,aformat=fltp:stereo` -> concat -> `loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000`.

## Layouts (W=1080, H=1920, FH=facecam_height=608, GH=H-FH)
- stack: `split=2` -> Facecam `crop=w:h:x:y,scale=W:FH:force_original_aspect_ratio=increase,crop=W:FH`,
  Spiel `scale=W:GH:force_original_aspect_ratio=increase,crop=W:GH` (oder vorher `crop` auf layout.game;
  `gameplay=` in manage_streamers.py schreibt denselben Schlüssel)
  -> `vstack=inputs=2`.
- blur: Hintergrund auf W/4 x H/4 skalieren, `boxblur=12:2`, hochskalieren, abdunkeln;
  Vordergrund `scale=W:-2`, mittig `overlay`.
- center: `scale=W:H:force_original_aspect_ratio=increase,crop=W:H` (mittiger 9:16-Ausschnitt).
Danach immer `setsar=1,fps=<fps>,format=yuv420p`.

## Encoder
- AMD (RX 5700, getestet): `-c:v h264_amf -quality quality -rc qvbr -qvbr_quality_level 22 -b:v 10M -maxrate 16M`.
  Optionen prüfen: `ffmpeg -h encoder=h264_amf`. Scheitert AMF, nimmt render_clip automatisch libx264.
- CPU: `-c:v libx264 -preset veryfast -crf 20 -profile:v high`.
- Immer: `-pix_fmt yuv420p -r <fps> -c:a aac -b:a 160k -ar 48000 -movflags +faststart`.
- Encoder erzwingen: `render_clip.py ... --encoder x264` oder `render.encoder: x264` in der Config.

## Pfade im Filter (Windows)
Der `subtitles=`-Filter braucht zweifach escapte Pfade (Doku "Notes on filtergraph escaping"):
Ebene 1 `\ ' :` escapen, Ebene 2 `\ ' [ ] , ;` escapen (inkl. Backslashes aus Ebene 1).
`C:\clips\a.ass` -> `C\\:/clips/a.ass`. Das macht `_lib.escape_filter_path`, getestet mit
Leerzeichen, Apostroph, eckigen Klammern und Komma im Pfad.

## Untertitel (assets/caption-style.ass)
Styles: Caption (unten-mittig, MarginV 560, aktives Wort gelb `&H0000E5FF`), Hook (oben, Kasten),
Credit (klein unten, nur mit render.burn_credit). Farben `&HAABBGGRR`. Schrift: Arial Black
(Windows-Standard). Andere Schrift: in der .ass ändern, Schriftname muss installiert sein;
render_clip warnt bei `fontselect`-/Glyph-Fehlern im ffmpeg-Log.

## Nützliche Prüfbefehle
- Ein Frame ansehen: `ffmpeg -ss 2 -i out.mp4 -frames:v 1 -vf scale=540:-2 frame.png`
- Lautheit: `verify_clip.py --vod-id <id> --loudness` (EBU R128, Ziel -16 ±2 LUFS)
