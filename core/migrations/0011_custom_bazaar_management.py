import django.core.validators
from django.db import migrations, models


DEFAULT_POSTER_INSTRUCTIONS = (
    "Erstelle ein übersichtliches, gut lesbares Basarplakat. "
    "Verwende ausschließlich die unten angegebenen Veranstaltungsdaten "
    "und erfinde keine zusätzlichen Angaben."
)


def migrate_bazaar_defaults(apps, schema_editor):
    database = schema_editor.connection.alias
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    Bazaar = apps.get_model("core", "Bazaar")
    settings, _ = GlobalSettings.objects.using(database).get_or_create(pk=1)
    first_bazaar = Bazaar.objects.using(database).order_by("pk").first()
    if first_bazaar:
        settings.default_seller_fee = first_bazaar.seller_fee
        settings.default_operator_percent = first_bazaar.operator_percent
        settings.save(
            using=database,
            update_fields=["default_seller_fee", "default_operator_percent"],
        )
    for bazaar in Bazaar.objects.using(database).all().iterator():
        bazaar.seller_registration_deadline = bazaar.starts_at
        if bazaar.status == "open":
            bazaar.seller_registration_open = False
        bazaar.save(
            using=database,
            update_fields=["seller_registration_deadline", "seller_registration_open"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_receipt_first_item_at_receiptitem_cancelled_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalsettings",
            name="default_seller_fee",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=8,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="Standard-Verkäufergebühr für neue Basare",
            ),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="default_operator_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
                verbose_name="Standard-Betreiberanteil für neue Basare",
            ),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="poster_prompt_instructions",
            field=models.TextField(
                default=DEFAULT_POSTER_INSTRUCTIONS,
                verbose_name="Standardvorgaben für Plakat-Prompts",
            ),
        ),
        migrations.AddField(
            model_name="bazaar",
            name="seller_registration_deadline",
            field=models.DateTimeField(
                blank=True,
                help_text="Muss am oder vor dem Beginn des Basars liegen.",
                null=True,
                verbose_name="Nummernvergabe möglich bis",
            ),
        ),
        migrations.AddField(
            model_name="bazaar",
            name="poster_prompt",
            field=models.TextField(blank=True, verbose_name="Plakat-Prompt"),
        ),
        migrations.RunPython(migrate_bazaar_defaults, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bazaar",
            name="seller_registration_deadline",
            field=models.DateTimeField(
                help_text="Muss am oder vor dem Beginn des Basars liegen.",
                verbose_name="Nummernvergabe möglich bis",
            ),
        ),
        migrations.AddConstraint(
            model_name="bazaar",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    seller_registration_deadline__lte=models.F("starts_at")
                ),
                name="bazaar_registration_deadline_before_start",
            ),
        ),
        migrations.RemoveField(
            model_name="bazaar",
            name="seller_number_prefix",
        ),
        migrations.AlterField(
            model_name="bazaar",
            name="seller_registration_open",
            field=models.BooleanField(
                default=True,
                help_text="Wird beim Aktivieren des Basars automatisch geschlossen.",
                verbose_name="Verkäuferanmeldung geöffnet",
            ),
        ),
    ]
