"""Aufnahme-Prozess (wird von record_start.py losgelöst gestartet, nie direkt aufrufen).

streamlink --stdout -> (Python-Weiterleitung) -> ffmpeg -f segment (10-Minuten-.ts-Segmente)
+ Chat-Thread (live_chat_monitor.ChatMonitor): Zählwerte + SPIKE-Zeilen ins Log.

Zeitachse: t=0 ist das erste Byte von streamlink. Chat-Sekunden und Segmentzeiten
(segments.csv, auf das erste Segment normiert) nutzen dieselbe Achse.
Stoppt bei: Stream-Ende, Stopp-Datei, max_hours, zu wenig Speicher, ffmpeg-Fehler.
Status alle 5 s in state/live_<rec_id>.json, Log in logs/live_<rec_id>.log.

Aufruf (nur durch record_start.py): live_worker.py --rec-id <id> --spec <spec.json>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import live_common
from live_common import _lib

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CHUNK = 64 * 1024


class Worker:
    def __init__(self, rec_id: str, spec: dict[str, Any]) -> None:
        self.rec_id, self.spec = rec_id, spec
        self.rec_dir = Path(spec["rec_dir"])
        self.state: dict[str, Any] = {
            "rec_id": rec_id, "channel": spec["channel"], "streamer": spec["streamer"], "url": spec["url"],
            "pid": os.getpid(), "status": "starting", "rec_dir": str(self.rec_dir), "log": str(live_common.log_file(rec_id)),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "t0": None, "bytes": 0, "segments": 0,
            "last_data_at": None, "free_gb": None, "ads": [], "spikes": 0, "reason": None, "chat": not spec["no_chat"],
        }
        self.t0: float | None = None
        self.log = logging.getLogger("live_worker")

    def save(self) -> None:
        _lib.write_json(live_common.state_file(self.rec_id), self.state)

    def _stderr_reader(self, stream: Any, name: str) -> None:
        """streamlink/ffmpeg-Meldungen ins Log; Werbepausen merken."""
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            self.log.info("[%s] %s", name, line)
            low = line.lower()
            if "advertisement" in low or "ad break" in low or "filtering out segments" in low:
                t = None if self.t0 is None else round(time.time() - self.t0, 1)
                self.state["ads"].append({"t": t, "line": line[-160:]})

    def run(self) -> None:
        spec = self.spec
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.save()
        sl_cmd = [spec["streamlink"], "--stdout", "--loglevel", "info", "--retry-open", "3",
                  *spec.get("streamlink_args", []), spec["url"], spec["quality"]]
        ff_cmd = [spec["ffmpeg"], "-hide_banner", "-loglevel", "warning",
                  *(["-re"] if spec.get("realtime") else []), "-i", "pipe:0",
                  "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-f", "segment",
                  "-segment_time", str(spec["live"]["segment_s"]), "-segment_format", "mpegts",
                  "-segment_list", str(self.rec_dir / "segments.csv"), "-segment_list_type", "csv",
                  "-reset_timestamps", "0", str(self.rec_dir / "seg_%04d.ts")]
        self.log.info("starte streamlink: %s", " ".join(sl_cmd))
        sl = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                              creationflags=NO_WINDOW)
        ff = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              creationflags=NO_WINDOW)
        self.state["child_pids"] = [sl.pid, ff.pid]
        threading.Thread(target=self._stderr_reader, args=(sl.stderr, "streamlink"), daemon=True).start()
        threading.Thread(target=self._stderr_reader, args=(ff.stderr, "ffmpeg"), daemon=True).start()

        chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=256)

        def reader() -> None:
            assert sl.stdout is not None
            while True:
                data = sl.stdout.read1(CHUNK) if hasattr(sl.stdout, "read1") else sl.stdout.read(CHUNK)
                if not data:
                    chunks.put(None)
                    return
                chunks.put(data)

        threading.Thread(target=reader, daemon=True).start()

        monitor = None
        if not spec["no_chat"]:
            from live_chat_monitor import ChatMonitor, spike_line  # noqa: PLC0415

            def on_spike(s: dict) -> None:
                self.state["spikes"] += 1
                self.log.info(spike_line(s))

            monitor = ChatMonitor(spec["channel"], self.rec_dir, spec["settings"], spec["live"], lambda: self.t0,
                                  on_spike, self.log.info, spec.get("irc_host", "irc.chat.twitch.tv"),
                                  int(spec.get("irc_port", 6697)), bool(spec.get("irc_tls", True)))
            monitor.start()

        stop_path = live_common.stop_file(self.rec_id)
        max_s = float(spec["live"]["max_hours"]) * 3600
        min_free = float(spec["live"]["min_free_gb"])
        last_save = 0.0
        reason = "ended"
        try:
            while True:
                try:
                    data = chunks.get(timeout=1.0)
                except queue.Empty:
                    data = b""
                if data is None:
                    reason = "ended" if sl.wait() == 0 else f"streamlink_exit_{sl.returncode}"
                    break
                if data:
                    if self.t0 is None:
                        self.t0 = time.time()
                        self.state.update({"t0": self.t0, "status": "recording"})
                        self.log.info("erstes Byte, Aufnahme läuft (t=0)")
                        self.save()
                    try:
                        ff.stdin.write(data)
                    except (BrokenPipeError, OSError):
                        reason = "ffmpeg_failed"
                        break
                    self.state["bytes"] += len(data)
                    self.state["last_data_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                now = time.time()
                if now - last_save >= 5:
                    last_save = now
                    self.state["segments"] = len(live_common.read_segments(self.rec_dir))
                    self.state["free_gb"] = round(shutil.disk_usage(self.rec_dir).free / 1024**3, 1)
                    self.save()
                    if stop_path.exists():
                        reason = "stopped"
                        break
                    if self.t0 and now - self.t0 > max_s:
                        reason = "max_duration"
                        break
                    if self.state["free_gb"] < min_free:
                        reason = "disk_full"
                        break
                    if ff.poll() is not None:
                        reason = "ffmpeg_failed"
                        break
        except Exception as exc:  # noqa: BLE001
            self.log.exception("Worker-Fehler")
            reason = f"error: {exc}"
        finally:
            self.log.info("beende Aufnahme (%s)", reason)
            try:
                ff.stdin.close()
            except OSError:
                pass
            if sl.poll() is None:
                sl.terminate()
            try:
                ff.wait(timeout=60)
            except subprocess.TimeoutExpired:
                ff.kill()
            try:
                sl.wait(timeout=15)
            except subprocess.TimeoutExpired:
                sl.kill()
            if monitor:
                monitor.close()
            self.state["segments"] = len(live_common.read_segments(self.rec_dir))
            self.state.update({"status": "stopped" if reason == "stopped" else
                               ("ended" if reason == "ended" else "error"),
                               "reason": reason, "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "duration_s": round(time.time() - self.t0, 1) if self.t0 else 0})
            self.save()
            stop_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interner Aufnahme-Prozess (von record_start.py gestartet).")
    parser.add_argument("--rec-id", required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    log_path = live_common.log_file(args.rec_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=str(log_path), encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    Worker(args.rec_id, spec).run()


if __name__ == "__main__":
    main()
