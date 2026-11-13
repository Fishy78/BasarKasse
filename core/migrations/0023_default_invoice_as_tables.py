from django.db import migrations


OLD_DEFAULT_TEMPLATE = """<b>Verkäuferabrechnung</b><br>
Basar: {{ basar_name }}<br>
Datum: {{ basar_datum }}<br>
Verkäufernummer: {{ verkaeufernummer }}<br>
Verkäufer: {{ vorname }} {{ name }}<br><br>
Artikel (Preis × Anzahl = Summe):<br>
{{ artikel_zeilen }}<br><br>
Bruttoumsatz: {{ bruttoumsatz }} {{ waehrung }}<br>
Verkäufergebühr: -{{ verkaeufergebuehr }} {{ waehrung }}<br>
Betreiberanteil ({{ betreiberanteil_prozent }} %): -{{ betreiberanteil }} {{ waehrung }}<br>
<b>Auszahlung: {{ auszahlung }} {{ waehrung }}</b>"""

NEW_DEFAULT_TEMPLATE = """<b>Verkäuferabrechnung</b><br>
Basar: {{ basar_name }}<br>
Datum: {{ basar_datum }}<br>
Verkäufernummer: {{ verkaeufernummer }}<br><br>
Verkäufer: {{ vorname }} {{ name }}<br><br>
<b>Abrechnung</b><br>
{{ artikel_zeilen }}<br><br>
{{ summen }}"""


def update_default_template(apps, schema_editor):
    SellerInvoiceTemplate = apps.get_model("core", "SellerInvoiceTemplate")
    templates = SellerInvoiceTemplate.objects.using(
        schema_editor.connection.alias
    ).filter(name="Standard")
    for template in templates:
        if template.body.replace("\r\n", "\n") == OLD_DEFAULT_TEMPLATE:
            newline = "\r\n" if "\r\n" in template.body else "\n"
            template.body = NEW_DEFAULT_TEMPLATE.replace("\n", newline)
            template.save(update_fields=["body"])


def restore_default_template(apps, schema_editor):
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
        ("core", "0022_add_seller_names_to_default_invoice_template"),
    ]

    operations = [
        migrations.RunPython(update_default_template, restore_default_template),
    ]
