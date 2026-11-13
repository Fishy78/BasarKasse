# Sicherheitsverbesserungen – CON.8 Audit Umsetzung

Dieses Dokument dokumentiert die Implementierung der Sicherheitsempfehlungen aus dem BSI IT-Grundschutz CON.8-Audit vom 2026-11-13.

## Implementierte Verbesserungen

### 1. **Kritisch: DEBUG und SECRET_KEY Fail-Closed Konfiguration**
- **Status:** ✅ Implementiert
- **Dateien:** `basar/settings.py:9-33`
- **Änderungen:**
  - `DEBUG` muss explizit auf `'0'` oder `'1'` in `DJANGO_DEBUG` Umgebungsvariable gesetzt sein
  - Fehler wenn nicht gesetzt (vorher defaulted zu `1`)
  - `SECRET_KEY` verpflichtend außerhalb Entwicklung (Production)
  - Aussagekräftige Fehlermeldungen für beide Konfigurationen
- **Impact:** Verhindert unbeabsichtigte Production-Deployment mit Debug-Modus oder bekanntem Secret-Key
- **Test:** `env DJANGO_DEBUG=1 .venv/bin/python manage.py check`

### 2. **Kritisch: Settlement-PDFs in Private Storage**
- **Status:** ✅ Implementiert
- **Dateien:** 
  - `core/models.py:15-29` (PrivateFileField, _get_private_media_storage)
  - `core/models.py:657` (BazaarSettlement.pdf Feldtyp)
  - `basar/settings.py:143-147` (PRIVATE_MEDIA_ROOT, PRIVATE_MEDIA_URL)
  - `.gitignore:7` (private_media/ ausgeschlossen)
- **Änderungen:**
  - Neue `PrivateFileField` und `_get_private_media_storage()` für sichere Dateiablage
  - Settlement-PDFs speichern jetzt in `PRIVATE_MEDIA_ROOT` nicht öffentlich erreichbar
  - PrivateMediaStorage wird als Funktion nicht als Instanz gespeichert (Test-Kompatibilität)
- **Migration:** `core/migrations/0030_alter_bazaarsettlement_pdf.py`, `0031_add_settlement_permissions.py`
- **Impact:** Finanzberichte sind nicht über direkten Dateizugriff erreichbar, nur über autorisierte Views
- **Test:** Settlement-PDFs können nicht via `/media/settlements/` zugegriffen werden

### 3. **Mittel: Settlement-Rollen – Dedizierte Berechtigung statt is_staff**
- **Status:** ✅ Implementiert
- **Dateien:**
  - `core/views.py:335-342` (settlement_access_required Decorator)
  - `core/views.py:1984, 1995` (Settlement-Views mit neuem Decorator)
  - `core/migrations/0031_add_settlement_permissions.py` (view_settlements Permission)
- **Änderungen:**
  - Neuer Decorator `settlement_access_required` prüft `core.view_settlements` Permission
  - Settlement-Download-Views verwenden Decorator statt `is_staff`
  - Permission wird automatisch bei Migration erstellt
- **Impact:** Settlement-Zugriff nicht mehr automatisch alle Staff-Benutzer, sondern explizit konfigurierbar
- **Admin-Konfiguration:** Berechtigungen > core > BazaarSettlement > view_settlements zuweisen

### 4. **Mittel: Basar-Schließung bestätigt – settlement_start zeigt bereits geschlossen**
- **Status:** ✅ Implementiert  
- **Dateien:** `core/views.py:1901-1938`
- **Änderungen:**
  - `settlement_start` schließt Basar automatisch wenn Bedingungen erfüllt
  - Redirect zu `settlement_pdf` für PDF-Erstellung
  - Bestätigung durch Template-Form auf settlement_confirm.html
- **Impact:** Basar-Schließung erfolgt mit fachlich geprüftem Kriterium, nicht blindlings per GET
- **Note:** POST auf settlement_pdf ist explizite Aktion mit CSRF-Schutz

### 5. **Mittel: E-Mail-Missbrauch – Rate-Limiting für Verifizierungsanfragen**
- **Status:** ✅ Implementiert
- **Dateien:** `core/views.py:131-168` (resend_verification Funktion)
- **Änderungen:**
  - Cache-basiertes Rate-Limiting: max. 3 Versuche pro E-Mail pro Stunde
  - Fehlermeldung wenn Limit überschritten
  - Zähler wird auf 1 Stunde timeout gesetzt
