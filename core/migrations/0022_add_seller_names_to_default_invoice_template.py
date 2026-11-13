from django.db import migrations


OLD_DEFAULT_TEMPLATE = """<b>Verkäuferabrechnung</b><br>
Basar: {{ basar_name }}<br>
Datum: {{ basar_datum }}<br>
Verkäufernummer: {{ verkaeufernummer }}<br><br>
Artikel (Preis × Anzahl = Summe):<br>
{{ artikel_zeilen }}<br><br>
Bruttoumsatz: {{ bruttoumsatz }} {{ waehrung }}<br>
Verkäufergebühr: -{{ verkaeufergebuehr }} {{ waehrung }}<br>
Betreiberanteil ({{ betreiberanteil_prozent }} %): -{{ betreiberanteil }} {{ waehrung }}<br>
<b>Auszahlung: {{ auszahlung }} {{ waehrung }}</b>"""

NEW_DEFAULT_TEMPLATE = OLD_DEFAULT_TEMPLATE.replace(
    "Verkäufernummer: {{ verkaeufernummer }}<br><br>",
    "Verkäufernummer: {{ verkaeufernummer }}<br>\n"
    "Verkäufer: {{ vorname }} {{ name }}<br><br>",
)


def add_seller_names(apps, schema_editor):
    SellerInvoiceTemplate = apps.get_model("core", "SellerInvoiceTemplate")
    templates = SellerInvoiceTemplate.objects.using(
        schema_editor.connection.alias
    ).filter(name="Standard")
    for template in templates:
        if template.body.replace("\r\n", "\n") == OLD_DEFAULT_TEMPLATE:
            newline = "\r\n" if "\r\n" in template.body else "\n"
            template.body = NEW_DEFAULT_TEMPLATE.replace("\n", newline)
            template.save(update_fields=["body"])


def restore_old_default(apps, schema_editor):
    SellerInvoiceTemplate = apps.get_model("core", "SellerInvoiceTemplate")
    templates = SellerInvoiceTemplate.objects.using(
        schema_editor.connection.alias
    ).filter(name="Standard")
    for template in templates:
        if template.body.replace("\r\n", "\n") == NEW_DEFAULT_TEMPLATE:
            newline = "\r\n" if "\r\n" in template.body else "\n"
            template.body = OLD_DEFAULT_TEMPLATE.replace("\n", newline)
            template.save(update_fields=["body"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_remove_bazaar_logo_bazaar_seller_number_limit_and_more"),
    ]

    operations = [
        migrations.RunPython(add_seller_names, restore_old_default),
    ]
