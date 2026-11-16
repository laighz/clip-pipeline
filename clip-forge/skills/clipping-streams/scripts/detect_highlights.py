# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["pyyaml>=6", "numpy>=1.26"]
# ///
"""Findet Highlight-Kandidaten aus signals.json und schreibt candidates.json.

Signale (alle pro Sekunde, VOD-Zeit):
  - Chat: einzigartige Chatter im 25-s-Fenster als robuster z-Score gegen eine
    rollende Baseline (Median/MAD), Anteil kurzer Nachrichten, Emote-Kategorien.
    Der Chat reagiert verzögert: Ein Chat-Peak bei t zeigt auf den Moment t - reaction_offset_s.
  - Audio: Lautheit im Stimmband (300-3400 Hz), robuster z-Score.
  - Zuschauer-Clips (Helix): Bonus, gewichtet mit log(Views).
Unter min_chat_per_hour zählt Audio mehr als Chat. Momente in stummen
(gemuteten) Abschnitten werden verworfen.

Aufruf: detect_highlights.py --vod-id <id> [--top 20] [--dry-run]
Exit: 0 ok (auch bei 0 Kandidaten), 2 Eingabe fehlt/ungültig.
"""
from __future__ import annotations

import argparse
from typing import Any

import numpy as np

import _lib

AUDIO_SMOOTH_S = 3
TOLERANCE_S = 5          # Unschärfe des Reaktionsversatzes
AUDIO_PEAK_Z = 2.0
MIN_SCORE = 1.5


def rolling_sum(x: np.ndarray, width: int) -> np.ndarray:
    """Zentrierte Summe über `width` Sekunden."""
    kernel = np.ones(width)
    return np.convolve(x, kernel, mode="same")


def rolling_max(x: np.ndarray, before: int, after: int) -> np.ndarray:
    out = np.empty_like(x)
    n = len(x)
    for i in range(n):
        out[i] = x[max(0, i - before): min(n, i + after + 1)].max()
    return out


