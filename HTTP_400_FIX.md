# HTTP 400 Bad Request - Lösungsanleitung

## Symptom
```
curl http://10.10.9.80:8000/
→ HTTP/1.1 400 Bad Request
```

## Ursache
Django (`ALLOWED_HOSTS`-Sicherheitsprüfung) lehnt den Request ab, weil der Host nicht in der Whitelist ist.

## Lösung

### ✅ Option 1: Schnelltest (lokal)
Wenn du lokal testest, sollte es funktionieren:
```bash
curl -H "Host: localhost" http://127.0.0.1:8000/
# oder direkt:
curl http://localhost:8000/
```

### ✅ Option 2: Production mit expliziter Host-Konfiguration
Umgebungsvariable auf dem Server setzen:
```bash
export DJANGO_ALLOWED_HOSTS="10.10.9.80,localhost"
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="mindestens-50-zeichen-random-wert"
export DATABASE_URL="postgresql://user:pass@10.10.9.80:5432/basar"

python manage.py runserver 0.0.0.0:8000
```

### ✅ Option 3: Production ohne explizite Hosts (vertraut Reverse-Proxy)
Wenn Nginx/Apache den Host-Header validiert, kann `ALLOWED_HOSTS = ["*"]` sicher sein:
```bash
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="..."
export DATABASE_URL="..."
# Kein DJANGO_ALLOWED_HOSTS nötig → automatisch "["*"]" in Production

python manage.py runserver 0.0.0.0:8000
```

## Verifikation

### 1. Debug-Modus testen
```bash
export DJANGO_DEBUG=1
python manage.py runserver 127.0.0.1:8000
curl http://127.0.0.1:8000/
# → Sollte 200 OK sein
```

### 2. ALLOWED_HOSTS prüfen
```bash
python manage.py shell
>>> from django.conf import settings
>>> print("ALLOWED_HOSTS:", settings.ALLOWED_HOSTS)
>>> print("DEBUG:", settings.DEBUG)
```

### 3. Remote-Host testen
```bash
# Von deinem lokalen Rechner:
curl -H "Host: 10.10.9.80" http://127.0.0.1:8000/
# Oder direkt vom Server:
curl -H "Host: 10.10.9.80" http://localhost:8000/
```

## Deployment-Checklist

- [ ] `DJANGO_DEBUG=0` gesetzt
- [ ] `DJANGO_SECRET_KEY` gesetzt (mindestens 50 Zeichen)
- [ ] `DATABASE_URL` auf PostgreSQL konfiguriert
- [ ] Migrationen ausgeführt: `python manage.py migrate`
- [ ] Health-Check: `python manage.py check --deploy`
- [ ] `DJANGO_ALLOWED_HOSTS` gesetzt ODER Reverse-Proxy validiert Host-Header

## Bei Docker

Falls du Docker verwendest:
```bash
docker run \
  -e DJANGO_DEBUG=0 \
  -e DJANGO_SECRET_KEY="..." \
  -e DATABASE_URL="..." \
  -e DJANGO_ALLOWED_HOSTS="10.10.9.80" \
  -p 8000:8000 \
  basar
```

---

Für weitere Fragen: siehe `DEPLOYMENT.md`
