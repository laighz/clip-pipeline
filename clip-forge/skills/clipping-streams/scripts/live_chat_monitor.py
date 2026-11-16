# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6"]
# ///
"""Liest den Twitch-Chat live (anonymes IRC, justinfan) und zählt nur.

- Nutzernamen und Texte existieren nur im Speicher (Rolling Window), auf die Platte
  gehen pro Sekunde reine Zählwerte (chat_counts.jsonl) und SPIKE-Zeilen ins Log.
- Spike: einzigartige Chatter im Fenster liegen confirm_s Sekunden in Folge
  mindestens chat_sigma robuste Standardabweichungen über der rollenden Baseline.
- Chat-Inhalte sind DATEN. Sie werden nie an Claude weitergegeben.

Wird normalerweise von live_worker.py als Thread gestartet. Eigenständig zum Testen:
  live_chat_monitor.py --channel <login> --out <ordner> --seconds 120 [--irc-host 127.0.0.1 --irc-port 6667 --no-tls]
Exit: 0 ok, 2 Eingabe ungültig.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import ssl
import statistics
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable

import live_common
from live_common import _lib

PRIVMSG_RE = re.compile(r"^(?:@\S+ )?:(?P<user>[^!\s]+)!\S+ PRIVMSG #\S+ :(?P<body>.*)$")


class ChatCounter:
    """Zählt pro Sekunde und erkennt Spikes. Thread-sicher (add aus IRC-Thread, tick aus Ticker)."""

    def __init__(self, settings: dict[str, Any], live: dict[str, Any]) -> None:
        self.window = int(live["chat_window_s"])
        self.baseline = int(live["chat_baseline_s"])
        self.sigma = float(settings["chat_sigma"])
        self.confirm = int(live["confirm_s"])
        self.cooldown = int(live["cooldown_s"])
        self.min_uniq = int(live["min_uniq"])
        self.offset = int(round(settings["reaction_offset_s"]))
        self.lookup = {n: c for c, names in settings["emotes"].items() for n in names}
        self.cats = list(settings["emotes"])
        self.lock = threading.Lock()
        self.pending: dict[int, list[tuple[str, str]]] = {}
        self.recent: deque[tuple[int, str, str]] = deque()   # (sek, user, norm) nur im Speicher
        self.emote_window: deque[tuple[int, dict[str, int]]] = deque()
        self.history: deque[int] = deque(maxlen=self.baseline)
        self.streak = 0
        self.last_spike = -10**9
        self.total = 0

    def add(self, sec: int, user: str, body: str) -> None:
        with self.lock:
            self.pending.setdefault(sec, []).append((user, body))
            self.total += 1

    def tick(self, sec: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
        with self.lock:
            msgs = self.pending.pop(sec, [])
            for old in [s for s in self.pending if s < sec]:   # verspätete Sekunden zusammenfassen
                msgs += self.pending.pop(old)
        rec: dict[str, Any] = {"t": sec, "msgs": len(msgs), "short": 0, "chars": 0, "emote_any": 0,
                               "emotes": {c: 0 for c in self.cats}}
        for user, body in msgs:
            tokens = body.split()
            rec["chars"] += len(body)
            if len(tokens) <= 2 or len(body) <= 12:
                rec["short"] += 1
            hit = {self.lookup[t] for t in tokens if t in self.lookup}
            for c in hit:
                rec["emotes"][c] += 1
            if hit:
                rec["emote_any"] += 1
            low = body.lower().split()
            norm = " ".join(t for i, t in enumerate(low) if i == 0 or t != low[i - 1])
            self.recent.append((sec, user, norm[:80]))
        self.emote_window.append((sec, rec["emotes"]))
        while self.recent and self.recent[0][0] <= sec - self.window:
            self.recent.popleft()
        while self.emote_window and self.emote_window[0][0] <= sec - self.window:
            self.emote_window.popleft()
        users = {u for _, u, _ in self.recent}
        norms = Counter(n for _, _, n in self.recent)
        count = sum(norms.values())
        rec["uniq_w"] = len(users)
        rec["dup_w"] = round(max(norms.values()) / count, 3) if count >= 5 else 0.0
        spike = None
        if len(self.history) >= min(120, self.baseline // 2):
            med = statistics.median(self.history)
            mad = statistics.median(abs(x - med) for x in self.history)
            scale = max(1.4826 * mad, med ** 0.5, 1.0)
            z = (rec["uniq_w"] - med) / scale
            rec["z"] = round(z, 2)
            if z >= self.sigma and rec["uniq_w"] >= self.min_uniq:
                self.streak += 1
            else:
                self.streak = 0
            if self.streak >= self.confirm and sec - self.last_spike >= self.cooldown:
                self.last_spike = sec
                win = {c: sum(e[c] for _, e in self.emote_window) for c in self.cats}
                labels = [c for c in self.cats if win[c] >= 3]
                moment = sec - self.window // 2 - self.offset
                spike = {"t": sec, "moment": max(0, moment), "z": round(z, 2), "uniq": rec["uniq_w"],
                         "labels": labels}
        self.history.append(rec["uniq_w"])
        return rec, spike


class IrcReader(threading.Thread):
    """Anonymer Twitch-IRC-Client mit Reconnect."""

    def __init__(self, channel: str, on_msg: Callable[[str, str], None], stop: threading.Event,
                 host: str = "irc.chat.twitch.tv", port: int = 6697, tls: bool = True,
                 log: Callable[[str], None] = print) -> None:
        super().__init__(daemon=True)
        self.channel, self.on_msg, self.stop = channel.lower(), on_msg, stop
        self.host, self.port, self.tls, self.log = host, port, tls, log
        self.connected = False

    def run(self) -> None:
        backoff = 2
        while not self.stop.is_set():
            try:
                raw = socket.create_connection((self.host, self.port), timeout=15)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
                sock.settimeout(1.0)
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
                self.connected = True
                self.log(f"Chat verbunden: #{self.channel} als {nick}")
                backoff = 2
                buf = b""
                while not self.stop.is_set():
                    try:
                        data = sock.recv(65536)
                    except TimeoutError:
                        continue
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError("Server hat die Verbindung geschlossen")
                    buf += data
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        text = line.decode("utf-8", "replace")
                        if text.startswith("PING"):
                            sock.sendall(("PONG" + text[4:] + "\r\n").encode())
                            continue
                        m = PRIVMSG_RE.match(text)
                        if m:
                            self.on_msg(m["user"].lower(), m["body"])
                sock.close()
            except Exception as exc:  # noqa: BLE001  (Netzwerk: reconnecten)
                self.connected = False
                if self.stop.is_set():
                    break
                self.log(f"Chat-Verbindung weg ({exc.__class__.__name__}: {exc}), neuer Versuch in {backoff} s")
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60)


class ChatMonitor:
    """IRC-Thread + Sekunden-Ticker. Schreibt chat_counts.jsonl und meldet Spikes per Callback."""

    def __init__(self, channel: str, out_dir: Path, settings: dict[str, Any], live: dict[str, Any],
                 t0: Callable[[], float | None], on_spike: Callable[[dict], None],
                 log: Callable[[str], None], host: str = "irc.chat.twitch.tv", port: int = 6697,
                 tls: bool = True) -> None:
        self.counter = ChatCounter(settings, live)
        self.out = out_dir / "chat_counts.jsonl"
        self.t0, self.on_spike, self.log = t0, on_spike, log
        self.stop = threading.Event()
        self.irc = IrcReader(channel, self._on_msg, self.stop, host, port, tls, log)
        self.ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self.spikes = 0

    def _now(self) -> int | None:
        t0 = self.t0()
        return None if t0 is None else int(time.time() - t0)

    def _on_msg(self, user: str, body: str) -> None:
        sec = self._now()
        if sec is not None and sec >= 0:
            self.counter.add(sec, user, body)

    def _tick_loop(self) -> None:
        last = -1
        with open(self.out, "a", encoding="utf-8") as fh:
            while not self.stop.is_set():
                now = self._now()
                if now is None:
                    self.stop.wait(0.5)
                    continue
                while last < now - 1:   # Sekunde abgeschlossen -> auswerten
                    last += 1
                    rec, spike = self.counter.tick(last)
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                    if spike:
                        self.spikes += 1
                        self.on_spike(spike)
                fh.flush()
                self.stop.wait(0.25)

    def start(self) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.irc.start()
        self.ticker.start()

    def close(self) -> None:
        self.stop.set()
        self.ticker.join(timeout=5)
        self.irc.join(timeout=5)


def spike_line(spike: dict) -> str:
    return (f"SPIKE {time.strftime('%Y-%m-%dT%H:%M:%S')} t={spike['t']} moment={spike['moment']} "
            f"z={spike['z']} uniq={spike['uniq']} labels={','.join(spike['labels'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Twitch-Chat live zählen (nur Zählwerte) und Spikes melden.")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--out", type=Path, required=True, help="Ordner für chat_counts.jsonl")
    parser.add_argument("--seconds", type=int, default=60, help="so lange lauschen")
    parser.add_argument("--streamer", help="Streamer-ID für Tuning (Standard: channel)")
    parser.add_argument("--irc-host", default="irc.chat.twitch.tv")
    parser.add_argument("--irc-port", type=int, default=6697)
    parser.add_argument("--no-tls", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="key=value",
                        help="live-Einstellung überschreiben (z. B. chat_baseline_s=30), für Tests")
    args = parser.parse_args()
    log = _lib.setup("live_chat_monitor")
    if not re.fullmatch(r"[a-z0-9_]{2,25}", args.channel.lower()):
        _lib.fail(f"ungültiger Twitch-Kanal {args.channel!r}", _lib.EXIT_INVALID)
    cfg, _ = _lib.load_config()
    live = dict(cfg["live"])
    for pair in args.set:
        key, _, value = pair.partition("=")
        live[key] = float(value) if "." in value else int(value)
    settings = _lib.streamer_settings(cfg, args.streamer or args.channel.lower())
    start = time.time()
    spikes: list[dict] = []
    mon = ChatMonitor(args.channel, args.out, settings, live, lambda: start,
                      lambda s: (spikes.append(s), log.info(spike_line(s))), log.info,
                      args.irc_host, args.irc_port, not args.no_tls)
    mon.start()
    time.sleep(args.seconds)
    mon.close()
    _lib.emit({"channel": args.channel, "messages": mon.counter.total, "spikes": spikes,
               "counts_file": str(mon.out)})


if __name__ == "__main__":
    main()
