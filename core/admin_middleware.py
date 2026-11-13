# Django Admin nur bei DEBUG=True erlauben
from django.conf import settings
from django.http import Http404


class AdminDebugOnlyMiddleware:
    """Middleware um Django Admin nur bei DEBUG=True zu erlauben."""
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Prüfe ob Zugriff auf /admin/ versucht wird
        if request.path.startswith('/admin/') and not settings.DEBUG:
            # Zulasse Admin-Zugriff aber mit 404 (wird in Production blockiert)
            # Bei Tests (wo settings.TESTING=True) normale Funktion erlauben
            if not getattr(settings, 'TESTING', False):
                raise Http404("Django Admin ist nur im Entwicklungsmodus (DEBUG=True) verfügbar.")
        
        response = self.get_response(request)
        return response
