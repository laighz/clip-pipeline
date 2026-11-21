# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = []
# ///
"""Selbsttests für clipping-streams mit synthetischem Material.

Läuft gegen einen eigenen Test-Datenordner (CLIPS_HOME), nie gegen ~/clips.

  uv run tests/run_tests.py                       # alle Stufen
  uv run tests/run_tests.py --only check_env validate_config
  uv run tests/run_tests.py --home D:/tmp/cs-test # anderer Testordner

Ausgabe: JSON mit pass/fail pro Stufe. Exit 0 nur, wenn alles grün ist.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
VOD_ID = "test-vod"
STREAMER = "teststreamer"


class Ctx:
    def __init__(self, home: Path, fixtures: Path) -> None:
        self.home = home
        self.fixtures = fixtures
        os.environ["CLIPS_HOME"] = str(home)
        os.environ["PYTHONUTF8"] = "1"
        self.uv = shutil.which("uv") or "uv"

    def run(self, script: str, *args: str, timeout: float = 900) -> tuple[int, dict, str]:
        cmd = [self.uv, "run", "--quiet", str(SCRIPTS / script), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        try:
            data = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            data = {"_raw_stdout": proc.stdout[-500:]}
        return proc.returncode, data, proc.stderr


class Fail(Exception):
    pass


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise Fail(message)


def expected(ctx: Ctx) -> dict:
    return json.loads((ctx.fixtures / "expected.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ Stufen
def t_check_env(ctx: Ctx) -> str:
    code, _, _ = ctx.run("check_env.py", "--help")
    expect(code == 0, "--help scheitert")
    env_json = ctx.home / "state" / "env.json"
    env_json.unlink(missing_ok=True)
    code, data, _ = ctx.run("check_env.py", "--dry-run")
    expect(code == 0, f"dry-run exit {code}: {data.get('missing')}")
    expect(not env_json.exists(), "dry-run hat env.json geschrieben")
    code, data, _ = ctx.run("check_env.py")
    expect(code == 0 and env_json.exists(), "env.json fehlt nach echtem Lauf")
    return f"encoder={data['encoder']['chosen']} ({data['encoder']['detail']})"


def t_validate_config(ctx: Ctx) -> str:
    cfg = ctx.home / "streamers.yaml"
    cfg.unlink(missing_ok=True)
    code, data, _ = ctx.run("validate_config.py")
    expect(code == 0 and data["warnings"], "fehlende Config sollte ok mit Warnung sein")
    code, data, _ = ctx.run("validate_config.py", "--init", "--dry-run")
    expect(code == 0 and not cfg.exists(), "--init --dry-run hat geschrieben")
    code, data, _ = ctx.run("validate_config.py", "--init")
    expect(code == 0 and cfg.exists(), f"--init: exit {code}, {data.get('errors')}")
    code, data, _ = ctx.run("validate_config.py")
    expect(code == 0 and data["streamers"] == [], f"--init sollte ohne Streamer starten: {data['streamers']}")
    example = str(SCRIPTS.parent / "assets" / "streamers.example.yaml")
    code, data, _ = ctx.run("validate_config.py", "--config", example, "--streamer", "beispielstreamer")
    s = data.get("settings", {})
    expect(code == 0 and s.get("known") and s.get("reaction_offset_s") == 20
           and s.get("layout", {}).get("facecam") == [1440, 810, 480, 270],
           f"Tuning nicht gemischt: {s}")
    code, data, _ = ctx.run("validate_config.py", "--streamer", "unbekannt")
    expect(code == 0 and any("Defaults" in w for w in data["warnings"]),
           "unbekannter Streamer sollte mit Defaults laufen")
    bad_cases = {
        "min-groesser-max": "defaults: {min_clip_s: 70, max_clip_s: 60}\n",
        "youtube": "streamers: [{id: a, platform: youtube}]\n",
        "facecam": "streamers: [{id: a, layout: {facecam: [1, 2, 3]}}]\n",
        "ungerade": "render: {width: 1081}\n",
        "backend": "transcription: {backend: openai}\n",
        "yaml": "defaults: [kaputt\n",
    }
    for name, text in bad_cases.items():
        path = ctx.home / f"bad-{name}.yaml"
        path.write_text(text, encoding="utf-8")
        code, data, _ = ctx.run("validate_config.py", "--config", str(path))
        path.unlink()
        expect(code == 2, f"Fall '{name}' sollte Exit 2 liefern, war {code}")
    return f"Beispiel-Config ok, {len(bad_cases)} Fehlerfälle liefern Exit 2"


def sentinel_hits(ctx: Ctx) -> list[str]:
    """Sucht die Sentinel-Strings aus dem Fake-Chat in allen Dateien unter CLIPS_HOME."""
    needles = [s.encode("utf-8") for s in expected(ctx)["sentinels"]]
    hits = []
    for path in ctx.home.rglob("*"):
        if path.is_file() and path.stat().st_size < 300 * 1024**2:
            data = path.read_bytes()
            hits += [f"{path.name}:{n.decode()}" for n in needles if n in data]
    return hits


def t_fetch_signals(ctx: Ctx) -> str:
    exp = expected(ctx)
    code, _, _ = ctx.run("fetch_signals.py", "--help")
    expect(code == 0, "--help scheitert")
    code, _, _ = ctx.run("fetch_signals.py", "--vod", "https://example.com/nix")
    expect(code == 2, f"ungültige VOD-URL sollte Exit 2 liefern, war {code}")
    work = ctx.home / "work" / VOD_ID
    shutil.rmtree(work, ignore_errors=True)
    inbox = ctx.home / "inbox"
    inbox.mkdir(exist_ok=True)
    chat_copy = inbox / "chat.json"
    shutil.copyfile(ctx.fixtures / "chat.json", chat_copy)
    base = ["--input", str(ctx.fixtures / "vod.mp4"), "--vod-id", VOD_ID, "--streamer", STREAMER]
    code, data, _ = ctx.run("fetch_signals.py", *base, "--chat-json", str(chat_copy), "--dry-run")
    expect(code == 0 and data.get("dry_run") and not (work / "signals.json").exists(),
           "dry-run hat Dateien erzeugt")
    expect(chat_copy.exists(), "dry-run hat die Chat-Datei gelöscht")
    # Fehlerpfad: kaputte Chat-Datei mit Sentinel -> muss trotzdem gelöscht werden
    broken = inbox / "broken.json"
    broken.write_text(chat_copy.read_text(encoding="utf-8")[:-5000], encoding="utf-8")
    code, data, _ = ctx.run("fetch_signals.py", *base, "--chat-json", str(broken))
    expect(code == 0 and not broken.exists(), "kaputte Chat-Datei wurde nicht gelöscht")
    expect(not data["chat"]["available"], "kaputte Chat-Datei sollte chat.available=false ergeben")
    # Normalfall
    code, data, err = ctx.run("fetch_signals.py", *base, "--chat-json", str(chat_copy))
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-300:]}")
    expect(not chat_copy.exists() and data["chat"]["raw_deleted"], "Chat-Rohdatei nicht gelöscht")
    expect(data["chat"]["messages_total"] == exp["chat"]["messages"],
           f"Nachrichten {data['chat']['messages_total']} != {exp['chat']['messages']}")
    muted = data["audio"]["muted"]
    expect(any(abs(a - exp["muted"][0]) <= 2 and abs(b - exp["muted"][1]) <= 2 for a, b in muted),
           f"stummer Bereich {exp['muted']} nicht erkannt: {muted}")
    hits = sentinel_hits(ctx)
    expect(not hits, f"Chat-Daten auf der Platte gefunden: {hits}")
    cfg_text = (ctx.home / "streamers.yaml").read_text(encoding="utf-8")
    expect(f"id: {STREAMER}" in cfg_text, "unbekannter Streamer wurde nicht automatisch eingetragen")
    sig = json.loads((work / "signals.json").read_text(encoding="utf-8"))
    expect(len(sig["audio"]["db"]) >= exp["duration"] - 1, "Audio-Kurve zu kurz")
    return (f"{data['chat']['messages_total']} Nachrichten -> nur Zählwerte, Rohdatei gelöscht, "
            f"stumm={muted}, kein Sentinel auf der Platte")


def t_chat_json_safety(ctx: Ctx) -> str:
    """Regression: --chat-json löschte jede übergebene Datei, auch wenn sie gar kein Chat war."""
    inbox = ctx.home / "inbox_safety"
    inbox.mkdir(exist_ok=True)
    cases = {"notizen.md": "# Meine Notizen\nnicht löschen\n",
             "anderes.json": json.dumps({"kein": "chat"}),
             "liste.json": json.dumps([1, 2, 3])}
    base = ["--input", str(ctx.fixtures / "vod.mp4"), "--vod-id", "safety-vod", "--no-clips"]
    for name, text in cases.items():
        path = inbox / name
        path.write_text(text, encoding="utf-8")
        code, data, _ = ctx.run("fetch_signals.py", *base, "--chat-json", str(path))
        expect(code == 2, f"{name}: falsche Datei sollte Exit 2 liefern, war {code} ({data.get('chat')})")
        expect(path.is_file() and path.read_text(encoding="utf-8") == text, f"{name} wurde gelöscht oder verändert")
    shutil.rmtree(inbox)
    return f"{len(cases)} falsche Dateien -> Exit 2, alle unverändert"


def t_detect_highlights(ctx: Ctx) -> str:
    exp = expected(ctx)
    work = ctx.home / "work" / VOD_ID
    code, _, _ = ctx.run("detect_highlights.py", "--help")
    expect(code == 0, "--help scheitert")
    code, _, _ = ctx.run("detect_highlights.py", "--vod-id", "gibtsnicht")
    expect(code == 2, f"fehlende signals.json sollte Exit 2 liefern, war {code}")
    (work / "candidates.json").unlink(missing_ok=True)
    code, data, _ = ctx.run("detect_highlights.py", "--vod-id", VOD_ID, "--dry-run")
    expect(code == 0 and not (work / "candidates.json").exists(), "dry-run hat candidates.json geschrieben")
    code, data, err = ctx.run("detect_highlights.py", "--vod-id", VOD_ID)
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-300:]}")
    cands = json.loads((work / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    moments = [c["moment_s"] for c in cands]
    strong = exp["events"][1]["moment"]
    medium = exp["events"][0]["moment"]
    expect(cands and abs(cands[0]["moment_s"] - strong) <= 3,
           f"Top-Kandidat {moments[:3]} trifft das starke Ereignis {strong} nicht")
    expect("chat-spike" in cands[0]["reasons"] and "laut" in cands[0]["reasons"],
           f"Top-Kandidat sollte Chat und Audio haben: {cands[0]['reasons']}")
    expect(any(abs(m - medium) <= 3 for m in moments), f"Ereignis {medium} fehlt in {moments}")
    a, b = exp["muted"]
    expect(not any(a <= m <= b for m in moments), f"Kandidat im stummen Bereich: {moments}")
    s0, s1 = cands[0]["section"]
    expect(s0 <= strong - 40 and s1 >= strong + 20, f"Abschnitt {cands[0]['section']} zu knapp")
    return f"Momente {moments} (erwartet {strong} und {medium}), Top: {cands[0]['reasons']}"


def loud_second(ctx: Ctx, media: str, start: float, length: float) -> int:
    """Beginn des lautesten Ereignisses (erste Sekunde > 50 % der Maximal-Energie), relativ zu start."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(length),
                           "-i", media, "-vn", "-af", "highpass=f=300,lowpass=f=3400", "-ac", "1", "-ar", "8000",
                           "-f", "s16le", "-"], capture_output=True, check=True)
    import array
    samples = array.array("h", proc.stdout[: len(proc.stdout) // 2 * 2])
    energy = [sum(s * s for s in samples[i:i + 8000]) for i in range(0, len(samples), 8000)]
    peak = max(energy)
    return next(i for i, e in enumerate(energy) if e > 0.5 * peak)


def t_fetch_sections(ctx: Ctx) -> str:
    exp = expected(ctx)
    work = ctx.home / "work" / VOD_ID
    code, _, _ = ctx.run("fetch_sections.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("fetch_sections.py", "--vod-id", VOD_ID, "--candidates", "c99")
    expect(code == 2, "unbekannter Kandidat sollte Exit 2 liefern")
    # Download-Stub: yt-dlp lädt per file:// aus dem Testvideo -> prüft echte Schnitte + Versatz
    stub = (ctx.fixtures / "hls" / "vod.m3u8").resolve().as_uri()   # HLS wie bei Twitch
    raw_sections = ctx.home / "raw" / VOD_ID / "sections"
    shutil.rmtree(raw_sections, ignore_errors=True)
    (work / "sections.json").unlink(missing_ok=True)
    code, data, _ = ctx.run("fetch_sections.py", "--vod-id", VOD_ID, "--stub-url", stub, "--max", "2", "--dry-run")
    expect(code == 0 and data.get("would_run") and not raw_sections.exists()
           and not (work / "sections.json").exists(), "dry-run hat Dateien erzeugt")
    code, data, err = ctx.run("fetch_sections.py", "--vod-id", VOD_ID, "--stub-url", stub, "--max", "2")
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-400:]}")
    secs = json.loads((work / "sections.json").read_text(encoding="utf-8"))["sections"]
    strong = exp["events"][1]["moment"]
    sec = next(s for s in secs if s["start"] <= strong <= s["end"])
    expect(Path(sec["file"]).is_file(), "Abschnittsdatei fehlt")
    rel = loud_second(ctx, sec["file"], 0, sec["end"] - sec["start"])
    expect(abs((sec["file_offset"] + rel) - strong) <= 1,
           f"Versatz falsch: lautester Punkt bei VOD {sec['file_offset'] + rel}, erwartet {strong}")
    stub_detail = f"Stub-Download: {len(secs)} Abschnitte, Ereignis in Datei bei VOD {sec['file_offset'] + rel} s"
    # Lokaler Modus (Standard für den Gesamttest): Abschnitte zeigen auf die Originaldatei
    code, data, _ = ctx.run("fetch_sections.py", "--vod-id", VOD_ID, "--max", "3")
    secs = json.loads((work / "sections.json").read_text(encoding="utf-8"))["sections"]
    expect(code == 0 and all(s["file_offset"] == 0.0 and s["status"] == "lokal" for s in secs),
           f"lokaler Modus falsch: {secs}")
    return f"{stub_detail}; lokal: {len(secs)} Abschnitte"


def t_manage_streamers(ctx: Ctx) -> str:
    cfg = ctx.home / "streamers.yaml"
    code, _, _ = ctx.run("manage_streamers.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("manage_streamers.py", "list")
    expect(code == 0 and any(s["id"] == STREAMER for s in data["streamers"]), "list zeigt teststreamer nicht")
    ctx.run("manage_streamers.py", "remove", "neuer")   # Rest eines abgebrochenen Laufs entfernen
    before = cfg.read_bytes()
    code, data, _ = ctx.run("manage_streamers.py", "--dry-run", "add", "neuer")
    expect(code == 0 and cfg.read_bytes() == before, "add --dry-run hat geschrieben")
    code, data, _ = ctx.run("manage_streamers.py", "add", "neuer", "--channel", "neuer_kanal",
                            "--facecam", "10,20,300,200")
    expect(code == 0 and data["action"] == "added" and data.get("written"), f"add: {data}")
    expect((ctx.home / "streamers.yaml.bak").exists(), "kein Backup angelegt")
    code, data, _ = ctx.run("manage_streamers.py", "add", "neuer")
    expect(code == 0 and data["action"] == "exists", "add ist nicht idempotent")
    code, data, _ = ctx.run("manage_streamers.py", "set", "neuer", "reaction_offset_s=18",
                            "emotes.funny=KEKW,LUL", "credit=Clip: @neuer_kanal auf Twitch")
    expect(code == 0, f"set: {data.get('error')}")
    code, data, _ = ctx.run("validate_config.py", "--streamer", "neuer")
    s = data["settings"]
    expect(s["reaction_offset_s"] == 18 and s["layout"]["facecam"] == [10, 20, 300, 200]
           and s["emotes"]["funny"] == ["KEKW", "LUL"] and s["credit"].startswith("Clip: @neuer_kanal"),
           f"Werte nicht übernommen: {s}")
    good = cfg.read_bytes()
    for bad in (["facecam=1,2,3"], ["max_clip_s=500"], ["gibtsnicht=1"]):
        code, data, _ = ctx.run("manage_streamers.py", "set", "neuer", *bad)
        expect(code == 2 and cfg.read_bytes() == good, f"{bad} hätte abgelehnt werden müssen (exit {code})")
    text = cfg.read_text(encoding="utf-8")
    expect("Chat reagiert ~22 s" in text, "Kommentare in streamers.yaml gingen verloren")
    code, data, _ = ctx.run("manage_streamers.py", "remove", "neuer")
    expect(code == 0 and "id: neuer" not in cfg.read_text(encoding="utf-8"), "remove hat nicht entfernt")
    # Facecam des Teststreamers setzen (für das stack-Layout beim Rendern)
    x, y, w, h = expected(ctx)["facecam"]
    code, data, _ = ctx.run("manage_streamers.py", "set", STREAMER, f"facecam={x},{y},{w},{h}")
    expect(code == 0, f"facecam setzen: {data.get('error')}")
    code, data, _ = ctx.run("manage_streamers.py", "snapshot", "--vod-id", VOD_ID, "--at", "231")
    expect(code == 0 and Path(data["snapshot"]).is_file(), f"snapshot: {data.get('error')}")
    return f"add/set/remove ok, Kommentare erhalten, 3 ungültige Werte abgelehnt, Snapshot {Path(data['snapshot']).name}"


def all_words(ctx: Ctx) -> list[dict]:
    words = []
    for f in sorted((ctx.home / "work" / VOD_ID / "transcripts").glob("*.json")):
        words += json.loads(f.read_text(encoding="utf-8"))["words"]
    return words


def t_transcribe(ctx: Ctx) -> str:
    exp = expected(ctx)
    work = ctx.home / "work" / VOD_ID
    tdir = work / "transcripts"
    code, _, _ = ctx.run("transcribe.py", "--help")
    expect(code == 0, "--help scheitert")
    code, _, _ = ctx.run("transcribe.py", "--vod-id", VOD_ID, "--sections", "s99")
    expect(code == 2, "unbekannter Abschnitt sollte Exit 2 liefern")
    shutil.rmtree(tdir, ignore_errors=True)
    code, data, _ = ctx.run("transcribe.py", "--vod-id", VOD_ID, "--dry-run")
    expect(code == 0, f"dry-run: exit {code}, {data.get('error')} (Stufen bauen aufeinander auf)")
    expect(data["would_transcribe"] and not tdir.exists(), "dry-run hat Dateien erzeugt")
    code, data, err = ctx.run("transcribe.py", "--vod-id", VOD_ID, timeout=1800)
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-500:]}")
    words = all_words(ctx)
    expect(len(words) >= 20, f"nur {len(words)} Wörter erkannt")

    def first(stem: str) -> dict | None:
        return next((w for w in words if w["w"].lower().strip(".,!?").startswith(stem)), None)

    # (Wort, TTS-Start, Toleranz): "gibt" steht nach dem kurzen Ausruf "Nein!", den Whisper oft verschluckt
    checks = {"okay": (exp["speech"][0]["start"], 1.5), "gibt": (exp["speech"][1]["start"] + 1.0, 1.5),
              "danke": (exp["speech"][3]["start"], 1.5)}
    found = {}
    for stem, (start, tol) in checks.items():
        w = first(stem)
        expect(w is not None, f"Wort '{stem}' nicht erkannt. Erkannt: {' '.join(x['w'] for x in words)[:300]}")
        expect(abs(w["s"] - start) <= tol, f"'{stem}' bei VOD {w['s']} s, erwartet ~{start} s (Zeitachse falsch?)")
        found[stem] = w["s"]
    expect(first("unglaublich") is not None, "'unglaublich' nicht erkannt")
    code, data2, _ = ctx.run("transcribe.py", "--vod-id", VOD_ID)
    expect(code == 0 and all(s["status"] == "schon da" for s in data2["sections"]), "zweiter Lauf nicht idempotent")
    return (f"{len(words)} Wörter, Zeitstempel {found}, {data['seconds_total']} s gesamt, "
            f"{data['realtime_factor']}x Echtzeit")


