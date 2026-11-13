from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    password_validators_help_text_html,
    validate_password,
)
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .invoice_templates import (
    INVOICE_TEMPLATE_PLACEHOLDER,
    INVOICE_TEMPLATE_VARIABLES,
)
from .models import Bazaar, GlobalSettings, ReceiptItem, SellerInvoiceTemplate

User = get_user_model()


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(label=_("Passwort"), widget=forms.PasswordInput)
    password2 = forms.CharField(label=_("Passwort wiederholen"), widget=forms.PasswordInput)
    consent = forms.BooleanField(
        label=_("Ich stimme der aktuellen Datenschutzerklärung zu."),
        required=True,
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "address_street",
            "address_postal_code",
            "address_city",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.global_settings = GlobalSettings.load()
        self.fields["consent"].label = _("Ich stimme der aktuellen Datenschutzerklärung zu.")
        self.fields["password1"].help_text = password_validators_help_text_html()

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_("Die Passwörter stimmen nicht überein."))
        return password2

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password2")
        if password:
            for field in (
                "first_name",
                "last_name",
                "email",
                "phone_number",
                "address_street",
                "address_postal_code",
                "address_city",
            ):
                if field in cleaned_data:
                    setattr(self.instance, field, cleaned_data[field])
            try:
                validate_password(password, self.instance)
            except ValidationError as error:
                self.add_error("password2", error)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = User.objects.normalize_email(user.email)
        user.set_password(self.cleaned_data["password1"])
        user.is_active = False
        if commit:
            user.save()
        return user


class AdminUserCreationForm(UserCreationForm):
    consent = forms.BooleanField(
        label=_("Ich stimme den Datenschutzhinweisen zu."),
        required=True,
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "address_street",
            "address_postal_code",
            "address_city",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.global_settings = GlobalSettings.load()
        self.fields["consent"].label = _("Der Benutzer stimmt den Datenschutzhinweisen zu.")

    def clean(self):
        cleaned_data = super().clean()
        if not self.global_settings.consent_text.strip():
            raise ValidationError(
                _("Bitte hinterlege zuerst die Datenschutzhinweise in den Systemeinstellungen.")
            )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = not self.global_settings.email_confirmation_required
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "address_street",
            "address_postal_code",
            "address_city",
        ]


