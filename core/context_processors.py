from django.conf import settings as django_settings
from .models import GlobalSettings


def global_design(request):
    settings = GlobalSettings.load()
    return {
        "global_design": settings.design_theme,
        "global_menu_position": settings.menu_position,
        "global_menu_fixed": settings.menu_fixed,
        "global_currency": settings.currency_display,
        "global_site_title": settings.site_title,
        "debug": django_settings.DEBUG,
    }
