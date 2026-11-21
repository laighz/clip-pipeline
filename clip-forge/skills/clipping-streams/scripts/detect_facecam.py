# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "numpy>=1.26", "opencv-python>=4.9"]
# ///
"""Findet im Standbild das Gesicht des Streamers und schlägt sein Facecam-Rechteck vor.

Gedacht für Setups mit mehreren Webcams im Bild (Streamer + Gast) und für Overlays,
die mitten im Stream die Ecke wechseln. Statt zu raten, welche Cam gemeint ist:

  1. Referenzbild des Streamers holen (in dieser Reihenfolge):
     faces/<id>.jpg (einmal bestätigt)  ->  --reference <datei>  ->  Twitch-Profilbild
  2. Gesichter im Standbild finden (YuNet) und mit dem Referenzgesicht vergleichen (SFace)
  3. Vom gewählten Gesicht aus die Kanten des Overlays suchen -> Rechteck-Vorschlag
  4. Standbild mit nummerierten Gesichtern und Vorschlag schreiben

Ohne klaren Treffer wird nichts gewählt: Du siehst dir das Bild an und bestätigst
mit --confirm <nr>. Genau dieses Gesicht landet dann als faces/<id>.jpg und wird beim
nächsten VOD automatisch wiedererkannt. Twitch-Profilbilder sind oft Memes oder Logos,
darum ist die einmal bestätigte Referenz der zuverlässige Weg.

  detect_facecam.py --vod-id <id> --at 9624
  detect_facecam.py --vod-id <id> --clip clip01
  detect_facecam.py --vod-id <id> --at 9624 --confirm 2            # Gesicht 2 ist der Streamer
  detect_facecam.py --vod-id <id> --clip clip01 --confirm 2 --write-clip
  detect_facecam.py --vod-id <id> --at 9624 --reference bild.jpg

--write-clip schreibt das Rechteck als "facecam" in den Clip in clips.json (gilt nur für
diesen Clip). --write-streamer speichert es als Standard in streamers.yaml.

Modelle (einmalig ~39 MB nach ~/clips/models/) kommen aus dem offiziellen opencv_zoo und
werden gegen eine SHA256 geprüft. Alles bleibt lokal, es wird nichts hochgeladen.
Exit: 0 ok, 1 Fehler, 2 Eingabe ungültig, 4 Programm/Modell fehlt, 5 kein sicherer Treffer.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import numpy as np

import _https
import _lib

# opencv_zoo liegt in git-lfs, darum media.githubusercontent.com statt raw.
ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODELS = {
    "yunet": (f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": (f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
COSINE_MATCH = 0.363      # Schwelle aus dem opencv_zoo (sface.py)
COSINE_MARGIN = 0.06      # so viel muss der Beste vor dem Zweiten liegen
UA = "Mozilla/5.0 (clipping-streams)"


# ------------------------------------------------------------------ Modelle
@functools.lru_cache(maxsize=None)
def model_path(name: str) -> Path:
    url, filename, want = MODELS[name]
    target = _lib.clips_home() / "models" / filename
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    _lib.log.info("lade Modell %s (einmalig) ...", filename)
    try:
        data = _https.read_https(url, headers={"User-Agent": UA}, timeout=300)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        _lib.fail(f"Modell {filename} nicht ladbar ({exc}). Datei von Hand nach "
                  f"{target} legen: {url}", _lib.EXIT_MISSING_TOOL)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        _lib.fail(f"Modell {filename} hat die falsche Prüfsumme ({got[:12]}..., erwartet "
                  f"{want[:12]}...). Nicht benutzt.", _lib.EXIT_MISSING_TOOL)
    target.write_bytes(data)
    return target


def load_cv2():
    try:
        import cv2  # noqa: PLC0415  (erst hier, damit --help ohne opencv läuft)
    except ImportError:
        _lib.fail("opencv-python fehlt. Das Skript über 'uv run' starten, dann kommt es "
                  "automatisch mit.", _lib.EXIT_MISSING_TOOL)
    if not hasattr(cv2, "FaceDetectorYN"):
        _lib.fail(f"opencv {cv2.__version__} kennt FaceDetectorYN nicht, 4.9 oder neuer nötig.",
                  _lib.EXIT_MISSING_TOOL)
    return cv2


# ------------------------------------------------------------------ Referenzbild
def faces_dir() -> Path:
    d = _lib.clips_home() / "faces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def twitch_avatar(channel: str) -> Path | None:
    """Profilbild des Kanals laden. Erst Helix (wenn Zugangsdaten da sind), sonst
    das og:image der Kanalseite. Beides nur lesend und ohne Login."""
    target = faces_dir() / f"{_lib.safe_id(channel)}.profile.jpg"
    if target.is_file():
        return target
    url = None
    cid, secret = _lib.os.environ.get("TWITCH_CLIENT_ID"), _lib.os.environ.get("TWITCH_CLIENT_SECRET")
    if cid and secret:
        try:
            token = json.loads(_https.read_https(
                "https://id.twitch.tv/oauth2/token",
                data=urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                             "grant_type": "client_credentials"}).encode(),
                timeout=30,
            )).get("access_token")
            data = json.loads(_https.read_https(
                _https.https_url("api" + ".twitch.tv", f"/helix/users?login={channel}"),
                headers={"Client-Id": cid, "Authorization": f"Bearer {token}"},
                timeout=30,
            )).get("data") or []
            url = (data[0].get("profile_image_url") if data else None)
        except Exception as exc:                      # noqa: BLE001  (Fallback folgt)
            _lib.log.info("Helix nicht nutzbar (%s), nutze die Kanalseite", exc)
    if not url:
        try:
            html = _https.read_https(
                f"https://www.twitch.tv/{channel}", headers={"User-Agent": UA}, timeout=30, limit=400_000,
            ).decode("utf-8", "replace")
            m = re.search(r'og:image"\s+content="([^"]+profile_image[^"]+)"', html)
            url = m.group(1) if m else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            _lib.log.info("Kanalseite nicht erreichbar (%s)", exc)
    if not url:
        return None
    try:
        target.write_bytes(_https.read_https(url, headers={"User-Agent": UA}, timeout=60))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        _lib.log.info("Profilbild nicht ladbar (%s)", exc)
        return None
    return target


def reference_image(streamer: str, channel: str, explicit: str | None) -> tuple[Path | None, str]:
    saved = faces_dir() / f"{_lib.safe_id(streamer)}.jpg"
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            _lib.fail(f"Referenzbild {p} gibt es nicht.", _lib.EXIT_INVALID)
        return p, "datei"
    if saved.is_file():
        return saved, "bestaetigt"
    avatar = twitch_avatar(channel)
    if avatar:
        return avatar, "twitch-profilbild"
    return None, "keine"


# ------------------------------------------------------------------ Erkennung
def detect(cv2, img, score: float = 0.6, min_rel_h: float = 0.03) -> list[Any]:
    """Gesichter von links nach rechts. Winzige Funde (Chat-Avatare, Zuschauer im
    Spielbild) fliegen raus: eine Facecam zeigt ein Gesicht in brauchbarer Größe."""
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(model_path("yunet")), "", (w, h), score, 0.3, 500)
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    if faces is None:
        return []
    keep = [f for f in faces if f[3] >= min_rel_h * h]
    return sorted(keep, key=lambda f: (f[0], f[1]))


@functools.lru_cache(maxsize=1)
def recognizer(cv2):
    return cv2.FaceRecognizerSF.create(str(model_path("sface")), "")


def embed(cv2, img, face) -> Any:
    rec = recognizer(cv2)
    return rec.feature(rec.alignCrop(img, face))


def cosine(cv2, a, b) -> float:
    return float(recognizer(cv2).match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


# ------------------------------------------------------------------ Rechteck
SPAN_MIN = 0.55       # so viel Prozent der Breite/Höhe muss eine Kante durchlaufen
BORDER_SNAP = 16      # so nah am Bildrand zählt eine Kante als Bildrand


def span_line(grad, lo: int, hi: int, span: slice, border: int, axis: int) -> int:
    """Durchgehendste Kante zwischen lo und hi, sonst der Bildrand.

    Entscheidend ist nicht, wie stark eine Kante ist, sondern wie weit sie läuft:
    der Rand eines Webcam-Fensters zieht sich über die ganze Breite (bzw. Höhe) des
    Fensters, ein Haaransatz oder eine Schranktür dahinter nicht. Ohne solche Kante
    ist der Bildrand die Antwort, denn Cams kleben fast immer in einer Ecke.
    """
    # axis 0: senkrechte Kante, Position ist eine Spalte, span sind Zeilen.
    # axis 1: waagerechte Kante, Position ist eine Zeile, span sind Spalten.
    band = grad[span, :] if axis == 0 else grad[:, span]
    lo, hi = max(0, lo), min(band.shape[1 - axis], hi)
    if band.size == 0 or hi - lo < 2:
        return border
    strong = band > max(18.0, 2.5 * float(np.median(band)))
    frac = (strong.mean(axis=0) if axis == 0 else strong.mean(axis=1))[lo:hi]
    pos = lo + int(np.argmax(frac))
    if float(frac.max()) < SPAN_MIN:
        return border
    return border if abs(pos - border) <= BORDER_SNAP else pos


def overlay_rect(cv2, img, face) -> tuple[list[int], str]:
    """Vom Gesicht aus die Kanten des Webcam-Overlays suchen.

    Erst links/rechts (dafür reichen die Zeilen rund um das Gesicht), dann oben/unten
    über die so gefundene Breite - eine Kante, die das ganze Fenster durchläuft.
    """
    H, W = img.shape[:2]
    fx, fy, fw, fh = (int(round(v)) for v in face[:4])
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    # Eine Webcam klebt fast immer in einer Ecke. Die beiden Seiten zur nächsten
    # Ecke hin sind damit der Bildrand; gesucht werden nur die zwei inneren Kanten.
    # Sonst gewinnt leicht eine Schrankkante im Cam-Bild gegen den Fensterrand.
    face_cx, face_cy = fx + fw / 2, fy + fh / 2
    at_right, at_bottom = face_cx > W / 2, face_cy > H / 2
    rows = slice(max(0, fy - fh // 2), min(H, fy + 2 * fh))
    left = 0 if not at_right else span_line(gx, fx - 8 * fw, fx, rows, 0, axis=0)
    right = W if at_right else span_line(gx, fx + fw, fx + fw + 8 * fw, rows, W, axis=0)
    cols = slice(left, max(left + 1, right))
    top = 0 if not at_bottom else span_line(gy, fy - 8 * fh, fy, cols, 0, axis=1)
    bottom = H if at_bottom else span_line(gy, fy + fh, fy + fh + 8 * fh, cols, H, axis=1)
    rect = [int(left), int(top), int(right - left), int(bottom - top)]
    note = "Kanten gefunden"
    if rect[2] < fw * 1.2 or rect[3] < fh * 1.2:   # unplausibel klein -> großzügig ums Gesicht
        cx, cy = fx + fw // 2, fy + fh // 2
        half_w, half_h = int(fw * 1.6), int(fh * 1.6)
        rect = [max(0, cx - half_w), max(0, cy - half_h),
                min(W, cx + half_w) - max(0, cx - half_w),
                min(H, cy + half_h) - max(0, cy - half_h)]
        note = "keine klaren Kanten, Rechteck ums Gesicht geschaetzt"
    return rect, note


def sample_offsets(n: int) -> list[float]:
    """Zusätzliche Zeitpunkte um den Hauptmoment herum, in Sekunden."""
    extra = max(0, int(n) - 1)
    return [(-1) ** i * 4.0 * (i // 2 + 1) for i in range(extra)]


def grab_frame(cv2, vod_id: str, at: float, target: Path, strict: bool = True):
    """Ein Standbild aus der passenden Abschnittsdatei holen.

    strict=False gibt None zurück, statt abzubrechen - für die Zusatz-Stichproben,
    die auch außerhalb eines geladenen Abschnitts liegen können.
    """
    found = _lib.video_source_at(vod_id, at, strict=strict)
    if found is None:
        return None
    source, offset = found
    ffmpeg = _lib.require_tool("ffmpeg")
    proc = _lib.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", f"{max(0.0, at - offset):.3f}", "-i", str(source),
                     "-frames:v", "1", "-q:v", "2", str(target)], timeout=180)
    if proc.returncode != 0 or not target.is_file():
        if not strict:
            return None
        _lib.fail(f"Standbild bei {at:.0f} s fehlgeschlagen: {(proc.stderr or '').strip()[-400:]}",
                  _lib.EXIT_ERROR)
    img = cv2.imread(str(target))
    if img is None and strict:
        _lib.fail(f"{target} ist kein lesbares Bild.", _lib.EXIT_ERROR)
    return img


def median_rect(rects: list[list[int]]) -> list[int]:
    """Koordinatenweiser Median über mehrere Standbilder.

    Ein einzelnes Bild kann danebenliegen (jemand lehnt sich aus dem Bild, ein
    Overlay blinkt). Der Median über ein paar Zeitpunkte ist deutlich stabiler.
    """
    return [int(np.median([r[i] for r in rects])) for i in range(4)]


def annotate(cv2, img, faces, chosen: int | None, rect: list[int] | None, out: Path) -> None:
    canvas = img.copy()
    for i, f in enumerate(faces, start=1):
        x, y, w, h = (int(round(v)) for v in f[:4])
        hit = chosen == i
        color = (0, 220, 0) if hit else (0, 160, 255)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 3)
        label = f"{i}" + (" = Streamer" if hit else "")
        cv2.rectangle(canvas, (x, max(0, y - 34)), (x + 24 + 22 * len(label), y), color, -1)
        cv2.putText(canvas, label, (x + 8, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    if rect:
        x, y, w, h = rect
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 0, 0), 4)
        cv2.putText(canvas, f"facecam={x},{y},{w},{h}", (max(4, x + 8), max(30, y + 36)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 0, 0), 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])


# ------------------------------------------------------------------ Schreiben
def write_clip_facecam(vod_id: str, clip_id: str, rect: list[int]) -> str:
    path = _lib.work_dir(vod_id) / "clips.json"
    if not path.is_file():
        _lib.fail(f"{path} fehlt.", _lib.EXIT_INVALID)
    plan = _lib.read_json(path)
    for clip in plan.get("clips", []):
        if clip.get("id") == clip_id:
            clip["facecam"] = rect
            _lib.write_json(path, plan)
            return str(path)
    _lib.fail(f"Clip {clip_id!r} steht nicht in clips.json.", _lib.EXIT_INVALID)


def write_streamer_facecam(streamer: str, rect: list[int]) -> str:
    script = Path(__file__).with_name("manage_streamers.py")
    proc = _lib.run([_lib.sys.executable, str(script), "set", streamer,
                     "facecam=" + ",".join(str(v) for v in rect)], timeout=120)
    if proc.returncode != 0:
        _lib.fail(f"manage_streamers.py hat abgelehnt: {(proc.stderr or '').strip()[-400:]}",
                  _lib.EXIT_ERROR)
    return str(_lib.config_path())


# ------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gesicht des Streamers im Standbild finden und das Facecam-Rechteck vorschlagen.",
        epilog="Exit: 0 ok, 1 Fehler, 2 Eingabe, 4 Modell/Programm fehlt, 5 kein sicherer Treffer.")
    parser.add_argument("--vod-id", required=True)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--at", type=_lib.parse_time, help="VOD-Sekunde (auch 2:39:24)")
    where.add_argument("--clip", help="Clip-ID aus clips.json; sieht ins längste Segment (nicht ins Cold Open)")
    parser.add_argument("--streamer", help="Standard: streamer aus meta.json")
    parser.add_argument("--reference", help="eigenes Referenzbild statt Profilbild/faces-Ordner")
    parser.add_argument("--samples", type=int, default=3, metavar="N",
                        help="so viele Standbilder ansehen und den Median nehmen (Standard 3)")
    parser.add_argument("--confirm", type=int, metavar="NR",
                        help="dieses Gesicht ist der Streamer; wird als Referenz gespeichert")
    parser.add_argument("--write-clip", action="store_true",
                        help="Rechteck als facecam in den Clip in clips.json schreiben (mit --clip)")
    parser.add_argument("--write-streamer", action="store_true",
                        help="Rechteck als Standard in streamers.yaml schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("detect_facecam", args.verbose)

    work = _lib.work_dir(args.vod_id)
    meta = _lib.read_json(work / "meta.json") if (work / "meta.json").is_file() else {}
    streamer = args.streamer or meta.get("streamer") or ""
    if not streamer:
        _lib.fail("Kein Streamer bekannt. --streamer angeben.", _lib.EXIT_INVALID)
    cfg, _ = _lib.load_config()
    channel = str(_lib.streamer_settings(cfg, streamer).get("channel") or streamer)

    clip_id = args.clip
    if clip_id:
        plan = _lib.read_json(work / "clips.json") if (work / "clips.json").is_file() else {"clips": []}
        clip = next((c for c in plan.get("clips", []) if c.get("id") == clip_id), None)
        if not clip or not clip.get("segments"):
            _lib.fail(f"Clip {clip_id!r} steht nicht (mit Segmenten) in clips.json.", _lib.EXIT_INVALID)
        # Cold Open kann woanders liegen; das längste Segment zeigt das normale Layout.
        main_seg = max(clip["segments"], key=lambda s: float(s["end"]) - float(s["start"]))
        at = float(main_seg["start"]) + min(2.0, (float(main_seg["end"]) - float(main_seg["start"])) / 2)
    else:
        at = float(args.at)
    if args.write_clip and not clip_id:
        _lib.fail("--write-clip braucht --clip.", _lib.EXIT_INVALID)

    cv2 = load_cv2()
    frame_path = work / f"facecam_{int(at)}.src.jpg"
    img = grab_frame(cv2, args.vod_id, at, frame_path)

    faces = detect(cv2, img)
    if not faces:
        _lib.emit({"vod_id": args.vod_id, "at": at, "faces": [], "status": "kein-gesicht",
                   "frame": str(frame_path), "streamer": streamer,
                   "hint": "Kein Gesicht gefunden. Anderen Zeitpunkt probieren oder mit "
                           "layout blur rendern."}, _lib.EXIT_VERIFY)

    ref_path, ref_kind = reference_image(streamer, channel, args.reference)
    ref_feat = None
    if ref_path:
        ref_img = cv2.imread(str(ref_path))
        ref_faces = detect(cv2, ref_img, score=0.5) if ref_img is not None else []
        if ref_faces:
            ref_feat = embed(cv2, ref_img, ref_faces[0])
        else:
            log.info("Im Referenzbild (%s) ist kein Gesicht zu finden", ref_kind)
            ref_kind += "-ohne-gesicht"

    entries = []
    for i, f in enumerate(faces, start=1):
        x, y, w, h = (int(round(v)) for v in f[:4])
        score = None
        if ref_feat is not None:
            score = round(cosine(cv2, ref_feat, embed(cv2, img, f)), 3)
        entries.append({"nr": i, "box": [x, y, w, h], "detect_score": round(float(f[-1]), 3),
                        "match": score})

    chosen: int | None = None
    status = "unsicher"
    if args.confirm:
        if not 1 <= args.confirm <= len(faces):
            _lib.fail(f"--confirm {args.confirm}: es gibt nur {len(faces)} Gesicht(er).",
                      _lib.EXIT_INVALID)
        chosen, status = args.confirm, "bestaetigt"
    elif ref_feat is not None:
        ranked = sorted((e for e in entries if e["match"] is not None),
                        key=lambda e: e["match"], reverse=True)
        best = ranked[0]
        second = ranked[1]["match"] if len(ranked) > 1 else -1.0
        if best["match"] >= COSINE_MATCH and best["match"] - second >= COSINE_MARGIN:
            chosen, status = best["nr"], "erkannt"

    rect, rect_note = (None, None)
    samples = [round(at, 2)]
    if chosen:
        rect, rect_note = overlay_rect(cv2, img, faces[chosen - 1])
        # Noch ein paar Zeitpunkte ansehen und den Median nehmen: in einem einzelnen
        # Bild kann der Kantenfund danebenliegen (Bewegung, kurzes Overlay).
        chosen_feat = embed(cv2, img, faces[chosen - 1])
        rects = [rect]
        for delta in sample_offsets(args.samples):
            t = at + delta
            if t <= 0:
                continue
            extra_path = work / f"facecam_{int(at)}.s{int(delta)}.jpg"
            extra = grab_frame(cv2, args.vod_id, t, extra_path, strict=False)
            extra_path.unlink(missing_ok=True)
            if extra is None:
                continue
            extra_faces = detect(cv2, extra)
            scored = [(cosine(cv2, chosen_feat, embed(cv2, extra, f)), f) for f in extra_faces]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if not scored or scored[0][0] < COSINE_MATCH:
                continue
            more, _ = overlay_rect(cv2, extra, scored[0][1])
            rects.append(more)
            samples.append(round(t, 2))
        if len(rects) > 1:
            rect = median_rect(rects)
            rect_note = f"{rect_note}, Median aus {len(rects)} Standbildern"
    shot = work / f"facecam_{int(at)}.jpg"
    annotate(cv2, img, faces, chosen, rect, shot)

    written = []
    if chosen and status == "bestaetigt":
        x, y, w, h = entries[chosen - 1]["box"]
        pad_x, pad_y = int(w * 0.25), int(h * 0.25)
        crop = img[max(0, y - pad_y):y + h + pad_y, max(0, x - pad_x):x + w + pad_x]
        ref_target = faces_dir() / f"{_lib.safe_id(streamer)}.jpg"
        cv2.imwrite(str(ref_target), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        written.append(str(ref_target))
    if rect and args.write_clip:
        written.append(write_clip_facecam(args.vod_id, clip_id, rect))
    if rect and args.write_streamer:
        written.append(write_streamer_facecam(streamer, rect))

    hints = {
        "unsicher": f"Bild {shot.name} ansehen, dann erneut mit --confirm <nr>. "
                    "Ohne Bestätigung nichts raten, sondern layout blur nutzen.",
        "erkannt": "Rechteck im Bild prüfen, dann mit --write-clip oder --write-streamer speichern.",
        "bestaetigt": "Referenz gespeichert, beim nächsten VOD läuft die Zuordnung automatisch.",
    }
    _lib.emit({
        "vod_id": args.vod_id, "streamer": streamer, "clip": clip_id, "at": round(at, 2),
        "status": status, "reference": {"kind": ref_kind, "file": str(ref_path) if ref_path else None,
                                        "threshold": COSINE_MATCH},
        "faces": entries, "chosen": chosen, "facecam": rect, "facecam_note": rect_note,
        "frame": str(frame_path), "snapshot": str(shot), "written": written, "samples": samples,
        "hint": hints[status],
    }, _lib.EXIT_OK if chosen else _lib.EXIT_VERIFY)


if __name__ == "__main__":
    main()
