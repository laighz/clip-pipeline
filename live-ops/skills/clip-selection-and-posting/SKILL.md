---
name: clip-selection-and-posting
description: >-
  Use when ranking or posting live stream clips. Not for a raw trim.
metadata:
  author: Moritz
  tags: [twitch, clips, live, posting]
allowed-tools: Bash(uv run *) PowerShell(uv run *) Read Write WebFetch env
---

# Clip-Auswahl und Posten

## Purpose
Ergänzt [clip-pipeline](../../../SKILL.md). Hier stehen die Auswahlregeln und die Betriebsfallen. `<root>` ist das clip-pipeline-Repo, der Ordner mit `clip-forge` und `live-ops`.

Vor dem ersten Schnitt die verlinkten Dateien lesen. Die Kurzfassung ersetzt sie nicht.

## Prerequisites
Der Skill clipping-streams ist geladen. Live-Skripte liegen in `scripts/` dieses Ordners und unter `<root>/live-ops/live`. ELEVENLABS_API_KEY kommt aus der Umgebung und wird nie im Chat abgefragt.

## Instructions
Jedes Skript über run_script starten: `uv run --quiet "<skill>/scripts/<name>.py"` oder `python3 "<skill>/scripts/<name>.py"`. `<skill>` ist der Ordner dieser Datei.

Zuerst den Modus klären, dann auswählen, dann posten. Ein validate- oder evaluate-Fehler ist ein error: nicht posten, bis die Prüfung `decision: post` sagt. YouTube-`title` nennt den Community-Namen, siehe [Posten](references/posting.md).

## Available Scripts
| Script | Purpose | Arguments |
| --- | --- | --- |
| live_spike_loop.py | Spikes still sammeln | rec_id, optional --exit-on-ready, --follow-restarts |
| live_status.py | Zwei Zeilen Status | rec_id |
| evaluate.py | Score und Post-Entscheid | rec, clip, --cand |
| post_all.py | Einen Clip über CDP posten | vod, clip |
| cdp_helper.py | Browser-Verbindung und Captions | von post_all genutzt |

## Examples
Standard: erst am Stream-Ende bewerten, nur die Top 2 posten. Live-Modus nur, wenn der User aktiv schneiden will: `python3 "<skill>/scripts/live_spike_loop.py" <rec_id> --exit-on-ready --follow-restarts`.

## Limitations
Kein bloßes Kürzen einer einzelnen Datei. Keine Passwörter. Keine parallelen Post-Läufe auf derselben Browser-Instanz.

## Troubleshooting
Fehlt ein Segment oder scheitert ein Post, den error nennen und nicht raten. Betriebsfallen, Speicher und die Post-Schwelle stehen in den Referenzen. Ein fail von evaluate.py heißt: nicht posten.

Vor dem Handeln lesen:

- [Modus](references/mode.md)
- [Auswahl](references/selection.md)
- [Kommunikation](references/communication.md)
- [Posten](references/posting.md)
- [Versionieren](references/versioning.md)
- [Aufräumen](references/cleanup.md)
- [Betriebs-Fallen](references/pitfalls.md)
- [Anti-Shadowban](references/anti-shadowban.md)
