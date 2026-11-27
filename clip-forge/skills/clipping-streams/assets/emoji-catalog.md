# Emoji-Katalog

`emoji-catalog.json.gz` ist ein Offline-Datensatz mit 3.963 vollständig
qualifizierten Emojis und 9 Komponenten aus Unicode Emoji 18.0. Jede Zeile
enthält die Emoji-Sequenz, den englischen Emojipedia-Namen, den deutschen Namen,
Stichwortkandidaten in beiden Sprachen, Einsatzideen und Quellen-URLs. Die
Kandidaten und Ideen sind keine aktiven Triggerlisten.

## Benutzung

```sh
python3 scripts/emoji_catalog.py 'Fliegende Untertasse' --limit 2
python3 scripts/emoji_catalog.py '👍🏽' --limit 1
```

Auf macOS werden für neue Renderings die bisherigen Einzelwortregeln, handverlesene
neue Begriffe und exakte, eindeutige Mehrwort-Namen konkreter Motive geprüft. Ein
Treffer erscheint erst beim letzten gesprochenen Wort, und er darf Pausen,
Satzzeichen oder Schnitte nicht überbrücken. Wortfolgen werden vor Einzelwörtern
geprüft. Caption-Text, Titelkarte, Pop-out-Größe, Positionierung und Wiederholungsabstand
bleiben gleich. Kann die lokale Apple-Schrift ein Symbol nicht darstellen, wird
nur dieses Emoji ausgelassen; die Untertitel bleiben stehen.

Alle Katalogeinträge bleiben suchbar, aber Varianten, Flaggen, generische
CLDR-Stichwörter und technische Komponenten lösen aus dem Katalog keine
automatischen Pop-outs aus. Die geschriebene Caption wird nicht durch ein Emoji
ersetzt. `render.caption_emoji_catalog: false` beschränkt die Zuordnung auf die
kuratierte Liste; `render.caption_emojis: false` schaltet alle Caption-Emojis aus.

## Datenherkunft

- Abgleich: [Unicode Emoji Keyboard/Display Test Data](https://www.unicode.org/Public/emoji/latest/emoji-test.txt), beim Abruf am 27. November 2026 Version 18.0.
- Englische Namen: [Emojipedia](https://emojipedia.org/emoji-versions). Pro Datensatz sind Detailseite und Index mitgeführt. Keine Bilder oder Beschreibungstexte übernommen.
- Deutsche Namen und Suchbegriffe: [Unicode CLDR](https://github.com/unicode-org/cldr/tree/main/common), `annotations` und `annotationsDerived`, Deutsch und Englisch. Siehe [Lizenzhinweis](emoji-catalog-UNICODE-LICENSE.txt).
- Die Szenenideen sind eigene Vorschläge, überwiegend aus Unterkategorie-Vorlagen. Hauttonvarianten übernehmen die Vorschläge ihrer Grundform. Die 9 Komponenten dienen nur dem Nachschlagen.
