# QR-Scanner Benutzerhandbuch

## Überblick

Das Kassensystem unterstützt die Verwendung eines zweiten Geräts (z.B. Handy oder Tablet) als QR-Code-Scanner. Dies macht die Kassierung schneller und fehlerfreier, da keine manuellen Eingaben über die Tastatur erforderlich sind.

## Ablauf

### 1. Scanner-Session starten (Kassiererin)

1. Öffne die Kasse auf Laptop oder Tablet
2. Klicke den Button **"🔧 QR-Scanner aktivieren"** (oben in der Kasse)
3. Der Kassierer erhält einen eindeutigen Code und einen QR-Code

### 2. Scanner-Handy verbinden

1. Öffne den QR-Code-Link auf dem Handy (z.B. per QR-Scanner des Handys oder manuell über die angezeigte URL)
2. Das Handy öffnet die Scanner-Seite
3. Die Scanner-Seite zeigt die Bereitschaft an

### 3. Artikel scannen

1. Öffne die Verkäuferschilder mit `VKNr={Verkäufer-ID}&price={Betrag}`
   - Beispiel: `VKNr=42&price=2.50`
2. Scanne jeden QR-Code mit dem Handy
3. Das Handy zeigt eine Bestätigung (Toast)
4. Die Artikel erscheinen automatisch in der Kasse
5. Die Kassierung läuft normal weiter (Verkäufernummer wechseln, Betrag anpassen, etc.)

### 4. Scanner beenden

1. Klicke **"Scanner schließen"** auf der Kasse
2. Das Handy-Interface wird gesperrt
3. Die Scanner-Session ist beendet

## Sicherheit

- **Eindeutige Codes:** Jede Scanner-Session erhält einen zufälligen 50-stelligen Code
- **Gültigkeitsdauer:** Scanner-Sessions verfallen nach 30 Minuten automatisch
- **Rate-Limiting:** Max. 100 Submissions pro Minute pro Session
- **Nur für offene Basare:** Scanner funktioniert nur während der Kasse aktiv ist

## Fehlerbehandlung

| Problem | Ursache | Lösung |
|---------|--------|---------|
| "Code nicht gefunden" | Code ist abgelaufen | Neue Scanner-Session in der Kasse starten |
| "Scanner-Session geschlossen" | Scanner wurde beendet | "Scanner aktivieren" erneut klicken |
| "QR-Code-Format ungültig" | QR-Code hat falsches Format | Überprüfe `VKNr=X&price=Y` Format |
| Rate-Limit Fehlermeldung | Zu viele Scans pro Minute | Kurz warten und erneut versuchen |

## QR-Code Format

QR-Codes müssen folgendes Format haben:

```
VKNr={registration_id}&price={betrag}
```

- **registration_id**: Datenbank-ID des Verkäufers (kein String!)
- **betrag**: Dezimalzahl mit bis zu 2 Dezimalstellen (z.B. `2.50`)

Beispiele:
- `VKNr=1&price=1.00`
- `VKNr=42&price=5.99`
- `VKNr=100&price=0.50`

## Tipps & Tricks

- **Mehrere Geräte:** Pro Kasse kann nur ein Scanner aktiv sein
- **Tablet-Kombination:** Kassieren auf Laptop, Scannen auf Tablet funktioniert optimal
- **Handy als Tastatur:** Kombination mit Handy-Scanner ist schneller als Kassieren per Tastatur
- **Fallback:** Wenn Scanner ausfällt, kann die Kassierung normal mit Tastatur weitergehen

## Datenschutz

- Scanner-Sessions sind temporär und benutzer-gebunden
- Nur der angemeldete Kassierer kann Sessions sehen
- Sessions verfallen nach 30 Minuten
- Keine persönlichen Daten werden zwischen Handy und Kasse übertragen
