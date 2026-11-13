# Basar-Kassensystem

Responsive Webanwendung zur Verkäuferregistrierung, Basarkasse und Abrechnung. Die Anwendung basiert auf Django und PostgreSQL; SQLite wird nur als lokale Entwicklungsdatenbank unterstützt.

## Lokal starten

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

In der Entwicklungsumgebung werden E-Mails in der Konsole ausgegeben. Registrierte Nutzer müssen den angezeigten Bestätigungslink öffnen, bevor sie sich anmelden können.

## Konfiguration

- `DJANGO_SECRET_KEY`: zufälliger, geheimer Django-Schlüssel
- `DJANGO_DEBUG`: lokale Entwicklung standardmäßig aktiv; in Produktion auf `0` setzen
- `DJANGO_ALLOWED_HOSTS`: kommaseparierte Hostnamen
- `DATABASE_URL`: in Produktion PostgreSQL, z. B. `postgresql://basar:passwort@localhost:5432/basar`
- `DJANGO_EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`: SMTP-Konfiguration für den produktiven Mailversand
- `DEFAULT_FROM_EMAIL`: Absenderadresse
- `DJANGO_MEDIA_ROOT`, `DJANGO_STATIC_ROOT`: Verzeichnisse für dauerhafte Medien und gesammelte statische Dateien

Für den Produktivbetrieb sind ein starker `DJANGO_SECRET_KEY`, PostgreSQL, SMTP sowie ein sicher konfigurierter Webserver und dauerhafter Medienspeicher erforderlich. Es werden ausschließlich relationale SQL-Datenbanken unterstützt.
Der Einwilligungstext muss im Admin mit dem fachlich und rechtlich geprüften Basar-Datenschutzhinweis gepflegt werden; eine Rechtsprüfung ist nicht Bestandteil der Anwendung.

## Erste Einrichtung

1. Über `/verwaltung/` globale Schnellpreise, Einwilligungstext und -version, E-Mail-Bestätigung, Gebührenvorgaben für neue Basare und Plakat-Standardvorgaben konfigurieren.
2. Unter „Basare verwalten“ einen Basar mit Beginn und letzter möglicher Nummernvergabe anlegen. Die Nummernvergabe darf nicht nach dem Basarbeginn enden.
3. Beim Setzen des Basarstatus auf „Aktiv“ wird die Nummernvergabe automatisch geschlossen.
4. Im `/admin/` Nutzerkonten verwalten und Kassenrecht an Kassennutzer vergeben. Basar- und Systemeinstellungen werden ausschließlich in der Anwendungsverwaltung bearbeitet.
5. Nach Ende des Basars den Status auf „Abgeschlossen“ setzen und über die Basarverwaltung die Abrechnung erzeugen.

Übliche Preise werden unter „Systemeinstellungen“ als JSON-Liste gepflegt, zum Beispiel `["1.00", "2.50", "5.00"]`. Sie gelten für alle Basare und Kassen. Dort lassen sich außerdem Einwilligungsversion und -text sowie die Pflicht zur E-Mail-Bestätigung für neu angelegte Konten einstellen. Bei einer geänderten Einwilligungsversion werden angemeldete Benutzer vor dem Fortfahren zur erneuten Zustimmung aufgefordert. Ein Kassenbeleg gehört zu einem Basar und kann Artikel verschiedener Verkäufernummern enthalten. Der prozentuale Betreiberanteil wird auf den Bruttoumsatz angewendet. Abrechnungen sind nach ihrer Erstellung als unveränderlicher Snapshot gespeichert.

Beim Speichern eines Basars wird ein Plakat-Prompt aus den globalen Standardvorgaben und den Basardaten erzeugt. Er kann in der Basarverwaltung kopiert werden.

In den Systemeinstellungen kann außerdem die Menüposition (oben, links oder rechts) und das Fixieren des Menüs beim Scrollen konfiguriert werden. Auf schmalen Bildschirmen wird ein Seitenmenü automatisch oben angezeigt; die Navigation ist dort zunächst im zugänglichen Burgermenü eingeklappt.

Die globale Währung ist für Kasse, Verkäuferumsätze und Abrechnungen auswählbar. Jede Abrechnung speichert die verwendete Währung im Snapshot.

## Prüfungen

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