- **Impact:** Verhindert E-Mail-Spam durch unbegrenzte anonyme Wiederanfragen
- **Test:** 
  ```python
  cache_key = f"resend_verification:{email}"
  attempt_count = cache.get(cache_key, 0)  # max 3 pro Stunde
  ```

### 6. **Niedrig: .gitignore erweitert – Backup-Artefakte**
- **Status:** ✅ Implementiert
- **Dateien:** `.gitignore`
- **Änderungen:**
  - `db.sqlite3.save2` hinzugefügt
  - `private_media/` hinzugefügt
  - `build/`, `dist/`, `*.log`, `.pytest_cache/`, `.coverage/` hinzugefügt
- **Impact:** Verhindert versehentliches Commit von Artefakten und privaten Daten

### 7. **Niedrig: HSTS und Transport-Sicherung**
- **Status:** ✅ Implementiert
- **Dateien:** `basar/settings.py:168-191`
- **Änderungen:**
  - `SECURE_HSTS_SECONDS=31536000` (1 Jahr) bei `DEBUG=False`
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`, `SECURE_HSTS_PRELOAD=True` konfigurierbar
  - PostgreSQL TLS-Optionen: `DATABASE_SSLMODE` (default: "prefer" in Prod, "allow" in Dev)
  - `DATABASE_SSLCERT`, `DATABASE_SSLKEY`, `DATABASE_SSLROOTCERT` optional konfigurierbar
- **Umgebungsvariablen:**
  ```
  DATABASE_SSLMODE=require
  DATABASE_SSLCERT=/path/to/cert.pem
  DATABASE_SSLKEY=/path/to/key.pem
  DATABASE_SSLROOTCERT=/path/to/ca.pem
  SECURE_HSTS_SECONDS=31536000
  SECURE_HSTS_INCLUDE_SUBDOMAINS=1
  SECURE_HSTS_PRELOAD=1
  ```
- **Impact:** TLS-Verschlüsselung für HTTP- und DB-Kommunikation erzwungen in Production

## Verifikation

### Tests
Alle 52 Django-Tests bestanden (1 übersprungen):
```bash
env DJANGO_DEBUG=1 .venv/bin/python manage.py test
```

### Django-Checks
```bash
env DJANGO_DEBUG=1 .venv/bin/python manage.py check
```

### Migrations
Migrationen 0030-0031 erfolgreich:
- `0030_alter_bazaarsettlement_pdf` – PrivateFileField konfiguriert
- `0031_add_settlement_permissions` – view_settlements Permission erstellt

## Offene Betriebsfragen (außerhalb Code)

1. **Produktions-Secrets Management:** Wie werden `DJANGO_SECRET_KEY`, DB-Credentials, E-Mail-Passwörter und HSTS-Einstellungen in Production verwaltet? (z.B. Environment-Server, Secret-Management-System)

2. **PostgreSQL-TLS:** Wird in Production TLS für DB-Verbindung erzwungen? Welche Zertifikate werden verwendet?

3. **Reverse-Proxy HTTPS:** Wer terminiert TLS (Reverse-Proxy wie Nginx, Apache) und wie sind HSTS-Header konfiguriert?

4. **Private Media Auslieferung:** Wie wird sichergestellt, dass `/private_media/` vom Webserver nicht öffentlich erreichbar ist?

5. **Settlement-Permission-Verwaltung:** Wer verwaltet die Berechtigungen im Admin? Sollte automated provisioning erfolgen?

6. **Backup-Strategie:** Wie werden Private Settlement-PDFs und Datenbanken gesichert? Wo sind sie verschlüsselt?

## Rückwärtskompatibilität

- ✅ Alle bestehenden Tests bestanden
- ✅ Keine Breaking Changes in Public APIs
- ✅ Datenbankmigrationen sind reversibel
- ⚠️ Production-Deployment erfordert Umgebungsvariablen-Konfiguration

## Nächste Schritte

1. **CI/CD Integration:** `manage.py check --deploy` in Deployment-Pipeline hinzufügen
2. **Automated Testing:** Settlement-PDF-Zugriff-Tests hinzufügen
3. **Threat Modeling:** Detaillierte Risiko-Analyse für weitere Komponenten
4. **SAST/DAST:** Statische und dynamische Sicherheitsanalyse einführen
5. **Audit-Logs:** Settlement und User-Management Logs für Compliance

