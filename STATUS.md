# Clip-Pipeline: Übergabe

Hier stehen nur offene Entscheidungen, Blocker und nächste Schritte. Erledigte Punkte streichen; Betriebsregeln nicht hierher kopieren.

## Offen

- **TikTok- und Snapchat-Änderungen:** Die lokalen Posting-Fixes sind noch nicht eingecheckt. Vor einem separat beauftragten Commit den Code-Diff vom Doku-Diff trennen und die Posting-Tests mit Playwright laufen lassen. Kein erneuter Upload als Test.

## Quellen für den aktuellen Stand

- **Regeln und Arbeitsablauf:** [SKILL.md](SKILL.md) verweist auf die zuständige Referenz.
- **Veröffentlichungen:** `~/.hermes/cache/scratch/live/publish_attempts.jsonl` für einzelne Plattformen, `~/.hermes/cache/scratch/live/posted_clips.jsonl` für vollständig bestätigte Läufe. Bei Unklarheit im Studio oder Profil nachsehen; keine zweite Beitragsliste führen.
- **Laufende Aufnahmen und Jobs:** live prüfen, nicht aus dieser Datei ableiten. Umfang der Abfrage: [Kommunikation](live-ops/skills/clip-selection-and-posting/references/communication.md).
- **Verlauf:** Git für Änderungen, `session_search` bei Bedarf für frühere Absprachen. Hermes-Memory bleibt auf dauerhafte, projektübergreifende Fakten und Präferenzen beschränkt.