def t_sections_merge(ctx: Ctx) -> str:
    """Regression: neue Kandidaten dürfen vorhandene Abschnitte/Transkripte nicht überschreiben
    (früher hieß jeder neue Abschnitt wieder s01 -> altes Transkript, validate_clips Exit 2)."""
    vid = "merge-vod"
    work = ctx.home / "work" / vid
    shutil.rmtree(work, ignore_errors=True)
    (work / "transcripts").mkdir(parents=True)
    (work / "meta.json").write_text(json.dumps({"source": "local", "input": str(ctx.fixtures / "vod.mp4"),
                                                "duration_s": 400, "streamer": STREAMER}), encoding="utf-8")

    def candidates(*moments: int) -> None:
        (work / "candidates.json").write_text(json.dumps({"candidates": [
            {"id": f"c{m}", "moment_s": m, "section": [m - 60, m + 52]} for m in moments]}), encoding="utf-8")

    candidates(100)
    code, d1, _ = ctx.run("fetch_sections.py", "--vod-id", vid, "--candidates", "c100")
    expect(code == 0 and [s["id"] for s in d1["sections"]] == ["s40-152"], f"Lauf 1: {d1}")
    (work / "transcripts" / "s40-152.json").write_text(json.dumps(
        {"section_id": "s40-152", "start": 40.0, "end": 152.0, "words": [{"s": 95.0, "e": 95.4, "w": "alt"}]}),
        encoding="utf-8")
    plan = good_plan()
    plan["vod_id"] = vid
    plan["clips"] = [dict(plan["clips"][1], segments=[{"start": 85.0, "end": 110.0}])]
    (work / "clips.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    candidates(100, 300)   # zweiter Live-Spike / nachträglich geladener Kandidat
    code, d2, _ = ctx.run("fetch_sections.py", "--vod-id", vid, "--candidates", "c300")
    ids = [s["id"] for s in d2["sections"]]
    expect(code == 0 and ids == ["s40-152", "s240-352"], f"Lauf 2 sollte ergänzen, nicht ersetzen: {ids}")
    code, tr, _ = ctx.run("transcribe.py", "--vod-id", vid, "--dry-run")
    todo = [s["id"] for s in tr.get("would_transcribe", [])]
    expect(code == 0 and todo == ["s240-352"] and tr["skipped_existing"] == ["s40-152"],
           f"transcribe sollte nur den neuen Abschnitt machen: {tr}")
    code, val, _ = ctx.run("validate_clips.py", "--vod-id", vid)
    expect(code == 0, f"alter Clip muss gültig bleiben: {val.get('errors')}")

    candidates(100, 300, 170)   # überlappt s40-152 -> Vereinigung ersetzt den alten Abschnitt
    code, d3, _ = ctx.run("fetch_sections.py", "--vod-id", vid, "--candidates", "c170")
    ids = [s["id"] for s in d3["sections"]]
    expect(code == 0 and ids == ["s40-222", "s240-352"] and d3["superseded"] == ["s40-152"],
           f"Überlappung sollte vereinigen: {ids}, ersetzt {d3.get('superseded')}")
    code, tr, _ = ctx.run("transcribe.py", "--vod-id", vid, "--dry-run")
    expect([s["id"] for s in tr["would_transcribe"]] == ["s40-222", "s240-352"],
           f"Transkript des ersetzten Abschnitts darf nicht weiterverwendet werden: {tr}")
    # Altes Transkript unter neuer Spanne (gleiche ID, andere Zeit) gilt nicht als aktuell
    (work / "transcripts" / "s240-352.json").write_text(json.dumps(
        {"section_id": "s240-352", "start": 241.0, "end": 352.0, "words": []}), encoding="utf-8")
    code, tr, _ = ctx.run("transcribe.py", "--vod-id", vid, "--dry-run")
    expect("s240-352" in [s["id"] for s in tr["would_transcribe"]], "Zeitspanne des Transkripts nicht geprüft")
    return "Abschnitte werden ergänzt/vereinigt, Transkripte nur für passende Zeitspanne wiederverwendet"


HOOK = "Völlig übertrieben: Ärger im Chat!"


def good_plan() -> dict:
    credit = f"Clip: @{STREAMER} auf Twitch"
    return {"vod_id": VOD_ID, "clips": [
        {"id": "clip01", "candidate": "c01",
         "segments": [{"start": 230.0, "end": 232.5, "role": "cold_open"}, {"start": 221.5, "end": 242.0}],
         "hook": HOOK, "title": "Er kann es nicht fassen #Shorts",
         "captions": {"youtube": f"Das gibt es doch nicht! {credit} #Shorts #twitch",
                      "tiktok": f"Wer hätte das gedacht? {credit} #fyp", "instagram": f"Unglaublich. {credit} #reels"},
         "commentary": "Kurz vorher war alles ruhig, dann kippt es komplett."},
        {"id": "clip02", "candidate": "c02",
         "segments": [{"start": 78.5, "end": 101.0}], "layout": "blur",
         "hook": "Jetzt wird es spannend", "title": "Passt auf! #Shorts",
         "captions": {"youtube": f"{credit} #Shorts", "tiktok": f"{credit} #fyp", "instagram": f"{credit} #reels"},
         "commentary": "Der Aufbau vor dem Knall."},
    ]}


def t_validate_clips(ctx: Ctx) -> str:
    work = ctx.home / "work" / VOD_ID
    clips_file = work / "clips.json"
    code, _, _ = ctx.run("validate_clips.py", "--help")
    expect(code == 0, "--help scheitert")

    def check(plan: dict | str, want: int, label: str) -> dict:
        clips_file.write_text(plan if isinstance(plan, str) else json.dumps(plan, ensure_ascii=False),
                              encoding="utf-8")
        code, data, _ = ctx.run("validate_clips.py", "--vod-id", VOD_ID)
        expect(code == want, f"{label}: exit {code} statt {want}: {data.get('errors') or data.get('error')}")
        return data

    bad = []
    p = good_plan(); p["clips"][0]["segments"] = [{"start": 221.5, "end": 225.0}]; bad.append((p, "zu kurz"))
    p = good_plan(); p["clips"][1]["segments"] = [{"start": 215.0, "end": 240.0}]; bad.append((p, "Überlappung"))
    p = good_plan(); p["clips"][0]["segments"].reverse(); bad.append((p, "Cold Open hinten"))
    p = good_plan(); p["clips"][1]["segments"] = [{"start": 395.0, "end": 420.0}]; bad.append((p, "ausserhalb"))
    p = good_plan(); del p["clips"][0]["captions"]["instagram"]; bad.append((p, "Caption fehlt"))
    p = good_plan(); p["clips"][1]["id"] = "Clip 2!"; bad.append((p, "unsichere ID"))
    p = good_plan(); p["clips"][0]["facecam"] = [10, 10, 0, 50]; bad.append((p, "facecam ohne Breite"))
    p = good_plan(); p["clips"][0]["facecam"] = "oben links"; bad.append((p, "facecam kein Rechteck"))
    for plan, label in bad:
        check(plan, 2, label)
    check("{kaputt", 2, "kaputtes JSON")
    word = next(w for w in all_words(ctx) if 79 <= w["s"] <= 86 and w["e"] - w["s"] >= 0.25)
    mid = round((word["s"] + word["e"]) / 2, 2)
    p = good_plan(); p["clips"][1]["segments"] = [{"start": mid, "end": mid + 21.0}]
    data = check(p, 0, "Schnitt im Wort")
    expect(any("mitten im Wort" in w for w in data["warnings"]), "Schnitt mitten im Wort nicht gewarnt")
    p = good_plan(); p["clips"][1]["segments"] = [{"start": 78.5, "end": 90.5}]
    data = check(p, 0, "kurzer Clip")
    expect(any("nicht mit Stille" in w for w in data["warnings"]),
           f"12-s-Clip sollte nur warnen: {data.get('warnings')}")
    p = good_plan(); p["clips"][0]["facecam"] = [1400, 0, 520, 300]
    check(p, 0, "facecam pro Clip")
    data = check(good_plan(), 0, "gültiger Plan")
    durations = {c["id"]: c["duration_s"] for c in data["clips"]}
    return f"{len(bad) + 1} Fehlerfälle -> Exit 2, Wort-Schnitt gewarnt, gültiger Plan ok {durations}"


def t_render_clip(ctx: Ctx) -> str:
    out = ctx.home / "out" / VOD_ID
    shutil.rmtree(out, ignore_errors=True)
    code, _, _ = ctx.run("render_clip.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("render_clip.py", "--vod-id", VOD_ID, "--dry-run")
    expect(code == 0 and data["clips"] and not out.exists(), f"dry-run: exit {code} oder Dateien erzeugt")
    expect(data["clips"][0]["layout"] == "stack" and data["clips"][1]["layout"] == "blur",
           f"Layouts falsch: {[c['layout'] for c in data['clips']]}")
    plan_file = ctx.home / "work" / VOD_ID / "clips.json"
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    plan["clips"][0]["facecam"] = [1400, 0, 520, 300]
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, own, _ = ctx.run("render_clip.py", "--vod-id", VOD_ID, "--dry-run", "--clip", "clip01")
    cmd = " ".join(str(x) for x in (own.get("clips") or [{}])[0].get("ffmpeg") or [])
    expect("crop=520:300:1400:0" in cmd, f"facecam aus clips.json nicht genutzt: {cmd[-400:]}")
    expect("out_range=tv" in cmd, "Range-Konvertierung fehlt (Full-Range-Quellen -> yuvj420p)")
    del plan["clips"][0]["facecam"]
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, _, _ = ctx.run("manage_streamers.py", "set", STREAMER, "gameplay=0,400,1920,680")
    expect(code == 0, "gameplay= wurde nicht als game gespeichert")
    code, cropped, _ = ctx.run("render_clip.py", "--vod-id", VOD_ID, "--dry-run", "--clip", "clip01")
    cmd = " ".join(str(x) for x in (cropped.get("clips") or [{}])[0].get("ffmpeg") or [])
    expect("crop=1920:680:0:400" in cmd, f"Gameplay-Crop fehlt im Filter: {cmd[-400:]}")
    code, _, _ = ctx.run("manage_streamers.py", "set", STREAMER, "game=")
    expect(code == 0, "game ließ sich nicht zurücksetzen")
    code, data, err = ctx.run("render_clip.py", "--vod-id", VOD_ID, timeout=1800)
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-500:]}")
    expect(not data["warnings"], f"Warnungen beim Rendern: {data['warnings']}")
    for c in data["clips"]:
        expect(Path(c["out"]).is_file(), f"{c['id']} fehlt")
    enc = {c["id"]: f"{c['encoder']}/{c['layout']} {c['render_seconds']} s" for c in data["clips"]}
    # Frame aus dem Hook-Bereich (Sek. 1) und aus den Untertiteln (Sek. 6) zum Ansehen
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    for t in (1, 6):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i",
                        str(out / "clip01.mp4"), "-frames:v", "1", "-vf", "scale=540:-2",
                        str(ctx.home / f"frame_clip01_{t}s.png")], check=True)
    # Escaping-Test: ASS in einem Pfad mit Leerzeichen, Apostroph und Klammern
    odd = ctx.home / "pfad mit leer'zeichen [x], y"
    odd.mkdir(exist_ok=True)
    shutil.copyfile(ctx.home / "work" / VOD_ID / "render" / "clip01.ass", odd / "clip01.ass")
    probe = "import sys; sys.path.insert(0, sys.argv[1]); import _lib; print(_lib.escape_filter_path(sys.argv[2]))"
    esc = subprocess.run([sys.executable, "-c", probe, str(SCRIPTS), str(odd / "clip01.ass")],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                           "color=c=black:size=1080x1920:rate=30", "-t", "1", "-vf", f"subtitles=filename={esc}",
                           "-f", "null", "-"], capture_output=True, text=True)
    expect(proc.returncode == 0, f"subtitles= mit Sonderzeichen-Pfad scheitert: {proc.stderr[-300:]}")
    code, data, _ = ctx.run("render_clip.py", "--vod-id", VOD_ID)
    expect(code == 0 and all(c["status"] == "schon da" for c in data["clips"]), "zweiter Lauf nicht idempotent")
    return f"{enc}; Sonderzeichen-Pfad ok; Frames: frame_clip01_1s.png, frame_clip01_6s.png"


