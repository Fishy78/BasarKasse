from django.db import migrations
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


def create_settlement_permissions(apps, schema_editor):
    """Erstellt Settlement-spezifische Berechtigungen."""
    BazaarSettlement = apps.get_model('core', 'BazaarSettlement')
    content_type = ContentType.objects.get_for_model(BazaarSettlement)
    
    Permission.objects.get_or_create(
        codename='view_settlements',
        content_type=content_type,
        defaults={
            'name': 'Kann Abrechnungen anzeigen und herunterladen'
        }
    )


def reverse_settlement_permissions(apps, schema_editor):
    """Entfernt Settlement-Berechtigungen beim Rollback."""
    BazaarSettlement = apps.get_model('core', 'BazaarSettlement')
    content_type = ContentType.objects.get_for_model(BazaarSettlement)
    Permission.objects.filter(
        codename='view_settlements',
        content_type=content_type,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_alter_bazaarsettlement_pdf'),
    ]

    operations = [
        migrations.RunPython(create_settlement_permissions, reverse_settlement_permissions),
    ]