def robust_z(x: np.ndarray, baseline_s: int, min_scale: float, step: int = 5,
             exclude: np.ndarray | None = None) -> np.ndarray:
    """z = (x - rollender Median) / (1.4826 * MAD), Baseline zentriert über baseline_s."""
    n = len(x)
    half = max(baseline_s // 2, 30)
    centers = np.arange(0, n, step)
    med = np.empty(len(centers))
    scale = np.empty(len(centers))
    valid = np.ones(n, dtype=bool) if exclude is None else ~exclude
    for k, c in enumerate(centers):
        lo, hi = max(0, c - half), min(n, c + half + 1)
        seg = x[lo:hi][valid[lo:hi]]
        if seg.size == 0:
            seg = x[lo:hi]
        m = np.median(seg)
        mad = np.median(np.abs(seg - m))
        med[k] = m
        scale[k] = max(1.4826 * mad, min_scale)
    med_full = np.interp(np.arange(n), centers, med)
    scale_full = np.interp(np.arange(n), centers, scale)
    return (x - med_full) / scale_full


def shift_left(x: np.ndarray, offset: int) -> np.ndarray:
    """y[m] = x[m + offset]  (Chat-Peak -> Moment-Zeitachse)."""
    if offset <= 0:
        return x.copy()
    y = np.zeros_like(x)
    y[: len(x) - offset] = x[offset:]
    return y


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kandidaten aus Chat-, Audio- und Clip-Signalen berechnen -> work/<vod>/candidates.json",
        epilog="Exit: 0 ok, 2 signals.json fehlt oder ist ungültig.",
    )
    parser.add_argument("--vod-id", required=True)
    parser.add_argument("--top", type=int, help="Anzahl Kandidaten (Standard: top_candidates aus der Config)")
    parser.add_argument("--dry-run", action="store_true", help="berechnen, aber candidates.json nicht schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    log = _lib.setup("detect_highlights", args.verbose)

    work = _lib.work_dir(args.vod_id)
    if not (work / "signals.json").is_file():
        _lib.fail(f"{work / 'signals.json'} fehlt. Erst fetch_signals.py laufen lassen.", _lib.EXIT_INVALID)
    signals = _lib.read_json(work / "signals.json")
    meta = _lib.read_json(work / "meta.json") if (work / "meta.json").is_file() else {}
    cfg, warnings = _lib.load_config()
    st = _lib.streamer_settings(cfg, meta.get("streamer"))
    top = args.top or int(st["top_candidates"])
    offset = int(round(st["reaction_offset_s"]))
    baseline_s = int(st["baseline_s"])

    audio_db = np.array(signals["audio"]["db"], dtype=float)
    n = len(audio_db)
    if n == 0:
        _lib.fail("Audio-Kurve ist leer", _lib.EXIT_INVALID)
    muted = np.zeros(n, dtype=bool)
    for a, b in signals["audio"].get("muted", []):
        muted[max(0, a - TOLERANCE_S): min(n, b + TOLERANCE_S)] = True

    # ---- Audio
    floor = np.clip(audio_db, -90, 0)
    smooth = np.convolve(floor, np.ones(AUDIO_SMOOTH_S) / AUDIO_SMOOTH_S, mode="same")
    z_audio = robust_z(smooth, baseline_s, min_scale=1.5, exclude=muted)
    z_audio[muted] = -10
    z_audio_m = rolling_max(z_audio, TOLERANCE_S, TOLERANCE_S)

    # ---- Chat
    chat = signals.get("chat") or {}
    chat_ok = bool(chat.get("available"))
    per_hour = float(chat.get("per_hour") or 0)
    if chat_ok:
        def arr(key: str) -> np.ndarray:
            a = np.zeros(n)
            v = np.array(chat.get(key) or [], dtype=float)[:n]
            a[: len(v)] = v
            return a
        window = int(chat.get("window_s") or st["window_s"])
        uniq = arr("uniq_w")
        msgs_w = rolling_sum(arr("msgs"), window)
        short_w = rolling_sum(arr("short"), window)
        dup_w = arr("dup_w")
        emote_w = {cat: rolling_sum(np.pad(np.array(v, dtype=float)[:n], (0, max(0, n - len(v)))), window)
                   for cat, v in (chat.get("emotes") or {}).items()}
        z_chat = robust_z(uniq, baseline_s, min_scale=max(1.0, float(np.sqrt(max(np.median(uniq), 1)))))
        z_chat_m = rolling_max(shift_left(z_chat, offset), TOLERANCE_S, TOLERANCE_S)
        short_ratio = np.divide(short_w, msgs_w, out=np.zeros(n), where=msgs_w > 0)
    else:
        z_chat_m = np.zeros(n)
        warnings.append(f"kein Chat-Signal ({chat.get('reason', 'nicht geladen')}): nur Audio/Clips")

    chat_mode = chat_ok and per_hour >= float(st["min_chat_per_hour"])
    w_chat, w_audio = (1.0, 0.6) if chat_mode else ((0.4, 1.0) if chat_ok else (0.0, 1.0))

    # ---- Zuschauer-Clips
    bonus = np.zeros(n)
    for clip in (signals.get("clips") or {}).get("items", []):
        o = int(clip["offset"])
        bonus[max(0, o): min(n, o + int(clip.get("duration") or 30))] += np.log1p(clip.get("views") or 0)
    if bonus.max() > 0:
        bonus /= bonus.max()

    score = w_chat * np.clip(z_chat_m, 0, None) + w_audio * np.clip(z_audio_m, 0, None) + 3.0 * bonus
    if chat_ok:
        shifted_short = rolling_max(shift_left(short_ratio, offset), TOLERANCE_S, TOLERANCE_S)
        score += 0.5 * shifted_short * (z_chat_m >= st["chat_sigma"])
    score[muted] = 0
    score[: min(5, n)] = 0

    # ---- Peaks mit Mindestabstand
    order = np.argsort(-score, kind="stable")
    chosen: list[int] = []
    for m in order:
        if score[m] < MIN_SCORE or len(chosen) >= top:
            break
        if all(abs(int(m) - c) >= st["min_spacing_s"] for c in chosen):
            chosen.append(int(m))

    candidates: list[dict[str, Any]] = []
    for rank, m in enumerate(chosen, start=1):
        # Score-Maxima sind Plateaus (rollendes Maximum ±TOLERANCE_S): der echte
        # Audio-Peak liegt irgendwo in diesem Fenster, also genau dort suchen.
        lo, hi = max(0, m - TOLERANCE_S), min(n, m + TOLERANCE_S + 1)
        audio_peak = lo + int(np.argmax(z_audio[lo:hi]))
        moment = audio_peak if z_audio[audio_peak] >= AUDIO_PEAK_Z else m
        reasons = []
        sig: dict[str, Any] = {"z_audio": round(float(z_audio_m[m]), 2),
                               "audio_db_max": round(float(audio_db[lo:hi].max()), 1)}
        if z_audio_m[m] >= AUDIO_PEAK_Z:
            reasons.append("laut")
        if chat_ok:
            peak = min(n - 1, m + offset)
            sig.update({"z_chat": round(float(z_chat_m[m]), 2), "chat_peak_s": peak,
                        "uniq_chatters": int(uniq[peak]), "msgs_window": int(msgs_w[peak]),
                        "short_ratio": round(float(short_ratio[peak]), 2),
                        "dup_ratio": round(float(dup_w[peak]), 2),
                        "emotes": {c: int(v[peak]) for c, v in emote_w.items() if v[peak] > 0}})
            if z_chat_m[m] >= st["chat_sigma"]:
                reasons.append("chat-spike")
            labels = [c for c, v in sig["emotes"].items() if v >= 3]
        else:
            labels = []
        if bonus[m] > 0:
            sig["viewer_clip_bonus"] = round(float(bonus[m]), 2)
            reasons.append("zuschauer-clip")
        sec_start = max(0.0, moment - float(st["section_pre_s"]))
        sec_end = min(float(n), moment + max(offset, 0) + float(st["section_post_s"]))
        cand_warn = []
        for a, b, title in meta.get("blocked_ranges") or []:
            if a is not None and b is not None and a - 30 <= moment <= b + 30:
                cand_warn.append(f"Kategorie '{title}': Musik-/Claim-Risiko, max. 60 s oder stumm")
        if muted[int(sec_start): int(sec_end)].any():
            cand_warn.append("stummer Abschnitt im Fenster")
        candidates.append({
            "id": _lib.candidate_id(moment), "rank": rank, "moment_s": int(moment), "score": round(float(score[m]), 2),
            "section": [round(sec_start, 1), round(sec_end, 1)],
            "reasons": reasons, "labels": labels, "signals": sig, "warnings": cand_warn,
        })

    result = {
        "vod_id": args.vod_id,
        "mode": "chat" if chat_mode else ("chat+audio" if chat_ok else "audio"),
        "chat_per_hour": per_hour,
        "params": {"reaction_offset_s": offset, "chat_sigma": st["chat_sigma"],
                   "min_spacing_s": st["min_spacing_s"], "weights": {"chat": w_chat, "audio": w_audio}},
        "candidates": candidates,
    }
    if not args.dry_run:
        _lib.write_json(work / "candidates.json", result)
    for w in warnings:
        log.warning(w)
    _lib.emit({
        "vod_id": args.vod_id, "mode": result["mode"], "count": len(candidates), "dry_run": args.dry_run,
        "top": [{k: c[k] for k in ("id", "rank", "moment_s", "score", "reasons", "labels")} for c in candidates[:10]],
        "warnings": warnings,
        "file": None if args.dry_run else str(work / "candidates.json"),
    })


if __name__ == "__main__":
    main()
