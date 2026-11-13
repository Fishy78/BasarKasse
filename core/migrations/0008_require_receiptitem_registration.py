from django.db import migrations, models
import django.db.models.deletion


def ensure_all_items_have_sellers(apps, schema_editor):
    ReceiptItem = apps.get_model("core", "ReceiptItem")
    if ReceiptItem.objects.using(schema_editor.connection.alias).filter(registration__isnull=True).exists():
        raise RuntimeError(
            "Die Migration kann nicht fortgesetzt werden: Mindestens eine Belegposition "
            "hat keine Verkäufernummer."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_remove_bazaar_common_prices_and_more"),
    ]

    operations = [
        migrations.RunPython(ensure_all_items_have_sellers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="receiptitem",
            name="registration",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="core.sellerregistration",
                verbose_name="Verkäufernummer",
            ),
        ),
    ]
