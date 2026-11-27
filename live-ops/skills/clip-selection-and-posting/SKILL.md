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
Ergänzt [clip-pipeline](../../../SKILL.md) für Live-Aufnahme, Auswahl und Veröffentlichung.

## Prerequisites
Der Skill clipping-streams ist geladen. Live-Skripte liegen in `scripts/` dieses Ordners und unter `<root>/live-ops/live`. ELEVENLABS_API_KEY kommt aus der Umgebung und wird nie im Chat abgefragt.

## Instructions
Die passende Referenz zum Auftrag lesen, nicht alle auf einmal:

- [Aufnahme und Modus](references/mode.md)
- [Auswahl und Bewertung](references/selection.md)
- [Einzelposts, mehrere Ziele und Beitragsprüfung](references/posting.md)
- [Aufräumen und Janitor](references/cleanup.md)
- [Kommunikation](references/communication.md)
- [Betriebsfallen](references/pitfalls.md) und [Versionieren](references/versioning.md) bei Bedarf

Bei einer Übergabe zuerst den [Projektstand](../../../STATUS.md) lesen. Veröffentlichungsregeln und Upload-Befehle stehen nur in der Posting-Referenz.

## Available Scripts
| Script | Purpose | Arguments |
| --- | --- | --- |
| live_spike_loop.py | Spikes still sammeln | rec_id, optional --exit-on-ready, --follow-restarts |
| live_status.py | Zwei Zeilen Status | rec_id |
| evaluate.py | Score und Post-Entscheid | rec, clip, --cand |


## Limitations
Kein bloßes Kürzen einer einzelnen Datei. Keine Passwörter erfragen oder eintippen.
