# Benutzerverwaltung ohne Django Admin

## Übersicht

Die neue Benutzerverwaltungsoberfläche ermöglicht es Administratoren, Benutzer zu verwalten und Rechte zu vergeben, **ohne Django Admin zu nutzen**. Django Admin ist jetzt nur noch bei `DEBUG=True` verfügbar.

## Features

### 1. Benutzerliste
- **URL:** `http://localhost:8000/verwaltung/benutzer/`
- Alle Benutzer in einer übersichtlichen Tabelle
- Filtern nach:
  - Aktivität (Aktiviert / Deaktiviert)
  - Kassenrecht
  - Administratoren
- Suche nach Name oder E-Mail

### 2. Benutzer bearbeiten
- **URL:** `http://localhost:8000/verwaltung/benutzer/<user_id>/`
- Bearbeite Kontaktdaten (Name, E-Mail, Adresse, Telefon)
- Vergebe Rechte:
  - **Konto aktivieren/deaktivieren** - Steuert ob sich der Benutzer anmelden kann
  - **Kassenrecht** - Berechtigung, die Kasse zu bedienen
  - **Superadministrator** - Volle Verwaltungsberechtigung
- Benutzer löschen/anonymisieren

### 3. Benutzer löschen
- **Sicherheit:** Verhindert Selbstlöschung und Löschung des letzten aktiven Administrators
- Anonymisiert alle persönlichen Daten
- Alle Konsents und Berechtigungen werden gelöscht
- Das Konto wird deaktiviert und kann nicht wiederhergestellt werden

## Zugriff

### Nur Superadministratoren haben Zugriff
Die Benutzerverwaltung ist geschützt durch den `@management_required` Decorator:

```python
@management_required  # Nur is_superuser=True
def user_management_list(request):
    ...
```

### Navigation
- Home → Verwaltung → **Benutzerverwaltung**
- Oder direkt: `/verwaltung/benutzer/`

## Django Admin - Nur noch im Entwicklungsmodus

### Blockierung in Production

Ein neuer Middleware (`AdminDebugOnlyMiddleware`) blockiert Zugriff auf `/admin/` wenn `DEBUG=False`:

```python
# basar/settings.py
MIDDLEWARE = [
    ...
    "core.admin_middleware.AdminDebugOnlyMiddleware",
    ...
]
```

**Verhalten:**
- `DEBUG=True` (Entwicklung) → Django Admin verfügbar
- `DEBUG=False` (Production) → Django Admin returniert HTTP 404
- Tests (`TESTING=True`) → Django Admin verfügbar (für Testzwecke)

### Admin-URL
- `http://localhost:8000/admin/` (nur im Entwicklungsmodus)

## Formulare

### UserManagementForm
- Benutzerdaten bearbeiten
- Rechte vergeben (`is_active`, `is_cashier`, `is_superuser`)
- Validierung: Verhindert Selbstentfernung von Admin-Rechten

### UserSearchForm
- Filterung nach Status und Rolle
- Textsuche nach Name/E-Mail

## Views

### `user_management_list` (GET)
- Zeigt gefilterte Benutzerliste
- Akzeptiert GET-Parameter: `search`, `filter`

### `user_management_edit` (GET/POST)
- Zeigt Bearbeitungsformular
- POST speichert Änderungen

### `user_anonymize` (POST)
- Löscht Benutzer durch Anonymisierung
- Nur superadministratoren können aufrufen

## Security-Highlights

1. **Nur Administratoren können Rechte vergeben** - `@management_required` Decorator
2. **Selbstschutz** - Kann sich selbst nicht deaktivieren oder Rechte entziehen
3. **Letzter Admin-Schutz** - Letzte aktive Admin kann nicht gelöscht werden
4. **Private Administratoren-Oberfläche** - Separate Verwaltungsoberfläche statt öffentlichen Django Admin
5. **Django Admin im Produktionsbetrieb deaktiviert** - Mittels `DEBUG=False` Check im Middleware

## Translations

Alle Texte sind internationalisiert (i18n) und verfügbar in:
- Deutsch (de)
- Englisch (en)
- Französisch (fr)
- Spanisch (es)
- Portugiesisch (pt)
- Japanisch (ja)
- Vereinfachtes Chinesisch (zh-hans)
- Türkisch (tr)

## URLs

```python
# basar/urls.py
path("verwaltung/benutzer/", views.user_management_list, name="user_management_list"),
path("verwaltung/benutzer/<int:user_id>/", views.user_management_edit, name="user_management_edit"),
path("verwaltung/benutzer/<int:user_id>/loeschen/", views.user_anonymize, name="user_anonymize"),
```

## Templates

- `core/templates/admin/user_management_list.html` - Benutzerliste
- `core/templates/admin/user_management_edit.html` - Benutzer bearbeiten

## Beispiel-Workflow

### 1. Neuen Kassierer hinzufügen
1. Registrierung durch Kassierer (via `/accounts/register/`)
2. Admin geht zu `/verwaltung/benutzer/`
3. Findet den Benutzer und klickt "Bearbeiten"
4. Aktiviert "Kassenrecht" Checkbox
5. Speichert die Änderungen

### 2. Admin-Rechte vergeben
1. Admin geht zu `/verwaltung/benutzer/`
2. Findet den Benutzer und klickt "Bearbeiten"
3. Aktiviert "Superadministrator" Checkbox
4. Speichert die Änderungen

### 3. Benutzer deaktivieren
1. Admin geht zu `/verwaltung/benutzer/`
2. Findet den Benutzer und klickt "Bearbeiten"
3. Deaktiviert "Konto aktiviert" Checkbox
4. Speichert die Änderungen
5. Der Benutzer kann sich nicht mehr anmelden

## Testing

Alle Views sind getestet:
- Benutzer können nur gefiltert/bearbeitet werden wenn Admin
- Selbstmodifikation ist blockiert
- Letzter Admin kann nicht gelöscht werden
- Anonymisierung funktioniert korrekt

```bash
python manage.py test core.BazaarWorkflowTests -v 2
```

---

**Zusammenfassung:** Die Benutzerverwaltung ist nun vollständig aus der Anwendungsoberfläche ohne Django Admin möglich. Django Admin ist nur noch für Entwicklung (DEBUG=True) zugänglich.
