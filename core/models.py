from decimal import Decimal
import os

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.files.storage import FileSystemStorage
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils.formats import number_format
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _get_private_media_storage():
    """Storage-Backend für private, nicht öffentlich erreichbare Dateien."""
    return FileSystemStorage(
        location=settings.PRIVATE_MEDIA_ROOT,
        base_url=settings.PRIVATE_MEDIA_URL
    )


class PrivateFileField(models.FileField):
    """
    FileField für sensible Dateien (z.B. Abrechnungs-PDFs), die außerhalb
    von MEDIA_ROOT in PRIVATE_MEDIA_ROOT gespeichert werden und nur über
    autorisierte Views zugänglich sind.
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('storage', _get_private_media_storage)
        kwargs.setdefault('upload_to', 'settlements/')
        super().__init__(*args, **kwargs)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError(_("Eine E-Mail-Adresse ist erforderlich."))
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError(
                _("Ein Administrator muss is_staff und is_superuser aktiviert haben.")
            )
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(_("E-Mail-Adresse"), unique=True)
    first_name = models.CharField(_("Vorname"), max_length=150, default="")
    last_name = models.CharField(_("Nachname"), max_length=150, default="")
    phone_number = models.CharField(_("Telefonnummer"), max_length=30, blank=True)
    address_street = models.CharField(_("Straße und Hausnummer"), max_length=200, blank=True)
    address_postal_code = models.CharField(
        _("Postleitzahl"),
        max_length=5,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^\d{5}$",
                message=_("Bitte eine fünfstellige deutsche Postleitzahl eingeben."),
            )
        ],
    )
    address_city = models.CharField(_("Ort"), max_length=100, blank=True)
    is_active = models.BooleanField(
        _("Aktiv"),
        default=False,
        help_text=_("Wird nach Bestätigung der E-Mail-Adresse aktiviert."),
    )
    is_cashier = models.BooleanField(
        _("Kassenrecht"),
        default=False,
        help_text=_("Kassenrecht wird durch einen Administrator vergeben."),
    )
    is_anonymized = models.BooleanField(
        _("Konto anonymisiert"),
        default=False,
        editable=False,
    )

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("Benutzer")
        verbose_name_plural = _("Benutzer")

    def __str__(self):
        return _("Gelöschter Benutzer") if self.is_anonymized else self.email


class GlobalSettings(models.Model):
    class DesignTheme(models.TextChoices):
        CLASSIC = "classic", _("Klassisches einfaches Design")
        DARK = "dark", _("Dunkelmodus")
        MODERN = "modern", _("Modernes Design")
        KINDERGARTEN = "kindergarten", _("Buntes Kindergartendesign")

    class MenuPosition(models.TextChoices):
        TOP = "top", _("Oben")
        LEFT = "left", _("Links")
        RIGHT = "right", _("Rechts")

    class Currency(models.TextChoices):
        EUR = "EUR", _("Euro (€)")
        CHF = "CHF", _("Schweizer Franken (CHF)")
        USD = "USD", _("US-Dollar (USD)")
        GBP = "GBP", _("Britisches Pfund (GBP)")

    common_prices = models.JSONField(_("Übliche Preise"), default=list, blank=True)
    site_title = models.CharField(
        _("Seitentitel"),
        max_length=100,
        default="Basar",
        help_text=_("Wird in der Kopfzeile und im Seitentitel angezeigt."),
    )
    logo = models.ImageField(
        _("Logo für Verkäuferabrechnungen"),
        upload_to="global-logo/",
        blank=True,
    )
    default_seller_invoice_template = models.ForeignKey(
        "SellerInvoiceTemplate",
        on_delete=models.SET_NULL,
        related_name="default_for_settings",
        verbose_name=_("Standard-Verkäuferrechnungsvorlage"),
        null=True,
        blank=True,
        editable=False,
    )
    default_seller_number_limit = models.PositiveIntegerField(
        _("Standard-Anzahl Verkäufernummern"),
        default=1000,
        validators=[MinValueValidator(1), MaxValueValidator(1000000)],
    )
    currency = models.CharField(
        _("Währung"),
        max_length=3,
        choices=Currency.choices,
        default=Currency.EUR,
    )
    design_theme = models.CharField(
        _("Oberflächendesign"),
        max_length=20,
        choices=DesignTheme.choices,
        default=DesignTheme.CLASSIC,
    )
    menu_position = models.CharField(
        _("Menüposition"),
        max_length=5,
        choices=MenuPosition.choices,
        default=MenuPosition.TOP,
    )
    menu_fixed = models.BooleanField(_("Menü beim Scrollen fixieren"), default=False)
    default_language = models.CharField(
        _("Standardsprache"),
        max_length=10,
        choices=settings.LANGUAGES,
        default="de",
        help_text=_("Wird für Benutzer ohne eigene Sprachauswahl verwendet."),
    )
    consent_version = models.CharField(_("Einwilligungsversion"), max_length=40, default="1")
    consent_text = models.TextField(_("Einwilligungstext"), blank=True)
    default_seller_fee = models.DecimalField(
        _("Standard-Verkäufergebühr für neue Basare"),
        max_digits=8,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    default_operator_percent = models.DecimalField(
        _("Standard-Betreiberanteil für neue Basare"),
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    poster_prompt_instructions = models.TextField(
        _("Standardvorgaben für Plakat-Prompts"),
        default=_(
            "Erstelle ein übersichtliches, gut lesbares Basarplakat. "
            "Verwende ausschließlich die unten angegebenen Veranstaltungsdaten "
            "und erfinde keine zusätzlichen Angaben."
        ),
    )
    email_confirmation_required = models.BooleanField(
        _("E-Mail-Bestätigung erforderlich"),
        default=True,
        help_text=_("Gilt für neue Konten. Bei Aktivierung muss der Benutzer die Adresse bestätigen."),
    )
    password_min_length = models.PositiveSmallIntegerField(
        _("Mindestlänge des Passworts"),
        default=12,
        validators=[MinValueValidator(8), MaxValueValidator(128)],
        help_text=_("Zulässiger Bereich: 8 bis 128 Zeichen."),
    )
    password_require_uppercase = models.BooleanField(
        _("Großbuchstaben verlangen"),
        default=True,
    )
    password_require_lowercase = models.BooleanField(
        _("Kleinbuchstaben verlangen"),
        default=True,
    )
    password_require_number = models.BooleanField(
        _("Ziffer verlangen"),
        default=True,
    )
    password_require_special = models.BooleanField(
        _("Sonderzeichen verlangen"),
        default=True,
    )
    password_block_common = models.BooleanField(
        _("Häufige Passwörter ablehnen"),
        default=True,
    )
    password_block_numeric_only = models.BooleanField(
        _("Reine Zahlenpasswörter ablehnen"),
        default=True,
    )
    password_check_similarity = models.BooleanField(
        _("Ähnlichkeit zu Benutzerdaten prüfen"),
        default=True,
    )
    price_label_columns = models.PositiveSmallIntegerField(
        _("Preisschilder: Spalten pro A4"),
        default=5,
        validators=[MinValueValidator(2), MaxValueValidator(10)],
        help_text=_("Anzahl der Spalten bei der Preisschild-Generierung (2-10)."),
    )
    price_label_rows = models.PositiveSmallIntegerField(
        _("Preisschilder: Reihen pro A4"),
        default=20,
        validators=[MinValueValidator(4), MaxValueValidator(30)],
        help_text=_("Anzahl der Reihen bei der Preisschild-Generierung (4-30)."),
    )

    class Meta:
        verbose_name = _("Globale Einstellung")
        verbose_name_plural = _("Globale Einstellungen")

    def __str__(self):
        return _("Globale Einstellungen")

    @property
    def currency_display(self):
        return "€" if self.currency == self.Currency.EUR else self.currency

    def clean(self):
        if self.pk:
            previous = GlobalSettings.objects.filter(pk=self.pk).first()
            if (
                previous
                and previous.consent_text.strip()
                and self.consent_text != previous.consent_text
                and self.consent_version == previous.consent_version
            ):
                from django.core.exceptions import ValidationError

                raise ValidationError(
                    {
                        "consent_version": (
                            _("Bei einer Änderung des Einwilligungstextes muss auch die Version erhöht werden.")
                        )
                    }
                )

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class SellerInvoiceTemplate(models.Model):
    global_settings = models.ForeignKey(
        GlobalSettings,
        on_delete=models.CASCADE,
        related_name="seller_invoice_templates",
        verbose_name=_("Globale Einstellungen"),
    )
    name = models.CharField(_("Vorlagenname"), max_length=100)
    body = models.TextField(_("Vorlageninhalt"))
    is_read_only = models.BooleanField(_("Schreibgeschützt"), default=False, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["global_settings", "name"],
                name="unique_seller_invoice_template_name",
            )
        ]
        verbose_name = _("Verkäuferrechnungsvorlage")
        verbose_name_plural = _("Verkäuferrechnungsvorlagen")

    def __str__(self):
        return self.name


class UserConsent(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="consents")
    version = models.CharField(_("Version"), max_length=40)
    text = models.TextField(_("Einwilligungstext"))
    accepted_at = models.DateTimeField(_("Zugestimmt am"))

    class Meta:
        ordering = ["-accepted_at"]
        verbose_name = _("Benutzereinwilligung")
        verbose_name_plural = _("Benutzereinwilligungen")

    def __str__(self):
        return _("%(email)s: %(version)s") % {
            "email": self.user.email,
            "version": self.version,
        }


class Bazaar(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Geplant")
        OPEN = "open", _("Aktiv")
        CLOSED = "closed", _("Abgeschlossen")

    name = models.CharField(_("Name"), max_length=120)
    starts_at = models.DateTimeField(_("Beginn"))
    seller_registration_deadline = models.DateTimeField(
        _("Nummernvergabe möglich bis"),
        help_text=_("Muss am oder vor dem Beginn des Basars liegen."),
    )
    status = models.CharField(
        _("Status"),
        max_length=12,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    seller_registration_open = models.BooleanField(
        _("Verkäuferanmeldung geöffnet"),
        default=True,
        help_text=_("Wird beim Aktivieren des Basars automatisch geschlossen."),
    )
    seller_number_width = models.PositiveSmallIntegerField(
        _("Stellenzahl"),
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    seller_number_start = models.PositiveIntegerField(_("Erste Nummer"), default=1)
    seller_number_limit = models.PositiveIntegerField(
        _("Maximale Anzahl Verkäufernummern"),
        default=1000,
        validators=[MinValueValidator(1), MaxValueValidator(1000000)],
    )
    next_receipt_number = models.PositiveIntegerField(
        _("Nächste Belegnummer"),
        default=1,
        editable=False,
    )
    seller_fee = models.DecimalField(
        _("Verkäufergebühr"),
        max_digits=8,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    operator_percent = models.DecimalField(
        _("Betreiberanteil in Prozent"),
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    seller_can_view_current = models.BooleanField(
        _("Verkäufer sehen aktuellen Stand"),
        default=True,
    )
    seller_can_view_history = models.BooleanField(
        _("Verkäufer sehen Historie"),
        default=True,
    )
    invoice_template = models.ForeignKey(
        SellerInvoiceTemplate,
        on_delete=models.PROTECT,
        related_name="bazaars",
        verbose_name=_("Verkäuferrechnungsvorlage"),
        null=True,
        blank=True,
    )
    poster_prompt = models.TextField(_("Plakat-Prompt"), blank=True)
    settlement_created_at = models.DateTimeField(
        _("Abrechnung erstellt am"),
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        ordering = ["-starts_at", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(seller_registration_deadline__lte=F("starts_at")),
                name="bazaar_registration_deadline_before_start",
            )
        ]
        verbose_name = _("Basar")
        verbose_name_plural = _("Basare")

    def __str__(self):
        return self.name

    @property
    def is_settled(self):
        return bool(
            self.settlement_created_at
            or (
                self.pk
                and BazaarSettlement.objects.filter(
                    bazaar_id=self.pk,
                    cancelled_at__isnull=True,
                ).exists()
            )
        )

    @property
    def has_settlement_history(self):
        return bool(self.pk and self.settlements.all())

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.pk and self.status != self.Status.CLOSED and self.is_settled:
            raise ValidationError(
                {
                    "status": _(
                        "Ein bereits abgerechneter Basar kann nicht wieder geöffnet werden."
                    )
                }
            )
        if (
            self.starts_at
            and self.seller_registration_deadline
            and self.seller_registration_deadline > self.starts_at
        ):
            raise ValidationError(
                {
                    "seller_registration_deadline": (
                        _("Die Nummernvergabe darf nicht nach dem Beginn des Basars enden.")
                    )
                }
            )
        if self.pk and self.seller_number_limit < self.registrations.count():
            raise ValidationError(
                {
                    "seller_number_limit": (
                        _(
                            "Die Nummernbegrenzung darf nicht unter der bereits vergebenen Anzahl liegen."
                        )
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        if self.status == self.Status.OPEN:
            self.seller_registration_open = False
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"seller_registration_open"}
        super().save(*args, **kwargs)

    def format_seller_number(self, sequence):
        return f"{sequence:0{self.seller_number_width}d}"


class SellerRegistration(models.Model):
    bazaar = models.ForeignKey(
        Bazaar,
        on_delete=models.PROTECT,
        related_name="registrations",
        verbose_name=_("Basar"),
    )
    seller = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="seller_registrations",
        verbose_name=_("Verkäufer"),
    )
    seller_number = models.CharField(_("Verkäufernummer"), max_length=24)
    registered_at = models.DateTimeField(
        _("Verkäufernummer vergeben am"),
        default=timezone.now,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        ordering = ["seller_number"]
        constraints = [
            models.UniqueConstraint(fields=["bazaar", "seller"], name="one_registration_per_seller"),
            models.UniqueConstraint(
                fields=["bazaar", "seller_number"],
                name="unique_seller_number_per_bazaar",
            ),
        ]
        verbose_name = _("Verkäuferanmeldung")
        verbose_name_plural = _("Verkäuferanmeldungen")

    def __str__(self):
        return f"{self.bazaar}: {self.seller_number}"


class Receipt(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("Offen")
        COMPLETE = "complete", _("Abgeschlossen")

    bazaar = models.ForeignKey(
        Bazaar,
        on_delete=models.PROTECT,
        related_name="receipts",
        verbose_name=_("Basar"),
    )
    cashier = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="cashier_receipts",
        verbose_name=_("Kassierer"),
    )
    receipt_number = models.PositiveIntegerField(_("Belegnummer"))
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(_("Erstellt am"), auto_now_add=True)
    first_item_at = models.DateTimeField(
        _("Erster Artikel erfasst am"),
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(_("Abgeschlossen am"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["bazaar", "receipt_number"], name="unique_receipt_number_per_bazaar"),
        ]
        verbose_name = _("Kassenbeleg")
        verbose_name_plural = _("Kassenbelege")

    def __str__(self):
        return _("%(bazaar)s, Beleg Nr. %(number)s") % {
            "bazaar": self.bazaar,
            "number": self.receipt_number,
        }

    @property
    def total(self):
        return sum(
            (item.price for item in self.items.filter(cancelled_at__isnull=True)),
            start=Decimal("0.00"),
        )


class ReceiptItem(models.Model):
    receipt = models.ForeignKey(
        Receipt,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Kassenbeleg"),
    )
    registration = models.ForeignKey(
        SellerRegistration,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name=_("Verkäufernummer"),
    )
    price = models.DecimalField(
        _("Preis"),
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    created_at = models.DateTimeField(_("Erfasst am"), auto_now_add=True)
    cancelled_at = models.DateTimeField(_("Storniert am"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="cancelled_receipt_items",
        null=True,
        blank=True,
        verbose_name=_("Storniert von"),
    )

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="receipt_item_price_positive"),
        ]
        verbose_name = _("Belegposition")
        verbose_name_plural = _("Belegpositionen")

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.receipt_id and self.registration_id:
            if self.receipt.bazaar_id != self.registration.bazaar_id:
                raise ValidationError(
                    {
                        "registration": _(
                            "Die Verkäufernummer muss zum Basar des Kassenbelegs gehören."
                        )
                    }
                )

    def __str__(self):
        return _("%(seller_number)s: %(price)s %(currency)s") % {
            "seller_number": self.registration.seller_number,
            "price": number_format(self.price, decimal_pos=2),
            "currency": GlobalSettings.load().currency_display,
        }


class BazaarSettlement(models.Model):
    bazaar = models.ForeignKey(
        Bazaar,
        on_delete=models.PROTECT,
        related_name="settlements",
        verbose_name=_("Basar"),
    )
    created_at = models.DateTimeField(_("Erstellt am"), auto_now_add=True)
    snapshot = models.JSONField(_("Abrechnungsdaten"))
    pdf = PrivateFileField(_("PDF"), blank=True)
    cancelled_at = models.DateTimeField(
        _("Storniert am"),
        null=True,
        blank=True,
        editable=False,
    )
    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="cancelled_bazaar_settlements",
        verbose_name=_("Storniert von"),
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bazaar"],
                condition=models.Q(cancelled_at__isnull=True),
                name="one_active_settlement_per_bazaar",
            )
        ]
        verbose_name = _("Basarabrechnung")
        verbose_name_plural = _("Basarabrechnungen")

    def __str__(self):
        return _("Abrechnung: %(bazaar)s") % {"bazaar": self.bazaar}


class ScannerSession(models.Model):
    """Verbindung zwischen Kasse und mobilen QR-Code-Scanner"""

    code = models.CharField(
        _("Anmeldecode"),
        max_length=50,
        unique=True,
        db_index=True,
    )
    bazaar = models.ForeignKey(
        Bazaar,
        on_delete=models.CASCADE,
        related_name="scanner_sessions",
        verbose_name=_("Basar"),
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="scanner_sessions",
        verbose_name=_("Kassierer"),
    )
    created_at = models.DateTimeField(_("Erstellt am"), auto_now_add=True)
    expires_at = models.DateTimeField(
        _("Ablaufzeit"),
        help_text=_("Session verfällt nach 30 Minuten"),
    )
    is_active = models.BooleanField(
        _("Aktiv"),
        default=True,
        help_text=_("Wird deaktiviert, wenn Session abläuft oder manuell geschlossen wird"),
    )
    device_name = models.CharField(
        _("Gerätenname (optional)"),
        max_length=100,
        blank=True,
        help_text=_("Z.B. 'iPhone 14 Pro'"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Scanner-Session")
        verbose_name_plural = _("Scanner-Sessions")

    def __str__(self):
        return _("Scanner: %(code)s (%(bazaar)s)") % {
            "code": self.code[:8],
            "bazaar": self.bazaar.name,
        }

    @property
    def is_expired(self):
        """Prüft, ob Session abgelaufen ist"""
        return timezone.now() > self.expires_at

    def mark_expired(self):
        """Markiert Session als abgelaufen"""
        if self.is_active:
            self.is_active = False
            self.save(update_fields=["is_active"])


class ScannerSubmission(models.Model):
    """Gescannte Artikel von Handy zu Kasse"""

    session = models.ForeignKey(
        ScannerSession,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name=_("Scanner-Session"),
    )
    receipt = models.ForeignKey(
        "Receipt",
        on_delete=models.SET_NULL,
        related_name="scanner_submissions",
        null=True,
        blank=True,
        verbose_name=_("Beleg"),
    )
    registration_id = models.IntegerField(
        _("Verkäufernummer-ID"),
        help_text=_("Referenz auf SellerRegistration.id"),
    )
    price = models.DecimalField(
        _("Preis"),
        max_digits=8,
        decimal_places=2,
    )
    submitted_at = models.DateTimeField(
        _("Eingescannt um"),
        auto_now_add=True,
    )
    processed = models.BooleanField(
        _("Verarbeitet"),
        default=False,
        help_text=_("True, wenn Artikel bereits zur Kasse hinzugefügt wurde"),
    )
    processed_at = models.DateTimeField(
        _("Verarbeitet um"),
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["processed", "-submitted_at"]
        verbose_name = _("Scanner-Eingabe")
        verbose_name_plural = _("Scanner-Eingaben")

    def __str__(self):
        return _("Scan: VKNr %(reg)s, %(price)s€") % {
            "reg": self.registration_id,
            "price": self.price,
        }

    def mark_processed(self):
        """Markiert Submission als verarbeitet"""
        self.processed = True
        self.processed_at = timezone.now()
        self.save(update_fields=["processed", "processed_at"])
