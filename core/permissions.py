"""
Custom Permissions für das Basar-System.
"""
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _


def ensure_settlement_permissions():
    """
    Erstellt die Settlement-Ansicht-Berechtigung, falls sie nicht existiert.
    Wird in einer Migration aufgerufen.
    """
    from core.models import BazaarSettlement
    
    content_type = ContentType.objects.get_for_model(BazaarSettlement)
    Permission.objects.get_or_create(
        codename='view_settlements',
        content_type=content_type,
        defaults={
            'name': _('Kann Abrechnungen anzeigen und herunterladen')
        }
    )