def t_detect_facecam(ctx: Ctx) -> str:
    """Offline-Teil: --help ohne opencv-Import, Rechteck-Helfer und Median.
    Die eigentliche Gesichtserkennung braucht echte Gesichter und ~39 MB Modelle;
    sie ist am echten VOD geprüft, nicht mit dem synthetischen Testmaterial."""
    code, _, _ = ctx.run("detect_facecam.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("detect_facecam.py", "--vod-id", VOD_ID)
    expect(code == 2, f"ohne --at/--clip sollte Exit 2 kommen, nicht {code}")
    probe = ("import sys, json; sys.path.insert(0, sys.argv[1]); import _lib, detect_facecam as d;"
             "print(json.dumps({'rect': [_lib.is_rect(v) for v in ([0,0,10,10],[0,0,0,10],[1,2,3],'x',[True,0,1,1])],"
             "'offsets': d.sample_offsets(5), 'median': d.median_rect([[0,0,10,10],[2,0,10,10],[100,0,10,10]])}))")
    proc = subprocess.run([ctx.uv, "run", "--quiet", "--with", "numpy", "python", "-c", probe, str(SCRIPTS)],
                          capture_output=True, text=True, encoding="utf-8")
    expect(proc.returncode == 0, f"Probe scheitert: {proc.stderr[-300:]}")
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    expect(got["rect"] == [True, False, False, False, False], f"is_rect falsch: {got['rect']}")
    expect(sorted(got["offsets"]) == [-8.0, -4.0, 4.0, 8.0], f"sample_offsets falsch: {got['offsets']}")
    expect(got["median"] == [2, 0, 10, 10], f"median_rect falsch: {got['median']}")
    return "--help ok, Eingabefehler -> Exit 2, Rechteck-Helfer und Median ok"


def t_verify_clip(ctx: Ctx) -> str:
    code, _, _ = ctx.run("verify_clip.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("verify_clip.py", "--vod-id", VOD_ID, "--loudness")
    expect(code == 0, f"echte Clips durchgefallen: {[r['errors'] for r in data.get('results', [])]}")
    lufs = {Path(r["file"]).stem: r.get("lufs") for r in data["results"]}
    # Gegenprobe: 16:9-Datei muss durchfallen
    wrong = ctx.home / "falsch_16x9.mp4"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=f=440:sample_rate=44100",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(wrong)], check=True)
    code, data, _ = ctx.run("verify_clip.py", "--file", str(wrong))
    expect(code == 5, f"16:9-Datei sollte Exit 5 liefern, war {code}")
    errs = data["results"][0]["errors"]
    expect(any("Auflösung" in e for e in errs) and any("Abtastrate" in e for e in errs), f"Fehler unvollständig: {errs}")
    code, _, _ = ctx.run("verify_clip.py", "--file", str(ctx.home / "gibtsnicht.mp4"))
    expect(code == 5, "fehlende Datei sollte Exit 5 liefern")
    return f"2 Clips ok, Lautheit {lufs}; 16:9-Datei und fehlende Datei -> Exit 5"


def t_render_resume(ctx: Ctx) -> str:
    """Regression: ein abgebrochenes Rendern (halbe MP4, kein Sidecar) darf nie als fertig gelten."""
    out = ctx.home / "out" / VOD_ID
    good = out / "clip02.mp4"
    expect(good.is_file() and (out / "clip02.json").is_file(), "render_clip-Stufe muss vorher laufen")
    # Zustand nach Abbruch nachbauen: halbe MP4, Sidecar fehlt (wird erst nach Erfolg geschrieben)
    data_full = good.read_bytes()
    (out / "clip02.json").unlink()
    good.write_bytes(data_full[: len(data_full) // 3])
    code, ver, _ = ctx.run("verify_clip.py", "--vod-id", VOD_ID)
    expect(code == 5 and any(Path(r["file"]).name == "clip02.mp4" and not r["ok"] for r in ver["results"]),
           f"verify_clip muss die MP4 ohne Sidecar melden: exit {code}, {ver.get('results')}")
    code, data, err = ctx.run("render_clip.py", "--vod-id", VOD_ID, "--clip", "clip02", timeout=1800)
    status = {c["id"]: c.get("status") for c in data.get("clips", [])}
    expect(code == 0 and status.get("clip02") == "gerendert",
           f"halbe MP4 ohne Sidecar muss neu gerendert werden, war {status} {err[-300:]}")
    expect(not list(out.glob("*.part.mp4")), "Zwischendatei .part.mp4 liegt noch herum")
    # Liegengebliebene Zwischendatei wird von verify_clip gemeldet
    leftover = out / "clip09.part.mp4"
    leftover.write_bytes(b"halb")
    code, ver, _ = ctx.run("verify_clip.py", "--vod-id", VOD_ID)
    leftover.unlink()
    expect(code == 5, f".part.mp4 muss gemeldet werden, exit {code}")
    code, ver, _ = ctx.run("verify_clip.py", "--vod-id", VOD_ID)
    expect(code == 0, f"nach Neu-Rendern muss alles grün sein: {ver.get('results')}")
    return "halbe MP4 ohne Sidecar -> verify Exit 5, render rendert neu; .part.mp4 wird gemeldet"


def t_log_clip(ctx: Ctx) -> str:
    db = ctx.home / "clips.db"
    db.unlink(missing_ok=True)
    code, _, _ = ctx.run("log_clip.py", "--help")
    expect(code == 0, "--help scheitert")
    code, data, _ = ctx.run("log_clip.py", "--vod-id", VOD_ID, "--dry-run")
    expect(code == 0 and not db.exists(), "dry-run hat clips.db angelegt")
    # Kandidaten-IDs leiten sich aus dem Moment ab (z. B. c231): Plan auf die echten IDs zeigen lassen
    work = ctx.home / "work" / VOD_ID
    cands = json.loads((work / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    plan = json.loads((work / "clips.json").read_text(encoding="utf-8"))
    for clip in plan["clips"]:
        start = min(s["start"] for s in clip["segments"])
        clip["candidate"] = min(cands, key=lambda c: abs(c["moment_s"] - start - 10))["id"]
    (work / "clips.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, data, _ = ctx.run("log_clip.py", "--vod-id", VOD_ID)
    expect(code == 0 and data["candidates"] >= 2 and data["selected"] == 2 and data["rendered"] == 2,
           f"Zählwerte falsch: {data}")
    code, data2, _ = ctx.run("log_clip.py", "--vod-id", VOD_ID)
    code, summ, _ = ctx.run("log_clip.py", "--summary")
    expect(summ["counts"]["candidates"] == data["candidates"] and summ["counts"]["clips"] == 2,
           f"nicht idempotent: {summ['counts']}")
    code, _, _ = ctx.run("log_clip.py", "--vod-id", "gibtsnicht")
    expect(code == 2, "unbekanntes VOD sollte Exit 2 liefern")
    hits = sentinel_hits(ctx)
    expect(not hits, f"Chat-Daten auf der Platte: {hits}")
    return f"{summ['counts']}, zweimal geloggt = gleiche Zeilen, kein Sentinel"


def t_timeout_json(ctx: Ctx) -> str:
    """Regression: ein Timeout in _lib.run warf TimeoutExpired -> Traceback statt einer JSON-Zeile."""
    slow = [sys.executable, "-c", "import time; time.sleep(5)"]
    half = "0.5"
    probe = (
        "import sys, json; sys.path.insert(0, sys.argv[1]); import _lib; _lib.setup('probe');"
        "p = _lib.run(json.loads(sys.argv[2]), timeout=" + half + ");"
        "print(json.dumps({'rc': p.returncode, 'err': p.stderr[-80:]}))"
    )
    proc = subprocess.run([sys.executable, "-c", probe, str(SCRIPTS), json.dumps(slow)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    expect(proc.returncode == 0 and "Traceback" not in proc.stderr, f"Timeout wirft noch: {proc.stderr[-300:]}")
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    expect(res["rc"] == 124 and "Timeout" in res["err"], f"Timeout-Ergebnis falsch: {res}")
    # check=True: genau eine JSON-Zeile mit ok=false und Exit 1, kein Traceback
    probe_check = (
        "import sys, json; sys.path.insert(0, sys.argv[1]); import _lib; _lib.setup('probe');"
        "_lib.run(json.loads(sys.argv[2]), timeout=" + half + ", check=True)"
    )
    proc = subprocess.run([sys.executable, "-c", probe_check, str(SCRIPTS), json.dumps(slow)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    expect(proc.returncode == 1 and len(lines) == 1 and json.loads(lines[0])["ok"] is False
           and "Traceback" not in proc.stderr, f"check=True: exit {proc.returncode}, stdout {proc.stdout[-200:]}")
    return "Timeout -> returncode 124 bzw. eine JSON-Zeile mit Exit 1, kein Traceback"


def t_status(ctx: Ctx) -> str:
    code, data, _ = ctx.run("status.py", "--brief")
    expect(code == 0 and data.get("ok"), f"status: {data}")
    vod = next((v for v in data["vods"] if v["vod"] == VOD_ID), None)
    expect(vod and vod["rendered"] == 2 and vod["next"] == "verify_clip/log_clip", f"VOD-Stand falsch: {vod}")
    expect(STREAMER in data["config"]["streamers"] and STREAMER not in data["config"]["facecam_offen"],
           f"Config-Stand falsch: {data['config']}")
    saved_home = os.environ.get("CLIPS_HOME")
    os.environ["CLIPS_HOME"] = str(ctx.home / "gibtsnicht")
    try:
        proc = subprocess.run([ctx.uv, "run", "--quiet", str(SCRIPTS / "status.py")], capture_output=True,
                              text=True, encoding="utf-8")
    finally:
        if saved_home is None:
            os.environ.pop("CLIPS_HOME", None)
        else:
            os.environ["CLIPS_HOME"] = saved_home
    expect(proc.returncode == 0, "status.py muss auch ohne Datenordner Exit 0 liefern")
    return f"Stand: {vod['rendered']} Clips, nächster Schritt '{vod['next']}'"


# ------------------------------------------------------------------ Live-Modus (Skripte liegen im selben scripts/-Ordner)
LIVE_SCRIPTS = SCRIPTS
LIVE_SET = ["--set", "chat_window_s=5", "--set", "chat_baseline_s=30", "--set", "confirm_s=2",
            "--set", "cooldown_s=30", "--set", "min_uniq=5"]


class FakeIrc:
    """Mini-IRC-Server: ruhiger Chat, Ausbruch ab burst_at Sekunden nach dem JOIN, ein PING."""

    def __init__(self, burst_at: float = 40.0, sentinel: str = "sentinel_user_zz9") -> None:
        import socket
        import threading
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.burst_at, self.sentinel = burst_at, sentinel
        self.got_pong = False
        self.joined = threading.Event()
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        import random
        self.srv.settimeout(1.0)
        while not self.stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                continue
            conn.settimeout(0.2)
            rng = random.Random(7)
            start = None
            sent_burst = False
            next_msg = 0.0
            pinged = False
            buf = b""
            while not self.stop.is_set():
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    if b"JOIN" in buf and start is None:
                        start = time.time()
                        self.joined.set()
                    if b"PONG" in buf:
                        self.got_pong = True
                except OSError:
                    pass
                if start is None:
                    continue
                now = time.time() - start
                try:
                    if not pinged and now > 3:
                        conn.sendall(b"PING :tmi.twitch.tv\r\n")
                        pinged = True
                    if now >= next_msg:
                        u = f"ruhig{rng.randint(1, 6)}"
                        conn.sendall(f":{u}!{u}@{u}.tmi.twitch.tv PRIVMSG #teststreamer :hallo chat\r\n".encode())
                        next_msg = now + 1.0
                    if not sent_burst and now >= self.burst_at:
                        lines = []
                        for i in range(60):
                            u = self.sentinel if i == 30 else f"hype{i % 40}"
                            body = "SENTINELTEXT_qq7 KEKW" if i == 30 else rng.choice(["KEKW", "OMEGALUL", "KEKW KEKW", "LUL"])
                            lines.append(f":{u}!{u}@{u}.tmi.twitch.tv PRIVMSG #teststreamer :{body}\r\n")
                        conn.sendall("".join(lines).encode())
                        sent_burst = True
                except OSError:
                    break
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self.srv.close()


def live_run(ctx: Ctx, script: str, *args: str, timeout: float = 300) -> tuple[int, dict, str]:
    cmd = [ctx.uv, "run", "--quiet", str(LIVE_SCRIPTS / script), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    try:
        data = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        data = {"_raw_stdout": proc.stdout[-500:]}
    return proc.returncode, data, proc.stderr


def t_live_chat_monitor(ctx: Ctx) -> str:
    code, _, _ = live_run(ctx, "live_chat_monitor.py", "--help")
    expect(code == 0, "--help scheitert")
    out = ctx.home / "live_chat_test"
    shutil.rmtree(out, ignore_errors=True)
    irc = FakeIrc(burst_at=40)
    try:
        code, data, err = live_run(ctx, "live_chat_monitor.py", "--channel", "teststreamer", "--out", str(out),
                                   "--seconds", "52", "--irc-host", "127.0.0.1", "--irc-port", str(irc.port),
                                   "--no-tls", *LIVE_SET, timeout=120)
    finally:
        irc.close()
    expect(code == 0, f"exit {code}: {data.get('error')} {err[-400:]}")
    expect(irc.got_pong, "auf PING kam kein PONG")
    spikes = data["spikes"]
    expect(len(spikes) == 1, f"erwartet genau 1 Spike, gefunden {spikes}")
    expect(39 <= spikes[0]["t"] <= 45 and "funny" in spikes[0]["labels"], f"Spike falsch: {spikes[0]}")
    lines = (out / "chat_counts.jsonl").read_text(encoding="utf-8").splitlines()
    expect(len(lines) >= 45, f"nur {len(lines)} Sekunden gezählt")
    hits = sentinel_hits(ctx)
    expect(not hits, f"Chat-Daten auf der Platte: {hits}")
    return f"{data['messages']} Nachrichten, Spike bei t={spikes[0]['t']} {spikes[0]['labels']}, PONG ok, nur Zählwerte"


def t_live_record(ctx: Ctx) -> str:
    """Live-Stub: lokaler HTTP-Server liefert 90 s HLS in Echtzeit, Fake-IRC liefert den Chat."""
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ctx.fixtures))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    irc = FakeIrc(burst_at=82)
    rec_root = ctx.home / "live_rec"
    url = f"hls://http://127.0.0.1:{httpd.server_address[1]}/live/clip.m3u8"
    try:
        for script in ("record_start.py", "record_status.py", "record_stop.py", "live_prepare.py"):
            code, _, _ = live_run(ctx, script, "--help")
            expect(code == 0, f"{script} --help scheitert")
        base = ["--channel", "teststreamer", "--source-url", url, "--test-realtime", "--irc-host", "127.0.0.1",
                "--irc-port", str(irc.port), "--no-tls", "--set", f"record_dir={rec_root}", "--set", "segment_s=20",
                *LIVE_SET]
        code, data, _ = live_run(ctx, "record_start.py", "--channel", "teststreamer", "--set", "segment_s=zehn",
                                 "--dry-run")
        expect(code == 2 and data.get("ok") is False, f"--set mit kaputtem Wert sollte Exit 2 + JSON liefern: {code}")
        code, data, _ = live_run(ctx, "record_start.py", *base, "--dry-run")
        expect(code == 0 and data.get("dry_run") and not (rec_root / data["rec_id"]).exists(), "dry-run hat Dateien erzeugt")
        t_start = time.time()
        code, data, err = live_run(ctx, "record_start.py", *base)
        expect(code == 0 and data["status"] == "recording", f"record_start: {data.get('error')} {err[-300:]}")
        expect(time.time() - t_start < 30, "record_start blockiert zu lange")
        rec_id = data["rec_id"]
        code, again, _ = live_run(ctx, "record_start.py", *base)
        expect(again.get("status") == "läuft schon" and again["rec_id"] == rec_id, "zweiter Start nicht idempotent")
        code, st, _ = live_run(ctx, "record_status.py", "--rec-id", rec_id)
        expect(st["recordings"][0]["alive"], "Worker lebt nicht")
        code, sp, _ = live_run(ctx, "record_status.py", "--rec-id", rec_id, "--wait-spike", "--timeout", "150",
                               timeout=200)
        expect(sp.get("new_spikes"), f"kein Spike gemeldet: {sp}")
        spike = sp["new_spikes"][0]
        code, data, _ = live_run(ctx, "record_stop.py", "--rec-id", rec_id, timeout=120)
        expect(code == 0 and data["stopped"][0]["status"] in ("stopped", "ended"), f"stop: {data}")
        code, data, _ = live_run(ctx, "record_stop.py", "--rec-id", rec_id)
        expect(data["stopped"][0]["result"] == "läuft schon nicht mehr", "zweiter Stopp nicht idempotent")
    finally:
        irc.close()
        httpd.shutdown()
    # Brücke in die VOD-Pipeline
    code, prep, err = live_run(ctx, "live_prepare.py", "--rec-id", rec_id)
    expect(code == 0 and prep["segments"] >= 4, f"live_prepare: {prep.get('error')} {err[-300:]}")
    code, det, _ = ctx.run("detect_highlights.py", "--vod-id", rec_id)
    top = det["top"][0]
    expect(abs(top["moment_s"] - 60) <= 4 and "chat-spike" in top["reasons"] and "laut" in top["reasons"],
           f"Live-Kandidat falsch: {det['top']} (erwartet ~60 s mit Chat und Audio)")
    code, sec, _ = ctx.run("fetch_sections.py", "--vod-id", rec_id, "--max", "1")
    expect(code == 0, f"fetch_sections live: {sec.get('error')}")
    code, tr, _ = ctx.run("transcribe.py", "--vod-id", rec_id, timeout=900)
    words = []
    for f in (ctx.home / "work" / rec_id / "transcripts").glob("*.json"):
        words += json.loads(f.read_text(encoding="utf-8"))["words"]
    gibt = next((w for w in words if w["w"].lower().startswith("gibt")), None)
    expect(gibt and abs(gibt["s"] - 53.0) <= 2.0, f"'gibt' bei {gibt and gibt['s']} statt ~53 s (Live-Zeitachse?)")
    plan = good_plan()
    plan["vod_id"] = rec_id
    plan["clips"] = [dict(plan["clips"][0], segments=[{"start": 60.0, "end": 62.5, "role": "cold_open"},
                                                      {"start": 44.0, "end": 72.0}])]
    (ctx.home / "work" / rec_id / "clips.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, val, _ = ctx.run("validate_clips.py", "--vod-id", rec_id)
    expect(code == 0, f"validate live: {val.get('errors')}")
    code, ren, err = ctx.run("render_clip.py", "--vod-id", rec_id, timeout=900)
    expect(code == 0, f"render live: {ren.get('error')} {err[-300:]}")
    code, ver, _ = ctx.run("verify_clip.py", "--vod-id", rec_id)
    expect(code == 0, f"verify live: {[r['errors'] for r in ver.get('results', [])]}")
    hits = sentinel_hits(ctx)
    expect(not hits, f"Chat-Daten auf der Platte: {hits}")
    return (f"{rec_id}: Spike t={spike['t']} -> Moment {spike['moment']}, Kandidat {top['moment_s']} s "
            f"{top['reasons']}, 'gibt' bei {gibt['s']} s, Clip {ren['clips'][0]['duration_s']} s gerendert und geprüft")


def t_from_audio(ctx: Ctx) -> str:
    """Kandidatenfenster aus der Tonspur, bevor ein Videoabschnitt existiert."""
    code, data, err = ctx.run("transcribe.py", "--vod-id", VOD_ID, "--from-audio", "--dry-run")
    expect(code == 0 and data.get("source") == "audio" and data.get("would_transcribe"),
           f"from-audio: exit {code} {data.get('error')} {err[-300:]}")
    tdir = ctx.home / "work" / VOD_ID / "transcripts"
    expect(not tdir.exists(), "dry-run hat ein Transkript geschrieben")
    audio_dir = ctx.home / "work" / VOD_ID / "audio"
    windows = list(audio_dir.glob("cand_*.window.json"))
    expect(windows, "keine Fenster-Metadaten")
    strong = expected(ctx)["events"][1]["moment"]
    covered = False
    for path in windows:
        meta = json.loads(path.read_text(encoding="utf-8"))
        wav = audio_dir / f"cand_{meta['id']}.wav"
        expect(wav.is_file() and "offset_s" in meta, f"Fenster unvollständig: {meta} wav={wav.is_file()}")
        if meta["offset_s"] <= strong <= meta["end_s"]:
            covered = True
    expect(covered, f"starkes Ereignis {strong} liegt in keinem Audio-Fenster")
    return f"{len(windows)} Fenster aus der Tonspur, Whisper nicht gestartet"


def t_whole_section(ctx: Ctx) -> str:
    """Kurze lokale Datei wird ein Abschnitt, das Original bleibt."""
    short = ctx.home / "short.mp4"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                           "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=30",
                           "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                           "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                           "-shortest", str(short)], capture_output=True, text=True)
    expect(proc.returncode == 0 and short.is_file(), proc.stderr[-300:])
    before = short.read_bytes()
    code, data, err = ctx.run("fetch_signals.py", "--input", str(short), "--vod-id", "shorty",
                              "--streamer", STREAMER)
    expect(code == 0, f"fetch_signals kurz: {data.get('error')} {err[-300:]}")
    code, data, err = ctx.run("fetch_sections.py", "--vod-id", "shorty", "--whole")
    expect(code == 0 and data.get("mode") == "local-whole", f"--whole: {data.get('error')} {err[-300:]}")
    secs = json.loads((ctx.home / "work" / "shorty" / "sections.json").read_text(encoding="utf-8"))["sections"]
    expect(len(secs) == 1 and secs[0]["start"] == 0.0 and secs[0]["status"] == "lokal"
           and 11 <= secs[0]["end"] <= 13 and Path(secs[0]["file"]).resolve() == short.resolve(),
           f"Abschnitt falsch: {secs}")
    expect(short.read_bytes() == before, "Originalaufnahme wurde verändert")
    return f"ein Abschnitt 0..{secs[0]['end']} s, Original unverändert"


STAGES = {
    "check_env": t_check_env,
    "validate_config": t_validate_config,
    "fetch_signals": t_fetch_signals,
    "chat_json_safety": t_chat_json_safety,
    "detect_highlights": t_detect_highlights,
    "from_audio": t_from_audio,
    "whole_section": t_whole_section,
    "fetch_sections": t_fetch_sections,
    "manage_streamers": t_manage_streamers,
    "transcribe": t_transcribe,
    "sections_merge": t_sections_merge,
    "validate_clips": t_validate_clips,
    "render_clip": t_render_clip,
    "detect_facecam": t_detect_facecam,
    "verify_clip": t_verify_clip,
    "render_resume": t_render_resume,
    "log_clip": t_log_clip,
    "status": t_status,
    "timeout_json": t_timeout_json,
    "live_chat_monitor": t_live_chat_monitor,
    "live_record": t_live_record,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Selbsttests mit synthetischem Material.")
    parser.add_argument("--home", type=Path,
                        default=Path(tempfile.gettempdir()) / "clipping-streams-selftest",
                        help="Test-Datenordner (wird als CLIPS_HOME genutzt)")
    parser.add_argument("--fixtures", type=Path, help="Ordner mit vod.mp4/chat.json (Standard: <home>/../fixtures)")
    parser.add_argument("--only", nargs="*", help="nur diese Stufen")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    home = args.home.resolve()
    if home == (Path.home() / "clips").resolve():
        sys.exit("Abbruch: Tests laufen nie gegen ~/clips")
    home.mkdir(parents=True, exist_ok=True)
    fixtures = (args.fixtures or home.parent / "fixtures").resolve()
    ctx = Ctx(home, fixtures)
    if not (fixtures / "vod.mp4").exists():
        print(f"[tests] erzeuge Testmaterial in {fixtures}", file=sys.stderr)
        subprocess.run([ctx.uv, "run", "--quiet", str(TESTS / "make_fixtures.py"), "--out", str(fixtures)],
                       check=True)

    results = []
    for name, func in STAGES.items():
        if args.only and name not in args.only:
            continue
        start = time.monotonic()
        try:
            detail = func(ctx)
            status = "pass"
        except Fail as exc:
            detail, status = str(exc), "fail"
        except Exception as exc:  # noqa: BLE001
            detail, status = f"{exc.__class__.__name__}: {exc}", "error"
        took = round(time.monotonic() - start, 1)
        results.append({"stage": name, "status": status, "seconds": took, "detail": detail})
        print(f"[tests] {name}: {status} ({took} s) {detail}", file=sys.stderr)
    ok = all(r["status"] == "pass" for r in results)
    print(json.dumps({"ok": ok, "home": str(home), "results": results}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
