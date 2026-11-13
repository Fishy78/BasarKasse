# QR-Code-Verkäuferschilder erstellen

## Schnelle Anleitung

### Online QR-Code Generator verwenden

1. Öffne eine QR-Code-Generatorseite (z.B. qrcode.com, qr-code-generator.com)
2. Gebe folgende Daten ein:

```
VKNr={VERKÄUFER_ID}&price={PREIS}
```

### Beispiele

Für Verkäufer mit ID `42` und verschiedene Preise:

- **€1,00**: `VKNr=42&price=1.00`
- **€2,50**: `VKNr=42&price=2.50`
- **€5,99**: `VKNr=42&price=5.99`
- **€0,50**: `VKNr=42&price=0.50`

### Schritt-für-Schritt mit qrcode.com

1. Besuche https://www.qrcode.com
2. Klicke auf "Codes erstellen"
3. Wähle "URL" oder "Text"
4. Gebe ein: `VKNr=42&price=2.50`
5. Klicke "Kostenlos herunterladen"
6. Speichere das PNG/PDF
7. Drucke die Codes auf deine Verkäuferschilder

### Wichtig

- **Verkäufer-ID**: Du findest deine ID in der Verkäuferübersicht
- **Dezimalformat**: Verwende Punkte, nicht Kommas (z.B. `2.50`, nicht `2,50`)
- **Testdruck**: Teste einen QR-Code vor dem großen Druck!

### Alternative: Von Hand generieren

Wenn du viele QR-Codes brauchst, kannst du ein Python-Skript verwenden:

```python
import qrcode

vendor_id = 42
prices = [1.00, 2.50, 5.99, 0.50]

for price in prices:
    data = f"VKNr={vendor_id}&price={price}"
    qr = qrcode.QRCode()
    qr.add_data(data)
    qr.make()
    img = qr.make_image()
    img.save(f"vendor_{vendor_id}_price_{price}.png")
```

### Support

Wenn du Probleme hast:
- Überprüfe deine Verkäufer-ID (im Verkäuferportal)
- Stelle sicher, dass der Preis im Format `X.XX` ist
- Teste den QR-Code mit einem QR-Scanner
