# Anti-Shadowban: Clips fremder Streams posten, ohne dass Konten gedrosselt oder gesperrt werden

Keine Garantie (Plattformen legen Kriterien nicht offen). Das groesste Risiko ist nicht die Menge,
sondern fast unbearbeiteter Fremd-Content: YouTube "reused content", TikTok "unoriginal content",
Instagram Originalitaets-Pruefung. Viele Reposts in kurzer Zeit auf einem jungen Konto -> Sperre.

## Umgesetzt (Standard)
- Takt: 1 Clip pro Stunde ueber alle Streams, +-0-5 min Zufall (`evaluate.py`, gap 60 min).
- Plattform-Versatz und Zielauswahl folgen dem [Veröffentlichungsweg](posting.md).
- Caption- und Hashtag-Regeln stehen unter [Posten](posting.md).
- Nur Clips mit klarer Pointe; reine Cutscenes/Gameplay ohne Reaktion nie (auch Publisher-Copyright).
- Gesperrte Plattform: Flag-Datei `<KEY>_BLOCKED`, alle Laeufe ueberspringen sie, bis der User Freigabe meldet.
- Instagram nach einer Sperre: max. 3 Reels / 24 h, mind. 4 h Abstand (Zaehler `live/ig_posts.log`).
- Neues Konto: `warmup_since` (Unix-Zeit) setzen. 14 Tage Instagram 1/24 h und TikTok 2/24 h. `warmup.sh` schreibt den Stand um 13:00 Europe/Berlin nach `warmup.json`. Kein Feed, kein Like.
- Streamer, der live gegen Re-Uploads/Restreams wettert oder seine Community zum Melden aufruft:
  hoehere Schwelle fuer seine Clips (z. B. 85 statt 80, `THRESH` in `evaluate.py`) oder pausieren,
  und den User einmal darauf hinweisen. Seine Community meldet sonst gezielt den Kanal.

## Offene Hebel (nur nach Entscheidung des Users)
- Eigenleistung: Voice-over/Intro 3-5 s (Kommentar-Skript aus clips.json einsprechen oder TTS) + 1 s Outro-Karte mit Kanalname. Wirkt am staerksten gegen "reused/unoriginal". Vorher fragen: eigene Stimme oder TTS.
- Erlaubnis der Streamer/Management (Clipper-Programme). Mit Erlaubnis lassen sich Strikes anfechten.
- Profilbild, Bio und die ersten eigenen Posts eines neuen Kontos macht der User, bevor der Takt hochgeht.
- Snapchat Spotlight prueft streng: max. 1-2/Tag. X ist am robustesten (bis ~5/Tag).
- Nachtruhe (keine Posts nachts), immer dieselbe IP/dasselbe Profil pro Konto, kein VPN-Wechsel.
- Interaktion (Kommentare beantworten, folgen, Liken) macht der User selbst.

## Frueherkennung
- Taeglich Views der neuen Posts gegen den Schnitt pruefen. Faellt ein Post >80 % ab oder fehlt in der Hashtag-Suche: 48 h Pause auf dieser Plattform.
- Status-Seiten (YouTube Studio Hinweise, TikTok/Instagram Konto-Status, "eingeschraenkte Reichweite") beim Posten mitlesen und sofort melden.

## Diagnose "Bin ich auf YouTube shadow-banned?"
Reihenfolge (per CDP im Clip-Profil):
1. Studio -> Content -> Tab *Shorts* (`.../videos/short`): pro `ytcp-video-row` Sichtbarkeit, Hinweise-Spalte
   ("Copyright", "Restrictions", "Age-restricted", "Ineligible", "Limited") und Views lesen. Steht nirgends eine
   Einschraenkung, gibt es keinen formalen Strike/Claim.
2. Analytics-Uebersicht (`.../analytics/tab-overview/period-default`): "48 hour views" und Top-Videos.
   Laufen aeltere Shorts weiter (hunderte+ Views), ist der Kanal nicht pauschal gesperrt.
3. Neue Shorts mit 0-1 Views nach 1-3 h, waehrend aeltere nach gleicher Zeit schon Views hatten = Drosselung
   moeglich, aber nicht belegt. Typische Ursachen: viele Uploads in kurzer Zeit auf kleinem Kanal, extremer
   Wettbewerb (Release-Tag eines Games), "reused content". Erst nach ~24 h urteilen: Traffic-Quelle "Shorts-Feed"
   des betroffenen Shorts pruefen. Dem User Befund + wahrscheinliche Ursachen + Gegenmittel (Takt, Voice-over)
   nennen, nicht "Shadowban" behaupten.
