import re
from html import escape

from django.utils.translation import gettext_lazy as _


INVOICE_TEMPLATE_VARIABLES = {
    "basar_name": _("Name des Basars"),
    "basar_datum": _("Datum des Basars"),
    "verkaeufernummer": _("Verkäufernummer"),
    "name": _("Nachname des Verkäufers"),
    "vorname": _("Vorname des Verkäufers"),
    "artikel_zeilen": _("Artikeltabelle mit Preis, Anzahl und rechtsbündiger Summe"),
    "summen": _("Tabelle mit Bruttoumsatz, Abzügen und Auszahlungsbetrag"),
    "bruttoumsatz": _("Umsatz vor Abzügen"),
    "verkaeufergebuehr": _("Feste Verkäufergebühr"),
    "betreiberanteil_prozent": _("Prozentualer Betreiberanteil"),
    "betreiberanteil": _("Betrag des Betreiberanteils"),
    "auszahlung": _("Auszahlungsbetrag nach Abzügen"),
    "waehrung": _("Währung"),
}

DEFAULT_SELLER_INVOICE_TEMPLATE = _("""<b>Verkäuferabrechnung</b><br>
Basar: {{ basar_name }}<br>
Datum: {{ basar_datum }}<br>
Verkäufernummer: {{ verkaeufernummer }}<br><br>
Verkäufer: {{ vorname }} {{ name }}<br><br>
<b>Abrechnung</b><br>
{{ artikel_zeilen }}<br><br>
{{ summen }}""")

INVOICE_TEMPLATE_PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")


def render_invoice_template(source, context):
    source = str(source)

    def replace(match):
        name = match.group(1)
        value = context.get(name, match.group(0))
        return escape(str(value))

    return INVOICE_TEMPLATE_PLACEHOLDER.sub(replace, source)
