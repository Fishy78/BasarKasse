import django.db.models.deletion
from django.db import migrations, models


def set_standard_template(apps, schema_editor):
    database = schema_editor.connection.alias
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    SellerInvoiceTemplate = apps.get_model("core", "SellerInvoiceTemplate")

    template = SellerInvoiceTemplate.objects.using(database).filter(name="Standard").first()
    if not template:
        settings, _ = GlobalSettings.objects.using(database).get_or_create(pk=1)
        template = SellerInvoiceTemplate.objects.using(database).create(
            global_settings_id=settings.pk,
            name="Standard",
            body="",
        )
    SellerInvoiceTemplate.objects.using(database).filter(pk=template.pk).update(
        is_read_only=True
    )
    GlobalSettings.objects.using(database).filter(pk=1).update(
        default_seller_invoice_template_id=template.pk
    )


def unset_standard_template(apps, schema_editor):
    database = schema_editor.connection.alias
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    SellerInvoiceTemplate = apps.get_model("core", "SellerInvoiceTemplate")
    GlobalSettings.objects.using(database).filter(pk=1).update(
        default_seller_invoice_template_id=None
    )
    SellerInvoiceTemplate.objects.using(database).filter(
        name="Standard",
        is_read_only=True,
    ).update(is_read_only=False)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_default_invoice_as_tables"),
    ]

    operations = [
        migrations.AddField(
            model_name="sellerinvoicetemplate",
            name="is_read_only",
            field=models.BooleanField(default=False, editable=False, verbose_name="Schreibgeschützt"),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="default_seller_invoice_template",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_settings",
                to="core.sellerinvoicetemplate",
                verbose_name="Standard-Verkäuferrechnungsvorlage",
            ),
        ),
        migrations.RunPython(set_standard_template, unset_standard_template),
    ]
