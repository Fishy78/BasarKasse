from django.db import migrations


def close_settled_bazaars(apps, schema_editor):
    database = schema_editor.connection.alias
    Bazaar = apps.get_model("core", "Bazaar")
    BazaarSettlement = apps.get_model("core", "BazaarSettlement")
    for settlement in BazaarSettlement.objects.using(database).all().iterator():
        Bazaar.objects.using(database).filter(pk=settlement.bazaar_id).update(
            status="closed",
            seller_registration_open=False,
            settlement_created_at=settlement.created_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_alter_globalsettings_currency"),
    ]

    operations = [
        migrations.RunPython(close_settled_bazaars, migrations.RunPython.noop),
    ]
