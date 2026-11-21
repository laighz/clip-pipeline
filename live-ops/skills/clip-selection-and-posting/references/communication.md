# Kommunikation

`<root>` ist das clip-pipeline-Repo, der Ordner mit `clip-forge` und `live-ops`.

- Antworten auf Deutsch (der User schreibt Deutsch), knapp. Das gilt auch für Antworten auf interne Notifications.
- Über WhatsApp nur bei Ergebnissen, nötigen Entscheidungen oder auf `/status` melden, keine Zwischenstände pro Schritt.
- Selbstcheck vor jeder Runden-Meldung: Hat sie mehr als zwei Zeilen oder eine Aufzählung der Kandidaten, ist sie zu lang. Sie darf nur Zahl, besten Score mit Streamer und nächste Runde nennen.
- Leere Bewertungsrunden (nichts ≥ Schwelle) in EINER Zeile melden: Anzahl bewertet, bester Score mit Streamer und Kurzinhalt im Halbsatz, nächste Runde (Muster: „Runde ohne Treffer: 12 bewertet, bester 38 (xQc, Motorrad), nächste in ~30 min."). Keine Liste der übrigen Kandidaten, auch bei neuen Streamern nicht. Keine Kandidatenliste, keine Wiederholung offener Fragen in jeder Runde; Details nur auf Nachfrage oder `/status`. Grund: Dutzende gleichförmige Runden-Meldungen fressen Kontext und Tokens, und der User hat genau das mehrfach bemängelt. Ausnahme: ein neuer Befund (Fehler, fehlende Segmente, Sperre, Serie ohne Treffer zum ersten Mal) bekommt einen Satz.
- `/status`-Antwort in 5 Zeilen: laufende Aufnahmen (Namen; fehlt ein Watchlist-Kanal, vorher `decapi`-Uptime und `<kanal>_sup.log` prüfen und sagen, ob offline oder kaputt), freier Speicher, heute bewertet/bester Score/gepostet, letzter Post mit noch offenen Plattformen, Zeit bis zur nächsten Runde. Fehlende Aufnahmen nicht nur melden, sondern gleich klären.
- Unklarer Kurzbefehl (Tippfehler, mehrdeutig, z. B. „poste X“ ohne erkennbares Objekt): kurz mit 2–3 konkreten Optionen zurückfragen, nichts raten und nichts posten.
