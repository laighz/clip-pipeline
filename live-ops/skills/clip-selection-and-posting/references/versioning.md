# Versionieren

`<root>` ist das clip-pipeline-Repo, der Ordner mit `clip-forge` und `live-ops`.

- Quellcode für Live-Betrieb und Posting liegt in `live-ops/`. Scratch enthält Laufzeitdaten, Logs, lokale Diagnosen und ggf. alte Skriptkopien; nicht zurück ins Repo kopieren. Keine Zugangsdaten, Browserdaten, Traces oder Medien committen.
- Der repo-eigene Skill ist diese [SKILL.md](../SKILL.md). Bei mehrdeutigem Kurznamen `video/clip-pipeline/live-ops/skills/clip-selection-and-posting` verwenden; keinen zweiten Snapshot als Quelle pflegen.
- Nur auf Auftrag committen oder pushen. Vorher die Änderungen auf den Auftrag begrenzen, Diff und passende Tests prüfen. Nach einem Push den Ziel-Commit auf dem Remote verifizieren.