class GlobalSettingsForm(forms.ModelForm):
    class Meta:
        model = GlobalSettings
        fields = "__all__"
        widgets = {
            "consent_text": forms.Textarea(attrs={"rows": 14}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consent_text"].help_text = (
            _(
                "HTML-Formatierung ist erlaubt, zum Beispiel <p>, <br>, <strong>, "
                "<em>, <ul>, <ol>, <li>, <h2> und sichere "
                "<a href=\"%(example_url)s\">Links</a>. "
                "Unsichere Elemente und Attribute werden entfernt."
            )
            % {"example_url": "https://…"}
        )

    def clean_common_prices(self):
        prices = self.cleaned_data["common_prices"]
        if not isinstance(prices, list):
            raise ValidationError(_("Übliche Preise müssen als JSON-Liste angegeben werden."))
        normalized = []
        for price in prices:
            try:
                amount = Decimal(str(price))
            except (InvalidOperation, ValueError):
                raise ValidationError(_("Jeder Schnellpreis muss eine gültige Zahl sein.")) from None
            if not amount.is_finite() or amount <= 0 or amount > Decimal("999999.99"):
                raise ValidationError(
                    _("Schnellpreise müssen größer als 0 und höchstens 999999,99 sein.")
                )
            if amount != amount.quantize(Decimal("0.01")):
                raise ValidationError(
                    _("Schnellpreise dürfen höchstens zwei Nachkommastellen haben.")
                )
            normalized.append(f"{amount:.2f}")
        return normalized

    def clean_poster_prompt_instructions(self):
        instructions = self.cleaned_data["poster_prompt_instructions"]
        if not instructions.strip():
            raise ValidationError(
                _("Bitte hinterlege die Standardvorgaben für Plakat-Prompts.")
            )
        return instructions


class SellerInvoiceTemplateForm(forms.ModelForm):
    class Meta:
        model = SellerInvoiceTemplate
        fields = ["name", "body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 18}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        variables = "\n".join(
            f"{{{{ {name} }}}}: {description}"
            for name, description in INVOICE_TEMPLATE_VARIABLES.items()
        )
        self.fields["body"].help_text = (
            _("Verwende einfache Formatierung mit <b>, <i>, <u> und <br>. "
              "Verfügbare Variablen:\n")
            + variables
        )

    def clean_body(self):
        body = self.cleaned_data["body"]
        unknown = set(INVOICE_TEMPLATE_PLACEHOLDER.findall(body)) - set(
            INVOICE_TEMPLATE_VARIABLES
        )
        unmatched = INVOICE_TEMPLATE_PLACEHOLDER.sub("", body)
        if unknown or "{{" in unmatched or "}}" in unmatched:
            names = ", ".join(sorted(unknown))
            message = (
                _("Unbekannte Vorlagenvariablen: %(names)s.") % {"names": names}
                if unknown
                else _("Vorlagenvariablen müssen das Format {{ variablenname }} haben.")
            )
            raise ValidationError(message)
        return body


class DefaultSellerInvoiceTemplateForm(forms.Form):
    template = forms.ModelChoiceField(
        label=_("Standardvorlage für neue Basare"),
        queryset=SellerInvoiceTemplate.objects.none(),
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, templates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = templates


class BazaarManagementForm(forms.ModelForm):
    class Meta:
        model = Bazaar
        fields = [
            "name",
            "starts_at",
            "seller_registration_deadline",
            "status",
            "seller_registration_open",
            "seller_number_width",
            "seller_number_start",
            "seller_number_limit",
            "seller_fee",
            "operator_percent",
            "seller_can_view_current",
            "seller_can_view_history",
            "invoice_template",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "seller_registration_deadline": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_settled:
            self.instance.status = Bazaar.Status.CLOSED
            self.instance.seller_registration_open = False
            self.initial["status"] = Bazaar.Status.CLOSED
            self.fields["status"].disabled = True
            self.fields["status"].help_text = (
                _("Der Basar ist abgerechnet und kann nicht wieder geöffnet werden.")
            )
        global_settings = GlobalSettings.load()
        self.fields["invoice_template"].queryset = SellerInvoiceTemplate.objects.filter(
            global_settings=global_settings
        )
        self.fields["invoice_template"].required = self.fields[
            "invoice_template"
        ].queryset.exists()
        if not self.instance.pk:
            self.instance.seller_fee = global_settings.default_seller_fee
            self.instance.operator_percent = global_settings.default_operator_percent
            self.initial["seller_fee"] = global_settings.default_seller_fee
            self.initial["operator_percent"] = global_settings.default_operator_percent
            self.initial["seller_number_limit"] = global_settings.default_seller_number_limit
            self.initial["invoice_template"] = (
                global_settings.default_seller_invoice_template
            )
        elif not self.instance.invoice_template_id:
            self.initial["invoice_template"] = global_settings.default_seller_invoice_template

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("status") == Bazaar.Status.OPEN:
            cleaned_data["seller_registration_open"] = False
        return cleaned_data


class ReceiptItemForm(forms.ModelForm):
    class Meta:
        model = ReceiptItem
        fields = ["price"]
        widgets = {
            "price": forms.NumberInput(
                attrs={"class": "form-control form-control-lg", "min": "0.01", "step": "0.01"}
            )
        }


class UserManagementForm(forms.ModelForm):
    """Formular zur Benutzerbearbeitung durch Administratoren."""
    is_superuser = forms.BooleanField(
        label=_("Superadministrator"),
        required=False,
        help_text=_("Voller Zugriff auf alle Funktionen und die Verwaltung."),
    )
    is_cashier = forms.BooleanField(
        label=_("Kassenrecht"),
        required=False,
        help_text=_("Darf die Kasse bedienen."),
    )
    is_active = forms.BooleanField(
        label=_("Konto aktiviert"),
        required=False,
        help_text=_("Inaktive Konten können sich nicht anmelden."),
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "address_street",
            "address_postal_code",
            "address_city",
            "is_active",
            "is_cashier",
            "is_superuser",
        ]
        widgets = {
            "email": forms.EmailInput(attrs={"readonly": "readonly"}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        super().__init__(*args, **kwargs)
        # Initiale Werte aus Datenbankfeld
        if self.instance and self.instance.pk:
            self.fields["is_superuser"].initial = self.instance.is_superuser
            self.fields["is_cashier"].initial = self.instance.is_cashier
            self.fields["is_active"].initial = self.instance.is_active

    def clean(self):
        cleaned_data = super().clean()
        # Verhindere Selbstentfernung von Superadmin-Rechten
        if self.current_user and self.instance.pk == self.current_user.pk:
            if self.instance.is_superuser and not cleaned_data.get("is_superuser"):
                raise ValidationError(
                    _("Du kannst deine eigenen Superadmin-Rechte nicht entfernen. "
                      "Richte zuerst einen anderen Administrator ein.")
                )
            if not cleaned_data.get("is_active"):
                raise ValidationError(
                    _("Du kannst dein eigenes Konto nicht deaktivieren.")
                )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = self.cleaned_data.get("is_superuser", False)
        user.is_cashier = self.cleaned_data.get("is_cashier", False)
        user.is_active = self.cleaned_data.get("is_active", True)
        if commit:
            user.save()
        return user


class UserSearchForm(forms.Form):
    """Formular zum Filtern der Benutzerliste."""
    FILTER_CHOICES = [
        ("", _("Alle Benutzer")),
        ("active", _("Aktiviert")),
        ("inactive", _("Deaktiviert")),
        ("cashier", _("Mit Kassenrecht")),
        ("admin", _("Administratoren")),
    ]

    search = forms.CharField(
        label=_("Nach Name oder E-Mail suchen"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Name, E-Mail-Adresse...")}),
    )
    filter = forms.ChoiceField(
        label=_("Filtern nach"),
        choices=FILTER_CHOICES,
        required=False,
    )
