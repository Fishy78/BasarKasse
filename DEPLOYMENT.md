# Deployment-Anleitung für Basar-Kassensystem

## Lokale Entwicklung

```bash
export DJANGO_DEBUG=1
python manage.py runserver 127.0.0.1:8000
```

## Production-Deployment auf Remote-Server (z.B. 10.10.9.80)

### 1. Umgebungsvariablen setzen

**Erforderlich (blockierend ohne diese):**
```bash
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="<mindestens-50-zeichen-random-schlüssel>"
export DATABASE_URL="postgresql://benutzer:passwort@postgres-host:5432/basar"
```

**Optional (empfohlen für Production):**
```bash
export DJANGO_ALLOWED_HOSTS="10.10.9.80,basar.example.com"  # Explizit konfigurieren statt Wildcard
export DATABASE_SSLMODE=require  # Wenn PostgreSQL TLS erfordert
export SECURE_HSTS_SECONDS=31536000  # HTTPS-only
export SECURE_SSL_REDIRECT=true  # HTTP → HTTPS Redirect
```

### 2. Datenbank-Setup

```bash
python manage.py migrate
```

### 3. Statische Dateien

**Option A: Django selbst (nur Entwicklung/Debug)**
```bash
python manage.py runserver 0.0.0.0:8000
```

**Option B: Gunicorn + Nginx (Production)**

Gunicorn starten:
```bash
gunicorn -b 127.0.0.1:8000 basar.wsgi:application
```

Nginx Konfiguration (`/etc/nginx/sites-available/basar`):
```nginx
server {
    listen 80;
    server_name 10.10.9.80;

    # Redirect zu HTTPS (Optional, wenn SECURE_SSL_REDIRECT=true)
    # return 301 https://$server_name$request_uri;

    # Statische Dateien
    location /static/ {
        alias /home/user/VibeCode/Basar/staticfiles/;
    }

    # Media-Dateien (öffentlich)
    location /media/ {
        alias /home/user/VibeCode/Basar/media/;
    }

    # Private Abrechnungs-PDFs: NUR über Django streamen (nicht direkt servieren!)
    # location /private-media/ {
    #     deny all;  # Webserver Zugriff verbieten
    # }

    # Django Application
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Statische Dateien sammeln:
```bash
python manage.py collectstatic --noinput
```

### 4. Health Checks

Vor Production-Start ausführen:
```bash
python manage.py check --deploy
```

Test der Applikation:
```bash
curl -H "Host: 10.10.9.80" http://localhost:8000/
```

## Häufige Fehler

### HTTP 400 Bad Request

**Ursache:** `DJANGO_ALLOWED_HOSTS` nicht konfiguriert
- **Lösung:** `DJANGO_ALLOWED_HOSTS="10.10.9.80"` setzen ODER
- Ohne explizite Konfiguration: Production (`DEBUG=0`) akzeptiert alle Hosts (Wildcard `["*"]`)
- In Entwicklung (`DEBUG=1`) nur `localhost`, `127.0.0.1`, `*.local`

### ImproperlyConfigured: DJANGO_DEBUG muss auf '0' oder '1' gesetzt sein

**Ursache:** Umgebungsvariable nicht oder falsch gesetzt
- **Lösung:** `export DJANGO_DEBUG=0` oder `export DJANGO_DEBUG=1`

### ImproperlyConfigured: DJANGO_SECRET_KEY muss für Production gesetzt sein

**Ursache:** In Production (`DJANGO_DEBUG=0`) ohne SECRET_KEY
- **Lösung:** `export DJANGO_SECRET_KEY="<mindestens-50-zeichen-random-wert>"`

Secure Secret generieren:
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### ImproperlyConfigured: DATABASE_URL must be configured outside development

**Ursache:** Production ohne Datenbank-Konfiguration
- **Lösung:** `export DATABASE_URL="postgresql://user:pass@host:5432/basar"`

### Media/Logo-Dateien nicht erreichbar

**In Entwicklung (DEBUG=1):**
- Django serviert Dateien automatisch aus `MEDIA_ROOT`

**In Production (DEBUG=0):**
- Webserver (Nginx/Apache) muss `MEDIA_URL` (`/media/`) → `MEDIA_ROOT` mappen
- Siehe Nginx-Konfiguration oben unter `location /media/`

### Settlement-PDFs sind öffentlich erreichbar

**Problem:** Private Abrechnungsdateien sollten nicht direkt via Webserver erreichbar sein
- **Lösung:** 
  1. `PRIVATE_MEDIA_ROOT` außerhalb von `MEDIA_ROOT` legen
  2. Nginx `location /private-media/ { deny all; }` konfigurieren
  3. PDFs nur über autorisierte Django-Views (`settlement_download`) streamen

## Docker-Deployment (Optional)

```dockerfile
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Production Umgebungsvariablen
ENV DJANGO_DEBUG=0
ENV DJANGO_ALLOWED_HOSTS="*"

EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "basar.wsgi:application"]
```

Build und Run:
```bash
docker build -t basar .
docker run -e DJANGO_SECRET_KEY="..." -e DATABASE_URL="..." -p 8000:8000 basar
```

## Sicherheitschecks

```bash
# Django Deployment Checks
python manage.py check --deploy

# Secret Key Validation
python -c "from django.conf import settings; print('SECRET_KEY length:', len(settings.SECRET_KEY))"

# Allowed Hosts Check
python -c "from django.conf import settings; print('ALLOWED_HOSTS:', settings.ALLOWED_HOSTS)"

# Database Connection Test
python manage.py shell -c "from django.db import connection; connection.ensure_connection()"
```

## Monitoring & Logging

Gunicorn Logs:
```bash
gunicorn --access-logfile - --error-logfile - basar.wsgi:application
```

Django Logs (mit Syslog):
```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'syslog': {
            'level': 'INFO',
            'class': 'logging.handlers.SysLogHandler',
            'address': '/dev/log',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['syslog'],
            'level': 'INFO',
        },
    },
}
```

---

Bei Fragen zum Deployment: siehe `SECURITY_IMPROVEMENTS.md` für weitere Sicherheitshinweise.
