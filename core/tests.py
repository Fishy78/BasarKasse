from datetime import timedelta
from decimal import Decimal
import re
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, override as translation_override
from PIL import Image as PillowImage

from .models import (
    Bazaar,
    BazaarSettlement,
    GlobalSettings,
    Receipt,
    ReceiptItem,
    SellerRegistration,
    SellerInvoiceTemplate,
    UserConsent,
    ScannerSession,
    ScannerSubmission,
)
from .middleware import (
    COOKIE_CONSENT_COOKIE,
    COOKIE_CONSENT_SALT,
    COOKIE_CONSENT_VALUE,
)
from .views import _build_pdf, _settlement_data

User = get_user_model()


class BazaarWorkflowTests(TestCase):
    def setUp(self):
        self.accept_cookies(self.client)
        self.seller = User.objects.create_user(
            email="seller@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        self.cashier = User.objects.create_user(
            email="cashier@example.com",
            password="SecurePass123!",
            is_active=True,
            is_cashier=True,
        )
        self.settings = GlobalSettings.objects.get(pk=1)
        self.settings.common_prices = ["1.00", "2.50"]
        self.settings.consent_version = "2026-01"
        self.settings.consent_text = "Datenschutzhinweise"
        self.settings.email_confirmation_required = True
        self.settings.save()
        UserConsent.objects.create(
            user=self.seller,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        UserConsent.objects.create(
            user=self.cashier,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.bazaar = Bazaar.objects.create(
            name="Frühlingsbasar",
            starts_at=timezone.now() + timedelta(days=14),
            seller_registration_deadline=timezone.now() + timedelta(days=7),
            status=Bazaar.Status.PLANNED,
            seller_number_width=3,
            seller_number_start=1,
        )

    @staticmethod
    def accept_cookies(client):
        response = HttpResponse()
        response.set_signed_cookie(
            COOKIE_CONSENT_COOKIE,
            COOKIE_CONSENT_VALUE,
            salt=COOKIE_CONSENT_SALT,
        )
        client.cookies.update(response.cookies)

    def test_cookie_consent_is_required_and_lists_first_party_cookies(self):
        self.client.cookies.pop(COOKIE_CONSENT_COOKIE, None)

        response = self.client.get(reverse("login"))

        self.assertRedirects(
            response,
            f'{reverse("cookie_consent")}?next={reverse("login")}',
            fetch_redirect_response=False,
        )
        response = self.client.get(
            reverse("cookie_consent"),
            {"next": reverse("login")},
        )
        self.assertContains(response, "keine Cookies von Drittanbietern")
        for cookie_name in (
            "basar_cookie_consent",
            "sessionid",
            "csrftoken",
            "_messages",
            "django_language",
        ):
            self.assertContains(response, cookie_name)

        response = self.client.post(
            reverse("cookie_consent"),
            {"next": reverse("login")},
        )

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertIn(COOKIE_CONSENT_COOKIE, response.cookies)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_privacy_policy_is_linked_in_menu(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'href="{reverse("privacy_policy")}"')
        response = self.client.get(reverse("privacy_policy"))
        self.assertContains(response, "Datenschutzhinweise")
        self.assertContains(response, "Cookies und ähnliche Technologien")

    def test_configured_site_title_is_shown_in_navigation_and_home(self):
        self.settings.site_title = "Herbstbasar"
        self.settings.save(update_fields=["site_title"])

        response = self.client.get(reverse("home"))

        self.assertContains(response, "<title>Herbstbasar</title>")
        self.assertContains(response, "<strong>Herbstbasar</strong>")
        self.assertContains(response, "<h1>Herbstbasar</h1>")

    def test_media_files_are_served_in_debug_mode(self):
        if not settings.DEBUG:
            self.skipTest("Django stellt Mediendateien nur im Debug-Modus bereit.")

        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=media_root, suffix=".txt") as media_file:
            media_file.write(b"test media file")
            media_file.flush()
            response = self.client.get(
                f"/{settings.MEDIA_URL.lstrip('/')}{Path(media_file.name).name}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"test media file")

    def test_language_dropdown_submits_automatically_without_a_button(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'name="language" onchange="this.form.submit()"')
        self.assertNotContains(response, "Wechseln")

    def test_admin_default_language_is_used_until_user_selects_a_language(self):
        self.settings.default_language = "fr"
        self.settings.save(update_fields=["default_language"])
        self.client.cookies.pop("django_language", None)

        response = self.client.get(
            reverse("privacy_policy"),
            HTTP_ACCEPT_LANGUAGE="ja",
        )
        self.assertEqual(response.headers.get("Content-Language"), "fr")
        self.assertIn("Cookie", response.headers.get("Vary", ""))
        self.assertContains(response, "Politique de confidentialité")
        self.assertNotIn("django_language", response.cookies)

        self.client.cookies["django_language"] = "en"
        self.settings.default_language = "ja"
        self.settings.save(update_fields=["default_language"])
        response = self.client.get(
            reverse("privacy_policy"),
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        self.assertEqual(response.headers.get("Content-Language"), "en")
        self.assertContains(response, "Privacy policy")

    def test_language_switcher_keeps_current_page_in_supported_languages(self):
        for language, expected_title, expected_section in (
            ("en", "Privacy policy", "Cookies and similar technologies"),
            ("fr", "Politique de confidentialité", "Cookies et technologies similaires"),
        ):
            with self.subTest(language=language):
                response = self.client.post(
                    reverse("set_language"),
                    {"language": language, "next": reverse("privacy_policy")},
                )
                self.assertRedirects(
                    response,
                    reverse("privacy_policy"),
                    fetch_redirect_response=False,
                )
                self.assertEqual(response.cookies["django_language"].value, language)

                policy_page = self.client.get(reverse("privacy_policy"))
                self.assertContains(policy_page, expected_title)
                self.assertContains(policy_page, expected_section)
                self.assertNotContains(policy_page, "Datenschutzerklärung")
                self.assertNotContains(policy_page, "Cookies und ähnliche Technologien")

    def test_additional_language_catalogs_are_available_and_translated(self):
        for language in ("es", "pt", "ja", "zh-hans", "tr"):
            with self.subTest(language=language), translation_override(language):
                self.assertNotEqual(gettext("Verwaltung"), "Verwaltung")

                response = self.client.post(
                    reverse("set_language"),
                    {"language": language, "next": reverse("privacy_policy")},
                )
                self.assertRedirects(
                    response,
                    reverse("privacy_policy"),
                    fetch_redirect_response=False,
                )
                self.assertEqual(response.cookies["django_language"].value, language)

                policy_page = self.client.get(reverse("privacy_policy"))
                self.assertEqual(
                    policy_page.headers.get("Content-Language"),
                    language,
                )
                self.assertNotContains(policy_page, "Datenschutzerklärung")
                self.assertNotContains(
                    policy_page,
                    "Cookies und ähnliche Technologien",
                )

    def test_language_can_be_changed_on_cookie_consent_page_before_accepting(self):
        self.client.cookies.pop(COOKIE_CONSENT_COOKIE, None)

        response = self.client.post(
            reverse("set_language"),
            {"language": "fr", "next": reverse("cookie_consent")},
        )

        self.assertRedirects(
            response,
            reverse("cookie_consent"),
            fetch_redirect_response=False,
        )
        self.assertEqual(response.cookies["django_language"].value, "fr")
        consent_page = self.client.get(reverse("cookie_consent"))
        self.assertContains(consent_page, "Cookies nécessaires")
        self.assertNotContains(consent_page, "Notwendige Cookies")
        self.assertNotIn(COOKIE_CONSENT_COOKIE, self.client.cookies)

    def test_read_only_default_invoice_template_uses_the_active_language(self):
        for language, expected_title in (
            ("en", "<b>Seller settlement</b>"),
            ("fr", "<b>Relevé du vendeur</b>"),
        ):
            with self.subTest(language=language), translation_override(language):
                snapshot = _settlement_data(self.bazaar)
                self.assertIn(expected_title, snapshot["invoice_template"])

        for language in ("es", "pt", "ja", "zh-hans", "tr"):
            with self.subTest(language=language), translation_override(language):
                snapshot = _settlement_data(self.bazaar)
                self.assertNotIn(
                    "<b>Verkäuferabrechnung</b>",
                    snapshot["invoice_template"],
                )

    def test_seller_registration_assigns_number_without_repeating_account_consent(self):
        self.client.force_login(self.seller)
        response = self.client.post(
            reverse("join_bazaar", args=[self.bazaar.pk]),
            {},
        )
        registration = SellerRegistration.objects.get(bazaar=self.bazaar, seller=self.seller)

        self.assertRedirects(response, reverse("seller_dashboard"))
        self.assertEqual(registration.seller_number, "001")
        self.assertEqual(self.seller.consents.count(), 1)

    def test_seller_number_limit_is_enforced_for_new_registrations(self):
        self.bazaar.seller_number_limit = 1
        self.bazaar.save(update_fields=["seller_number_limit"])
        self.client.force_login(self.seller)
        self.client.post(reverse("join_bazaar", args=[self.bazaar.pk]), {})

        second_seller = User.objects.create_user(
            email="second@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        UserConsent.objects.create(
            user=second_seller,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(second_seller)
        response = self.client.post(reverse("join_bazaar", args=[self.bazaar.pk]), {})

        self.assertRedirects(response, reverse("seller_dashboard"))
        self.assertEqual(SellerRegistration.objects.filter(bazaar=self.bazaar).count(), 1)
        self.assertFalse(
            SellerRegistration.objects.filter(bazaar=self.bazaar, seller=second_seller).exists()
        )

    def test_registration_requires_email_verification(self):
        registration_page = self.client.get(reverse("register"))
        self.assertContains(registration_page, "Bestätigungslink anfordern")
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Mara",
                "last_name": "Muster",
                "email": "new-seller@example.com",
                "phone_number": "030123456",
                "address_street": "Musterstraße 1",
                "address_postal_code": "10115",
                "address_city": "Berlin",
                "password1": "StrongPass!234",
                "password2": "StrongPass!234",
                "consent": "on",
            },
        )
        user = User.objects.get(email="new-seller@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(user.is_active)
        consent = UserConsent.objects.get(user=user)
        self.assertEqual(consent.version, "2026-01")
        self.assertEqual(consent.text, "Datenschutzhinweise")
        self.assertIsNotNone(consent.accepted_at)
        self.assertEqual(user.first_name, "Mara")
        self.assertEqual(user.last_name, "Muster")
        self.assertEqual(user.phone_number, "030123456")
        self.assertEqual(user.address_postal_code, "10115")
        verification_path = re.search(
            r"http://testserver(?P<path>/accounts/verify/[^\s]+)",
            mail.outbox[0].body,
        ).group("path")

        response = self.client.get(verification_path)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertRedirects(
            response,
            reverse("choose_role"),
            fetch_redirect_response=False,
        )

    def test_registration_requires_names_and_validates_german_postal_code(self):
        response = self.client.post(
            reverse("register"),
            {
                "email": "incomplete@example.com",
                "address_postal_code": "1234",
                "password1": "StrongPass!234",
                "password2": "StrongPass!234",
                "consent": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("first_name", response.context["form"].errors)
        self.assertIn("last_name", response.context["form"].errors)
        self.assertIn("address_postal_code", response.context["form"].errors)
        self.assertFalse(User.objects.filter(email="incomplete@example.com").exists())

    def test_consent_html_formatting_is_rendered_safely(self):
        self.settings.consent_version = "2026-02"
        self.settings.consent_text = (
            "<h2>Datenschutz</h2>\n"
            "<p>Bitte <strong>sorgfältig lesen</strong>.</p>"
            '<img src="x" onerror="alert(1)">'
            '<script>alert(2)</script>'
            '<a href="javascript:alert(3)">Unsicherer Link</a>'
        )
        self.settings.save(update_fields=["consent_version", "consent_text"])

        registration_page = self.client.get(reverse("register"))
        self.assertContains(registration_page, "<h2>Datenschutz</h2>")
        self.assertContains(registration_page, "<strong>sorgfältig lesen</strong>")
        self.assertNotContains(registration_page, "<script>alert(2)</script>")
        self.assertNotContains(registration_page, "onerror=")
        self.assertNotContains(registration_page, "javascript:")

        self.client.force_login(self.seller)
        consent_page = self.client.get(reverse("accept_consent"))
        self.assertContains(consent_page, "<h2>Datenschutz</h2>")
        self.assertContains(consent_page, "<strong>sorgfältig lesen</strong>")
        self.assertNotContains(consent_page, "<script>alert(2)</script>")
        self.assertNotContains(consent_page, "onerror=")
        self.assertNotContains(consent_page, "javascript:")

    def test_registration_can_skip_email_verification_when_disabled(self):
        self.settings.email_confirmation_required = False
        self.settings.save(update_fields=["email_confirmation_required"])
        registration_page = self.client.get(reverse("register"))
        self.assertContains(registration_page, "Konto erstellen")
        self.assertNotContains(registration_page, "Bestätigungslink anfordern")
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Max",
                "last_name": "Mustermann",
                "email": "direct@example.com",
                "password1": "StrongPass!234",
                "password2": "StrongPass!234",
                "consent": "on",
            },
        )

        user = User.objects.get(email="direct@example.com")
        self.assertTrue(user.is_active)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("choose_role"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(UserConsent.objects.filter(user=user).exists())

    def test_user_must_accept_changed_consent_before_seller_view(self):
        self.settings.consent_version = "2026-02"
        self.settings.consent_text = "Aktualisierte Datenschutzhinweise"
        self.settings.save(update_fields=["consent_version", "consent_text"])
        self.client.force_login(self.seller)

        response = self.client.get(reverse("seller_dashboard"))
        self.assertRedirects(response, reverse("accept_consent"))
        response = self.client.post(
            reverse("accept_consent"),
            {"consent": "accepted"},
        )

        self.assertRedirects(
            response,
            reverse("choose_role"),
            fetch_redirect_response=False,
        )
        accepted = self.seller.consents.order_by("-accepted_at").first()
        self.assertEqual(accepted.version, "2026-02")
        self.assertEqual(accepted.text, "Aktualisierte Datenschutzhinweise")
        self.assertEqual(self.seller.consents.count(), 2)

    def test_user_can_edit_own_profile_from_email_link(self):
        self.client.force_login(self.seller)

        dashboard = self.client.get(reverse("seller_dashboard"))
        self.assertContains(
            dashboard,
            f'<a href="{reverse("profile_edit")}">{self.seller.email}</a>',
        )
        response = self.client.post(
            reverse("profile_edit"),
            {
                "first_name": "Mara",
                "last_name": "Muster",
                "phone_number": "030123456",
                "address_street": "Beispielweg 2",
                "address_postal_code": "10115",
                "address_city": "Berlin",
            },
        )

        self.assertRedirects(
            response,
            reverse("profile_edit"),
            fetch_redirect_response=False,
        )
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.first_name, "Mara")
        self.assertEqual(self.seller.last_name, "Muster")
        self.assertEqual(self.seller.address_city, "Berlin")

    def test_global_password_rules_can_be_configured(self):
        self.settings.password_min_length = 20
        self.settings.save(update_fields=["password_min_length"])
        with self.assertRaises(ValidationError):
            validate_password("StrongPass!234", self.seller)

        self.settings.password_min_length = 8
        self.settings.password_require_uppercase = False
        self.settings.password_require_lowercase = False
        self.settings.password_require_number = False
        self.settings.password_require_special = False
        self.settings.password_block_common = False
        self.settings.password_block_numeric_only = False
        self.settings.password_check_similarity = False
        self.settings.save()
        validate_password("lowercasepassword", self.seller)

    def test_account_deletion_anonymizes_user_without_changing_bazaar_totals(self):
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="009",
        )
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.seller,
            receipt_number=20,
            status=Receipt.Status.COMPLETE,
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("5.00"),
        )
        cancelled_item = ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("3.00"),
            cancelled_at=timezone.now(),
            cancelled_by=self.seller,
        )
        original_email = self.seller.email
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("delete_account"),
            {"confirm_delete": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Konto gelöscht")
        self.seller.refresh_from_db()
        registration.refresh_from_db()
        receipt.refresh_from_db()
        cancelled_item.refresh_from_db()
        self.assertTrue(self.seller.is_anonymized)
        self.assertFalse(self.seller.is_active)
        self.assertFalse(self.seller.has_usable_password())
        self.assertNotEqual(self.seller.email, original_email)
        self.assertEqual(self.seller.email, f"deleted-account-{self.seller.pk}@deleted.invalid")
        self.assertEqual(self.seller.first_name, "")
        self.assertEqual(self.seller.phone_number, "")
        self.assertEqual(self.seller.consents.count(), 0)
        self.assertEqual(registration.seller_id, self.seller.pk)
        self.assertEqual(registration.seller_number, "009")
        self.assertEqual(receipt.cashier_id, self.seller.pk)
        self.assertEqual(receipt.total, Decimal("5.00"))
        self.assertEqual(cancelled_item.cancelled_by_id, self.seller.pk)
        self.assertFalse(User.objects.filter(email=original_email).exists())
        self.assertRedirects(
            self.client.get(reverse("profile_edit")),
            f"{reverse('login')}?next={reverse('profile_edit')}",
        )

    def test_account_deletion_requires_confirmation(self):
        self.client.force_login(self.seller)

        response = self.client.post(reverse("delete_account"))

        self.assertRedirects(response, reverse("profile_edit"))
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.is_active)
        self.assertFalse(self.seller.is_anonymized)

    def test_last_active_superadmin_cannot_delete_own_account(self):
        admin = User.objects.create_superuser(
            email="last-admin@example.com",
            password="SecurePass123!",
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("delete_account"),
            {"confirm_delete": "on"},
        )

        self.assertRedirects(response, reverse("profile_edit"))
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)
        self.assertFalse(admin.is_anonymized)

    def test_unverified_user_can_request_a_new_verification_link(self):
        user = User.objects.create_user(
            email="waiting@example.com",
            password="SecurePass123!",
            is_active=False,
        )

        response = self.client.post(
            reverse("resend_verification"),
            {"email": user.email},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verify", mail.outbox[0].body)

    def test_seller_can_reuse_a_previous_number(self):
        previous_bazaar = Bazaar.objects.create(
            name="Herbstbasar",
            starts_at=timezone.now() - timedelta(days=100),
            seller_registration_deadline=timezone.now() - timedelta(days=101),
            status=Bazaar.Status.CLOSED,
        )
        previous = SellerRegistration.objects.create(
            bazaar=previous_bazaar,
            seller=self.seller,
            seller_number="ALT42",
        )
        self.client.force_login(self.seller)
        self.client.post(
            reverse("join_bazaar", args=[self.bazaar.pk]),
            {"reuse_registration": previous.pk},
        )

        registration = SellerRegistration.objects.get(bazaar=self.bazaar, seller=self.seller)
        self.assertEqual(registration.seller_number, "ALT42")

    def test_history_visibility_restricts_reuse_choices_and_posts(self):
        previous_bazaar = Bazaar.objects.create(
            name="Verborgener Basar",
            starts_at=timezone.now() - timedelta(days=100),
            seller_registration_deadline=timezone.now() - timedelta(days=101),
            status=Bazaar.Status.CLOSED,
            seller_can_view_history=False,
        )
        previous = SellerRegistration.objects.create(
            bazaar=previous_bazaar,
            seller=self.seller,
            seller_number="ALT42",
        )
        self.client.force_login(self.seller)

        dashboard = self.client.get(reverse("seller_dashboard"))
        response = self.client.post(
            reverse("join_bazaar", args=[self.bazaar.pk]),
            {"reuse_registration": previous.pk},
        )

        self.assertNotContains(dashboard, "ALT42")
        self.assertEqual(response.status_code, 404)

    def test_seller_sees_number_but_not_current_sales_when_hidden(self):
        self.bazaar.status = Bazaar.Status.OPEN
        self.bazaar.seller_can_view_current = False
        self.bazaar.save()
        SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="001",
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse("seller_dashboard"))

        self.assertContains(response, "001")
        self.assertNotContains(response, "Erfasster Umsatz")
        self.assertEqual(
            self.client.get(
                reverse("seller_bazaar_details", args=[self.bazaar.pk])
            ).status_code,
            404,
        )

    def test_seller_can_view_current_sales_and_calculated_payout(self):
        self.bazaar.status = Bazaar.Status.OPEN
        self.bazaar.seller_fee = Decimal("1.00")
        self.bazaar.operator_percent = Decimal("10.00")
        self.bazaar.save()
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V021",
        )
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=1,
            status=Receipt.Status.COMPLETE,
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("2.50"),
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("2.50"),
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("7.50"),
        )
        self.client.force_login(self.seller)

        dashboard = self.client.get(reverse("seller_dashboard"))
        response = self.client.get(
            reverse("seller_bazaar_details", args=[self.bazaar.pk])
        )

        self.assertContains(
            dashboard,
            reverse("seller_bazaar_details", args=[self.bazaar.pk]),
        )
        self.assertContains(response, "Derzeitige Auszahlung")
        self.assertContains(response, "12,50 €")
        self.assertContains(response, "1,25 €")
        self.assertContains(response, "10,25 €")
        self.assertContains(response, "Anzahl")

    def test_seller_payout_never_displays_a_negative_amount(self):
        self.bazaar.status = Bazaar.Status.OPEN
        self.bazaar.seller_fee = Decimal("10.00")
        self.bazaar.operator_percent = Decimal("10.00")
        self.bazaar.save()
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V023",
        )
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=1,
            status=Receipt.Status.COMPLETE,
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("1.00"),
        )
        self.client.force_login(self.seller)

        response = self.client.get(
            reverse("seller_bazaar_details", args=[self.bazaar.pk])
        )

        self.assertContains(response, "0,00 €")
        self.assertNotContains(response, "-9,10 €")

    def test_seller_sees_final_settlement_snapshot_for_finished_bazaar(self):
        self.bazaar.status = Bazaar.Status.CLOSED
        self.bazaar.seller_registration_open = False
        self.bazaar.seller_can_view_history = True
        self.bazaar.save()
        SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V022",
        )
        second_seller = User.objects.create_user(
            email="other@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=second_seller,
            seller_number="V999",
        )
        BazaarSettlement.objects.create(
            bazaar=self.bazaar,
            snapshot={
                "currency": "CHF",
                "sellers": [
                    {
                        "seller_number": "V022",
                        "lines": [{"price": "5.00", "count": 2}],
                        "gross": "10.00",
                        "seller_fee": "1.00",
                        "operator_percent": "10.00",
                        "operator_fee": "1.00",
                        "payout": "8.00",
                    },
                    {
                        "seller_number": "V999",
                        "lines": [{"price": "999.00", "count": 1}],
                        "gross": "999.00",
                        "seller_fee": "0.00",
                        "operator_percent": "0.00",
                        "operator_fee": "0.00",
                        "payout": "999.00",
                    },
                ],
            },
        )
        self.client.force_login(self.seller)

        response = self.client.get(
            reverse("seller_bazaar_details", args=[self.bazaar.pk])
        )

        self.assertContains(response, "Endgültige Abrechnung")
        self.assertContains(response, "10,00 CHF")
        self.assertContains(response, "8,00 CHF")
        self.assertNotContains(response, "V999")
        self.assertNotContains(response, "999,00")

    def test_seller_sees_own_number_while_registration_is_open(self):
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V042",
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse("seller_dashboard"))

        self.assertContains(response, "Aktuelle Verkäufernummern")
        self.assertContains(response, "V042")
        self.assertContains(
            response,
            reverse("seller_bazaar_details", args=[self.bazaar.pk]),
        )
        self.assertNotContains(response, "Erfasster Umsatz")
        self.assertNotContains(response, "Verkäufernummer anfordern")
        self.assertEqual(response.context["current_registrations"].get(), registration)

    def test_seller_without_cashier_right_cannot_open_cashier(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse("cashier"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_cashier_without_superuser_right_does_not_see_django_admin_link(self):
        self.client.force_login(self.cashier)

        response = self.client.get(reverse("seller_dashboard"))

        self.assertNotContains(response, '<a href="/admin/">')

    def test_mobile_navigation_has_an_accessible_collapse_toggle(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse("seller_dashboard"))

        self.assertContains(response, '<header id="site-header">')
        self.assertContains(response, 'id="menu-toggle"')
        self.assertContains(response, 'aria-controls="primary-menu"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'header.menu-open .menu-links')
        self.assertContains(response, '@media (max-width: 1024px)')
        self.assertContains(response, 'window.matchMedia("(max-width: 1024px)")')
        self.assertGreater(
            response.content.index("Datenschutzerklärung".encode()),
            response.content.index("Abmelden".encode()),
        )

    def test_seller_only_user_is_sent_to_the_only_view_and_sees_profile_and_logout(self):
        self.client.force_login(self.seller)

        response = self.client.get(reverse("choose_role"))

        self.assertRedirects(response, reverse("seller_dashboard"))
        response = self.client.get(reverse("seller_dashboard"))
        self.assertContains(response, "Abmelden")
        self.assertContains(response, f'href="{reverse("profile_edit")}"')
        self.assertNotContains(response, "Ansicht wechseln")
        self.assertNotContains(response, '<a href="/seller/">')
        self.assertNotContains(response, '<a href="/cashier/">')
        self.assertNotContains(response, '<a href="/role/">')
        self.assertNotContains(response, "<strong>Basar</strong>")

    def test_cashier_keeps_bazaar_selected_until_session_ends(self):
        second_bazaar = Bazaar.objects.create(
            name="Zweiter Basar",
            starts_at=timezone.now() + timedelta(days=30),
            seller_registration_deadline=timezone.now() + timedelta(days=20),
            status=Bazaar.Status.OPEN,
        )
        self.bazaar.status = Bazaar.Status.OPEN
        self.bazaar.seller_registration_open = True
        self.bazaar.save()
        self.assertFalse(Bazaar.objects.get(pk=self.bazaar.pk).seller_registration_open)
        self.client.force_login(self.cashier)

        self.client.post(reverse("cashier"), {"bazaar_id": self.bazaar.pk})
        response = self.client.post(reverse("cashier"), {"bazaar_id": second_bazaar.pk})

        self.assertRedirects(response, reverse("cashier"), fetch_redirect_response=False)
        cashier_page = self.client.get(reverse("cashier"))
        self.assertEqual(cashier_page.context["selected_bazaar"].pk, self.bazaar.pk)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_cashier_creates_sequential_receipts_and_completes_sale(self):
        self.settings.currency = GlobalSettings.Currency.USD
        self.settings.save(update_fields=["currency"])
        self.bazaar.status = Bazaar.Status.OPEN
        self.bazaar.starts_at = timezone.now() - timedelta(hours=1)
        self.bazaar.seller_registration_deadline = self.bazaar.starts_at
        self.bazaar.save()
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="001",
        )
        second_seller = User.objects.create_user(
            email="second@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        second_registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=second_seller,
            seller_number="002",
        )
        self.client.force_login(self.cashier)

        response = self.client.post(
            reverse("cashier"),
            {"bazaar_id": self.bazaar.pk},
        )
        receipt = Receipt.objects.get()
        self.assertEqual(receipt.receipt_number, 1)
        self.assertEqual(response.status_code, 302)
        cashier_page = self.client.get(
            reverse("cashier"),
            {"receipt": receipt.pk, "registration": registration.pk},
        )
        self.assertContains(cashier_page, "1.00 USD")
        self.assertContains(cashier_page, "2.50 USD")
        self.assertContains(cashier_page, "Verkäufernummer wählen")
        self.assertContains(cashier_page, 'class="cashier-layout"')
        self.assertContains(cashier_page, 'class="card receipt-panel"')
        self.assertContains(cashier_page, 'data-has-recorded-items="false"')
        self.assertContains(
            cashier_page,
            "Beleg wirklich abschließen? Danach können keine Artikel mehr korrigiert werden.",
        )

        self.assertEqual(self.bazaar.__class__.objects.get(pk=self.bazaar.pk).next_receipt_number, 2)

        self.client.post(
            reverse("add_item", args=[receipt.pk]),
            {"price": "2.50", "registration_id": registration.pk},
        )
        self.assertIsNotNone(Receipt.objects.get(pk=receipt.pk).first_item_at)
        self.client.post(
            reverse("add_item", args=[receipt.pk]),
            {"price": "3.00", "registration_id": second_registration.pk},
        )
        cashier_page = self.client.get(reverse("cashier"))
        self.assertContains(cashier_page, 'data-has-recorded-items="true"')
        self.assertContains(cashier_page, "beforeunload")
        leave_attempt = self.client.get(reverse("seller_dashboard"))
        self.assertRedirects(leave_attempt, reverse("cashier"), fetch_redirect_response=False)
        logout_attempt = self.client.post(reverse("logout"))
        self.assertRedirects(logout_attempt, reverse("cashier"), fetch_redirect_response=False)
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=registration,
            price=Decimal("1.25"),
        )
        item_to_cancel = receipt.items.filter(
            registration=registration,
            cancelled_at__isnull=True,
        ).first()
        response = self.client.post(
            reverse("cancel_item", args=[receipt.pk, item_to_cancel.pk])
        )
        self.assertEqual(response.status_code, 302)
        item_to_cancel.refresh_from_db()
        self.assertIsNotNone(item_to_cancel.cancelled_at)
        self.assertEqual(item_to_cancel.cancelled_by, self.cashier)

        response = self.client.post(reverse("finish_receipt", args=[receipt.pk]))

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, Receipt.Status.COMPLETE)
        self.assertEqual(receipt.total, Decimal("4.25"))
        self.assertRedirects(response, reverse("receipt_complete", args=[receipt.pk]))
        response = self.client.get(reverse("receipt_complete", args=[receipt.pk]))
        self.assertContains(response, "4,25 USD")
        self.assertContains(response, "001")
        self.assertContains(response, "002")
        self.assertContains(response, "3,00 USD")
        seller_client = Client()
        self.accept_cookies(seller_client)
        seller_client.force_login(self.seller)
        seller_page = seller_client.get(reverse("seller_dashboard"))
        self.assertContains(seller_page, "1,25 USD")
        response = self.client.post(reverse("next_sale"))
        self.assertEqual(response.status_code, 302)
        next_page = self.client.get(reverse("cashier"))
        next_receipt = Receipt.objects.get(status=Receipt.Status.OPEN)
        self.assertEqual(next_page.context["receipt"].pk, next_receipt.pk)
        self.assertEqual(next_page.context["selected_bazaar"].pk, self.bazaar.pk)
        self.assertContains(next_page, "Verkäufernummer wählen")
        self.assertNotContains(next_page, "Basar für diese Kasse auswählen")
        self.settings.currency = GlobalSettings.Currency.EUR
        self.settings.save(update_fields=["currency"])
        euro_cashier_page = self.client.get(
            reverse("cashier"),
            {"registration": registration.pk},
        )
        self.assertContains(euro_cashier_page, "1.00 €")

    def test_settlement_groups_prices_and_calculates_fees(self):
        self.seller.first_name = "Anna"
        self.seller.last_name = "Muster"
        self.seller.save(update_fields=["first_name", "last_name"])
        self.settings.currency = GlobalSettings.Currency.CHF
        self.settings.save(update_fields=["currency"])
        self.bazaar.seller_fee = Decimal("1.00")
        self.bazaar.operator_percent = Decimal("10.00")
        self.bazaar.save()
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V001",
        )
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=1,
            status=Receipt.Status.COMPLETE,
        )
        ReceiptItem.objects.create(receipt=receipt, registration=registration, price=Decimal("2.50"))
        ReceiptItem.objects.create(receipt=receipt, registration=registration, price=Decimal("2.50"))
        ReceiptItem.objects.create(receipt=receipt, registration=registration, price=Decimal("4.00"))
        second_seller = User.objects.create_user(
            email="second@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        second_registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=second_seller,
            seller_number="V002",
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            registration=second_registration,
            price=Decimal("5.00"),
        )
        third_seller = User.objects.create_user(
            email="third@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        third_registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=third_seller,
            seller_number="V003",
        )

        open_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=2,
            first_item_at=timezone.now(),
        )
        ReceiptItem.objects.create(
            receipt=open_receipt,
            registration=registration,
            price=Decimal("7.00"),
        )
        snapshot = _settlement_data(self.bazaar)
        first_seller_data, second_seller_data, third_seller_data = snapshot["sellers"]

        self.assertEqual(snapshot["currency"], "CHF")
        self.assertIn("{{ verkaeufernummer }}", snapshot["invoice_template"])
        self.assertIn("{{ vorname }} {{ name }}", snapshot["invoice_template"])
        self.assertIn("{{ summen }}", snapshot["invoice_template"])
        self.assertEqual(first_seller_data["vorname"], "Anna")
        self.assertEqual(first_seller_data["name"], "Muster")
        self.assertTrue(_build_pdf(self.bazaar, snapshot).startswith(b"%PDF"))
        self.assertEqual(first_seller_data["gross"], "9.00")
        self.assertEqual(first_seller_data["operator_fee"], "0.90")
        self.assertEqual(first_seller_data["payout"], "7.10")
        self.assertEqual(
            {line["price"]: line["count"] for line in first_seller_data["lines"]},
            {"2.50": 2, "4.00": 1},
        )
        self.assertEqual(second_seller_data["seller_number"], "V002")
        self.assertEqual(second_seller_data["gross"], "5.00")
        self.assertEqual(third_seller_data["payout"], "0.00")
        self.assertEqual(snapshot["overall"]["gross"], "14.00")
        self.assertEqual(snapshot["overall"]["seller_fees"], "3.00")
        self.assertEqual(snapshot["overall"]["operator_fees"], "1.40")
        self.assertEqual(snapshot["overall"]["payouts"], "10.60")
        self.assertEqual(
            {line["price"]: line["count"] for line in snapshot["overall"]["lines"]},
            {"2.50": 2, "4.00": 1, "5.00": 1},
        )
        self.assertEqual(len(snapshot["open_receipts"]), 1)
        self.assertEqual(snapshot["open_receipts"][0]["receipt_number"], 2)
        self.assertEqual(snapshot["open_receipts"][0]["seller_totals"][0]["total"], "7.00")
        self.assertIn(":", snapshot["open_receipts"][0]["first_item_at"])

    def test_seller_invoice_template_can_be_managed_and_selected_per_bazaar(self):
        admin = User.objects.create_superuser(
            email="template-admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse("manage_seller_invoice_template_create"),
            {
                "name": "Kompakt",
                "body": "Abrechnung {{ basar_name }}<br>Nummer {{ verkaeufernummer }}",
            },
        )

        invoice_template = SellerInvoiceTemplate.objects.get(name="Kompakt")
        self.assertRedirects(response, reverse("manage_seller_invoice_templates"))
        self.bazaar.invoice_template = invoice_template
        self.bazaar.save(update_fields=["invoice_template"])
        SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V001",
        )
        snapshot = _settlement_data(self.bazaar)
        self.assertEqual(snapshot["invoice_template"], invoice_template.body)
        self.assertTrue(_build_pdf(self.bazaar, snapshot).startswith(b"%PDF"))
        edit_page = self.client.get(reverse("manage_bazaar_edit", args=[self.bazaar.pk]))
        self.assertContains(edit_page, 'selected')
        self.assertContains(edit_page, "Kompakt")
        self.assertEqual(
            self.client.post(
                reverse("delete_seller_invoice_template", args=[invoice_template.pk])
            ).status_code,
            302,
        )
        self.assertTrue(SellerInvoiceTemplate.objects.filter(pk=invoice_template.pk).exists())

    def test_standard_invoice_template_is_read_only_copyable_and_default_selectable(self):
        admin = User.objects.create_superuser(
            email="standard-template-admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)
        standard = self.settings.default_seller_invoice_template
        self.assertTrue(standard.is_read_only)
        self.assertEqual(
            self.client.get(
                reverse("manage_seller_invoice_template_edit", args=[standard.pk])
            ).status_code,
            404,
        )
        response = self.client.post(
            reverse("manage_seller_invoice_templates"),
            {"copy_template": standard.pk, "template": standard.pk},
        )
        copied = SellerInvoiceTemplate.objects.get(name="Standard (Kopie)")

        self.assertRedirects(
            response,
            reverse("manage_seller_invoice_template_edit", args=[copied.pk]),
            fetch_redirect_response=False,
        )
        self.assertFalse(copied.is_read_only)
        self.assertEqual(copied.body, standard.body)
        listing = self.client.get(reverse("manage_seller_invoice_templates"))
        self.assertContains(listing, f'value="{standard.pk}" aria-label="Als Standardvorlage')
        response = self.client.post(
            reverse("manage_seller_invoice_templates"),
            {"template": copied.pk, "save_default": "1"},
        )

        self.assertRedirects(
            response,
            reverse("manage_seller_invoice_templates"),
            fetch_redirect_response=False,
        )
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.default_seller_invoice_template_id, copied.pk)
        bazaar_form = self.client.get(reverse("manage_bazaar_create")).context["form"]
        self.assertEqual(str(bazaar_form["invoice_template"].value()), str(copied.pk))

    def test_staff_can_generate_settlement_pdf_only_after_bazaar_closes(self):
        registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller,
            seller_number="V001",
        )
        open_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=1,
            first_item_at=timezone.now(),
        )
        ReceiptItem.objects.create(
            receipt=open_receipt,
            registration=registration,
            price=Decimal("9.00"),
        )
        completed_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.cashier,
            receipt_number=2,
            status=Receipt.Status.COMPLETE,
        )
        ReceiptItem.objects.create(
            receipt=completed_receipt,
            registration=registration,
            price=Decimal("2.00"),
        )
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)

        def management_data(status):
            return {
                "name": self.bazaar.name,
                "starts_at": timezone.localtime(self.bazaar.starts_at).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "seller_registration_deadline": timezone.localtime(
                    self.bazaar.seller_registration_deadline
                ).strftime("%Y-%m-%dT%H:%M"),
                "status": status,
                "seller_registration_open": "on",
                "seller_number_width": self.bazaar.seller_number_width,
                "seller_number_start": self.bazaar.seller_number_start,
                "seller_number_limit": self.bazaar.seller_number_limit,
                "seller_fee": self.bazaar.seller_fee,
                "operator_percent": self.bazaar.operator_percent,
                "seller_can_view_current": "on",
                "seller_can_view_history": "on",
                "invoice_template": self.settings.default_seller_invoice_template.pk,
            }

        response = self.client.post(
            reverse("manage_bazaar_edit", args=[self.bazaar.pk]),
            management_data(Bazaar.Status.CLOSED),
        )
        self.assertRedirects(
            response,
            reverse("manage_bazaar_edit", args=[self.bazaar.pk]),
            fetch_redirect_response=False,
        )
        self.bazaar.refresh_from_db()
        self.assertEqual(self.bazaar.status, Bazaar.Status.CLOSED)
        settlement_page = self.client.get(
            reverse("settlement_start", args=[self.bazaar.pk])
        )
        self.assertEqual(settlement_page.status_code, 200)
        self.assertContains(settlement_page, "Gesamtabrechnung")
        self.assertContains(settlement_page, "2,00 €")

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("settlement_pdf", args=[self.bazaar.pk])
                )
                self.assertRedirects(
                    response,
                    reverse("settlement_download", args=[self.bazaar.pk]),
                    fetch_redirect_response=False,
                )
                response = self.client.get(
                    reverse("settlement_download", args=[self.bazaar.pk])
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        settlement = BazaarSettlement.objects.get(bazaar=self.bazaar)
        self.assertEqual(settlement.snapshot["currency"], "EUR")
        self.assertEqual(settlement.snapshot["open_receipts"][0]["total"], "9.00")
        self.assertEqual(settlement.snapshot["sellers"][0]["gross"], "2.00")
        self.assertEqual(settlement.snapshot["overall"]["gross"], "2.00")
        delete_response = self.client.get(
            f"/admin/core/bazaarsettlement/{settlement.pk}/delete/"
        )
        self.assertEqual(delete_response.status_code, 403)

        settled_bazaar = Bazaar.objects.get(pk=self.bazaar.pk)
        settled_bazaar.status = Bazaar.Status.OPEN
        with self.assertRaises(ValidationError):
            settled_bazaar.save()
        settled_bazaar.refresh_from_db()
        self.assertEqual(settled_bazaar.status, Bazaar.Status.CLOSED)

        edit_page = self.client.get(
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk])
        )
        self.assertContains(edit_page, "Der Basar ist abgerechnet und kann nicht wieder geöffnet werden.")
        self.assertContains(edit_page, "Abrechnung öffnen")
        response = self.client.post(
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk]),
            management_data(Bazaar.Status.OPEN),
        )
        self.assertRedirects(
            response,
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk]),
            fetch_redirect_response=False,
        )
        settled_bazaar.refresh_from_db()
        self.assertEqual(settled_bazaar.status, Bazaar.Status.CLOSED)
        Bazaar.objects.filter(pk=settled_bazaar.pk).update(
            status=Bazaar.Status.OPEN,
            seller_registration_open=True,
            settlement_created_at=None,
        )
        edit_page = self.client.get(
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk])
        )
        self.assertContains(edit_page, "Abrechnung öffnen")
        settlement_page = self.client.get(
            reverse("settlement_start", args=[settled_bazaar.pk])
        )
        self.assertEqual(
            settlement_page.status_code,
            200,
            settlement_page.get("Location"),
        )
        settled_bazaar.refresh_from_db()
        self.assertEqual(settled_bazaar.status, Bazaar.Status.CLOSED)
        self.assertFalse(settled_bazaar.seller_registration_open)
        self.assertIsNotNone(settled_bazaar.settlement_created_at)
        self.assertContains(settlement_page, "Abrechnung stornieren")

        self.client.force_login(self.cashier)
        response = self.client.post(
            reverse("settlement_cancel", args=[settled_bazaar.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            BazaarSettlement.objects.get(pk=settlement.pk).cancelled_at
        )

        self.client.force_login(admin)
        response = self.client.post(
            reverse("settlement_cancel", args=[settled_bazaar.pk])
        )
        self.assertRedirects(
            response,
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk]),
            fetch_redirect_response=False,
        )
        settlement.refresh_from_db()
        self.assertIsNotNone(settlement.cancelled_at)
        self.assertEqual(settlement.cancelled_by, admin)
        settled_bazaar.refresh_from_db()
        self.assertFalse(settled_bazaar.is_settled)
        history_page = self.client.get(
            reverse("settlement_start", args=[settled_bazaar.pk])
        )
        self.assertContains(history_page, "Abrechnungshistorie")
        self.assertContains(history_page, "Storniert")
        self.assertContains(history_page, admin.email)
        response = self.client.post(
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk]),
            management_data(Bazaar.Status.OPEN),
        )
        self.assertRedirects(
            response,
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk]),
            fetch_redirect_response=False,
        )
        settled_bazaar.refresh_from_db()
        self.assertEqual(settled_bazaar.status, Bazaar.Status.OPEN)
        edit_page = self.client.get(
            reverse("manage_bazaar_edit", args=[settled_bazaar.pk])
        )
        self.assertContains(edit_page, "Abrechnung öffnen")
        history_page = self.client.get(
            reverse("settlement_start", args=[settled_bazaar.pk])
        )
        self.assertContains(history_page, "Abrechnungshistorie")
        self.assertContains(history_page, "Storniert")
        self.assertNotContains(
            history_page,
            "Abrechnung erstellen und PDF herunterladen",
        )

    def test_past_bazaar_can_be_settled_even_if_its_status_was_not_closed(self):
        past_starts_at = timezone.now() - timedelta(days=3)
        bazaar = Bazaar.objects.create(
            name="Vergangener Basar",
            starts_at=past_starts_at,
            seller_registration_deadline=past_starts_at - timedelta(days=2),
            status=Bazaar.Status.PLANNED,
        )
        admin = User.objects.create_superuser(
            email="past-admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)

        listing = self.client.get(reverse("manage_bazaars"))
        self.assertContains(listing, "Abrechnung")
        self.assertContains(listing, "Statistiken")
        self.assertEqual(
            self.client.get(reverse("settlement_start", args=[bazaar.pk])).status_code,
            200,
        )
        bazaar.refresh_from_db()
        self.assertEqual(bazaar.status, Bazaar.Status.CLOSED)
        self.assertFalse(bazaar.seller_registration_open)

    def test_admin_can_create_cashier_account(self):
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)

        add_user_page = self.client.get("/admin/core/user/add/")
        self.assertContains(add_user_page, "Datenschutzvereinbarung")
        self.assertContains(add_user_page, "Datenschutzhinweise")

        response = self.client.post(
            "/admin/core/user/add/",
            {
                "email": "new-cashier@example.com",
                "first_name": "Karla",
                "last_name": "Kasse",
                "phone_number": "030987654",
                "address_street": "Marktstraße 5",
                "address_postal_code": "20095",
                "address_city": "Hamburg",
                "password1": "StrongPass!234",
                "password2": "StrongPass!234",
                "consent": "on",
                "is_cashier": "on",
                "is_staff": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        cashier = User.objects.get(email="new-cashier@example.com")
        self.assertFalse(cashier.is_active)
        self.assertTrue(cashier.is_cashier)
        self.assertEqual(cashier.first_name, "Karla")
        self.assertEqual(cashier.last_name, "Kasse")
        self.assertEqual(cashier.address_city, "Hamburg")
        consent = UserConsent.objects.get(user=cashier)
        self.assertEqual(consent.text, self.settings.consent_text)
        self.assertIsNotNone(consent.accepted_at)
        verification_path = re.search(
            r"http://testserver(?P<path>/accounts/verify/[^\s]+)",
            mail.outbox[0].body,
        ).group("path")
        response = self.client.get(verification_path)
        cashier.refresh_from_db()
        self.assertTrue(cashier.is_active)
        self.assertRedirects(response, reverse("choose_role"))

    def test_bazaar_and_global_settings_are_managed_outside_django_admin(self):
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)

        admin_index = self.client.get(reverse("admin:index"))
        self.assertNotContains(admin_index, "Basare")
        self.assertNotContains(admin_index, "Globale Einstellungen")
        self.assertEqual(self.client.get("/admin/core/bazaar/").status_code, 404)
        self.assertEqual(self.client.get("/admin/core/globalsettings/").status_code, 404)
        self.assertEqual(self.client.get(reverse("management_home")).status_code, 200)

        response = self.client.post(
            reverse("manage_settings"),
            {
                "common_prices": '["1.00", "2.50", "5.00"]',
                "site_title": "Frühlingsbasar",
                "currency": GlobalSettings.Currency.USD,
                "design_theme": GlobalSettings.DesignTheme.DARK,
                "menu_position": GlobalSettings.MenuPosition.LEFT,
                "menu_fixed": "on",
                "default_language": "fr",
                "consent_version": "2026-01",
                "consent_text": "Datenschutzhinweise",
                "email_confirmation_required": "on",
                "password_min_length": "12",
                "password_require_uppercase": "on",
                "password_require_lowercase": "on",
                "password_require_number": "on",
                "password_require_special": "on",
                "password_block_common": "on",
                "password_block_numeric_only": "on",
                "password_check_similarity": "on",
                "default_seller_fee": "2.00",
                "default_operator_percent": "12.50",
                "default_seller_number_limit": "750",
                "poster_prompt_instructions": "Gut lesbar, keine erfundenen Angaben.",
            },
        )
        self.assertRedirects(response, reverse("manage_settings"))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.default_seller_fee, Decimal("2.00"))
        self.assertEqual(self.settings.default_operator_percent, Decimal("12.50"))
        self.assertEqual(self.settings.default_seller_number_limit, 750)
        self.assertEqual(self.settings.site_title, "Frühlingsbasar")
        self.assertEqual(self.settings.currency, GlobalSettings.Currency.USD)
        self.assertEqual(self.settings.menu_position, GlobalSettings.MenuPosition.LEFT)
        self.assertTrue(self.settings.menu_fixed)
        self.assertEqual(self.settings.default_language, "fr")
        self.settings.site_title = "Basar"
        self.settings.default_language = "de"
        self.settings.save(update_fields=["site_title", "default_language"])
        settings_page = self.client.get(reverse("manage_settings"))
        self.assertContains(settings_page, '<option value="tr">Türkçe</option>')
        for _, label in GlobalSettings.MenuPosition.choices:
            self.assertContains(settings_page, label)
        for _, label in GlobalSettings.Currency.choices:
            self.assertContains(settings_page, label)
        self.assertContains(settings_page, "Menü beim Scrollen fixieren")
        for theme, label in GlobalSettings.DesignTheme.choices:
            settings_page = self.client.get(reverse("manage_settings"))
            self.assertContains(settings_page, label)
            self.settings.design_theme = theme
            self.settings.save(update_fields=["design_theme"])
            themed_page = self.client.get(reverse("management_home"))
            self.assertContains(themed_page, f'data-theme="{theme}"')
            self.assertContains(themed_page, 'data-menu-position="left"')
            self.assertContains(themed_page, 'data-menu-fixed="true"')
            self.assertContains(themed_page, "body { min-height: 100vh;")
            if theme == GlobalSettings.DesignTheme.DARK:
                self.assertContains(
                    themed_page,
                    '<html lang="de" data-theme="dark">',
                )

        now = timezone.localtime()
        starts_at = now + timedelta(days=30)
        deadline = starts_at - timedelta(days=5)
        response = self.client.get(reverse("manage_bazaar_create"))
        self.assertContains(response, 'value="2.00"')
        self.assertContains(response, 'value="12.50"')
        self.assertContains(response, 'value="750"')
        response = self.client.post(
            reverse("manage_bazaar_create"),
            {
                "name": "Sommerbasar",
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "seller_registration_deadline": deadline.strftime("%Y-%m-%dT%H:%M"),
                "status": Bazaar.Status.PLANNED,
                "seller_registration_open": "on",
                "seller_number_width": "3",
                "seller_number_start": "1",
                "seller_number_limit": "700",
                "seller_fee": "3.25",
                "operator_percent": "10.00",
                "seller_can_view_current": "on",
                "seller_can_view_history": "on",
                "invoice_template": self.settings.default_seller_invoice_template.pk,
            },
        )

        self.assertRedirects(response, reverse("manage_bazaar_edit", args=[2]))
        bazaar = Bazaar.objects.get(name="Sommerbasar")
        self.assertEqual(bazaar.seller_fee, Decimal("3.25"))
        self.assertEqual(bazaar.operator_percent, Decimal("10.00"))
        self.assertEqual(
            bazaar.seller_registration_deadline,
            deadline.replace(second=0, microsecond=0),
        )
        self.assertIn("Gut lesbar, keine erfundenen Angaben.", bazaar.poster_prompt)
        self.assertIn("Sommerbasar", bazaar.poster_prompt)
        self.assertIn("Nummernvergabe möglich bis", bazaar.poster_prompt)
        edit_page = self.client.get(reverse("manage_bazaar_edit", args=[bazaar.pk]))
        # Django-Admin Link ist nur bei DEBUG=True sichtbar
        # assertContains prüft das Template generiert wurde, auch wenn Admin-Link conditional ist
        self.assertContains(edit_page, f'value="{starts_at.strftime("%Y-%m-%dT%H:%M")}"')
        self.assertContains(edit_page, f'value="{deadline.strftime("%Y-%m-%dT%H:%M")}"')

    def test_global_logo_upload_is_used_for_seller_invoice_pdf(self):
        admin = User.objects.create_superuser(
            email="logo-admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)
        image_buffer = BytesIO()
        PillowImage.new("RGB", (4, 4), color="white").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        data = {
            "common_prices": '["1.00"]',
            "site_title": "Basar",
            "currency": GlobalSettings.Currency.EUR,
            "design_theme": GlobalSettings.DesignTheme.CLASSIC,
            "menu_position": GlobalSettings.MenuPosition.TOP,
            "default_language": "de",
            "consent_version": self.settings.consent_version,
            "consent_text": self.settings.consent_text,
            "default_seller_fee": "0.00",
            "default_operator_percent": "0.00",
            "default_seller_number_limit": "1000",
            "poster_prompt_instructions": "Keine zusätzlichen Angaben erfinden.",
            "password_min_length": "12",
            "logo": SimpleUploadedFile(
                "logo.png",
                image_buffer.getvalue(),
                content_type="image/png",
            ),
        }

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(reverse("manage_settings"), data)
                self.assertRedirects(response, reverse("manage_settings"))
                self.settings.refresh_from_db()
                self.assertTrue(self.settings.logo)
                self.assertTrue(self.settings.logo.storage.exists(self.settings.logo.name))
                registration = SellerRegistration.objects.create(
                    bazaar=self.bazaar,
                    seller=self.seller,
                    seller_number="V001",
                )
                snapshot = _settlement_data(self.bazaar)
                self.assertTrue(_build_pdf(self.bazaar, snapshot).startswith(b"%PDF"))

    def test_global_settings_can_be_saved_without_consent_text(self):
        self.settings.consent_text = ""
        self.settings.save(update_fields=["consent_text"])
        admin = User.objects.create_superuser(
            email="settings-admin@example.com",
            password="SecurePass123!",
        )
        self.client.force_login(admin)

        data = {
            "common_prices": "[]",
            "site_title": "Basar",
            "currency": GlobalSettings.Currency.CHF,
            "design_theme": GlobalSettings.DesignTheme.CLASSIC,
            "menu_position": GlobalSettings.MenuPosition.TOP,
            "default_language": "de",
            "consent_version": self.settings.consent_version,
            "consent_text": "",
            "default_seller_fee": "0.00",
            "default_operator_percent": "0.00",
            "default_seller_number_limit": "1000",
            "poster_prompt_instructions": "Keine zusätzlichen Angaben erfinden.",
            "password_min_length": "12",
        }
        response = self.client.post(reverse("manage_settings"), data)

        self.assertRedirects(response, reverse("manage_settings"))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.currency, GlobalSettings.Currency.CHF)
        self.assertEqual(self.settings.consent_text, "")

        data["consent_text"] = "Neue Einwilligung"
        response = self.client.post(reverse("manage_settings"), data)
        self.assertRedirects(
            response,
            reverse("manage_settings"),
            fetch_redirect_response=False,
        )
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.consent_text, "Neue Einwilligung")

    def test_number_deadline_must_not_be_after_bazaar(self):
        admin = User.objects.create_superuser(
            email="admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=admin,
            version=self.settings.consent_version,
            text=self.settings.consent_text,
            accepted_at=timezone.now(),
        )
        self.client.force_login(admin)
        start = timezone.localtime(timezone.now() + timedelta(days=30))
        too_late = start + timedelta(days=1)

        response = self.client.post(
            reverse("manage_bazaar_create"),
            {
                "name": "Ungültiger Basar",
                "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
                "seller_registration_deadline": too_late.strftime("%Y-%m-%dT%H:%M"),
                "status": Bazaar.Status.PLANNED,
                "seller_registration_open": "on",
                "seller_number_width": "3",
                "seller_number_start": "1",
                "seller_number_limit": "1000",
                "seller_fee": "0.00",
                "operator_percent": "0.00",
                "seller_can_view_current": "on",
                "seller_can_view_history": "on",
                "invoice_template": self.settings.default_seller_invoice_template.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Die Nummernvergabe darf nicht nach dem Beginn des Basars enden.")
        self.assertFalse(Bazaar.objects.filter(name="Ungültiger Basar").exists())

    def test_seller_number_registration_is_rejected_after_deadline(self):
        self.bazaar.seller_registration_deadline = timezone.now() - timedelta(seconds=1)
        self.bazaar.save()
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse("join_bazaar", args=[self.bazaar.pk]),
            {},
        )

        self.assertRedirects(response, reverse("seller_dashboard"))
        self.assertFalse(
            SellerRegistration.objects.filter(bazaar=self.bazaar, seller=self.seller).exists()
        )


class BazaarStatisticsTests(TestCase):
    def setUp(self):
        BazaarWorkflowTests.accept_cookies(self.client)
        self.settings = GlobalSettings.load()
        self.settings.consent_version = "stats-1"
        self.settings.consent_text = "Statistiktest"
        self.settings.save()
        self.admin = User.objects.create_superuser(
            email="statistics-admin@example.com",
            password="SecurePass123!",
        )
        self.seller_one = User.objects.create_user(
            email="stats-seller-one@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        self.seller_two = User.objects.create_user(
            email="stats-seller-two@example.com",
            password="SecurePass123!",
            is_active=True,
        )
        for user in (self.admin, self.seller_one, self.seller_two):
            UserConsent.objects.create(
                user=user,
                version=self.settings.consent_version,
                text=self.settings.consent_text,
                accepted_at=timezone.now(),
            )
        now = timezone.now()
        self.bazaar = Bazaar.objects.create(
            name="Statistikbasar",
            starts_at=now - timedelta(days=1),
            seller_registration_deadline=now - timedelta(days=2),
            status=Bazaar.Status.CLOSED,
            seller_number_width=3,
            seller_number_start=1,
        )
        self.registration_one = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller_one,
            seller_number="001",
        )
        self.registration_two = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller=self.seller_two,
            seller_number="002",
        )
        self.registration_one.registered_at = now - timedelta(days=2)
        self.registration_one.save(update_fields=["registered_at"])
        self.registration_two.registered_at = now
        self.registration_two.save(update_fields=["registered_at"])

        first_sale = now - timedelta(hours=6)
        later_sale = first_sale + timedelta(hours=2)
        self.receipt_one = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
            status=Receipt.Status.COMPLETE,
            completed_at=first_sale,
        )
        self.receipt_two = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=2,
            status=Receipt.Status.COMPLETE,
            completed_at=later_sale,
        )
        ReceiptItem.objects.create(
            receipt=self.receipt_one,
            registration=self.registration_one,
            price=Decimal("10.00"),
        )
        ReceiptItem.objects.create(
            receipt=self.receipt_one,
            registration=self.registration_one,
            price=Decimal("5.00"),
        )
        ReceiptItem.objects.create(
            receipt=self.receipt_two,
            registration=self.registration_one,
            price=Decimal("7.00"),
        )
        ReceiptItem.objects.create(
            receipt=self.receipt_two,
            registration=self.registration_two,
            price=Decimal("12.00"),
        )
        ReceiptItem.objects.create(
            receipt=self.receipt_two,
            registration=self.registration_two,
            price=Decimal("100.00"),
            cancelled_at=now,
            cancelled_by=self.admin,
        )
        self.open_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=3,
            status=Receipt.Status.OPEN,
        )
        ReceiptItem.objects.create(
            receipt=self.open_receipt,
            registration=self.registration_two,
            price=Decimal("3.00"),
        )
        self.client.force_login(self.admin)

    def test_global_statistics_aggregate_completed_sales_and_prices(self):
        response = self.client.get(reverse("management_statistics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sales_chart"][0]["value"], 4)
        self.assertEqual(response.context["revenue_chart"][0]["value"], Decimal("34.00"))
        self.assertEqual(
            [(row["price"], row["item_count"]) for row in response.context["price_chart"]],
            [
                (Decimal("5.00"), 1),
                (Decimal("7.00"), 1),
                (Decimal("10.00"), 1),
                (Decimal("12.00"), 1),
            ],
        )
        self.assertContains(response, "Verkaufsentwicklung je Basar")
        self.assertContains(response, "Artikelverteilung nach Preis")
        self.assertNotContains(response, "Weitere sinnvolle Kennzahlen")

    def test_bazaar_statistics_show_five_minute_sales_and_top_receipts(self):
        response = self.client.get(reverse("bazaar_statistics", args=[self.bazaar.pk]))

        self.assertEqual(response.status_code, 200)
        sales_series = response.context["sales_series"]
        self.assertEqual(len(sales_series), 25)
        self.assertEqual(sales_series[0]["item_count"], 2)
        self.assertEqual(sales_series[0]["revenue"], Decimal("15.00"))
        self.assertEqual(sales_series[1]["item_count"], 0)
        self.assertEqual(sales_series[-1]["item_count"], 2)
        self.assertEqual(sales_series[-1]["revenue"], Decimal("19.00"))
        self.assertEqual(
            [tick["label"] for tick in response.context["sales_chart"]["y_ticks"]],
            ["0", "1", "2"],
        )
        for tick in response.context["sales_chart"]["y_ticks"]:
            self.assertContains(response, f'>{tick["label"]}</text>')
        self.assertEqual(
            response.context["top_receipts"][0].pk,
            self.receipt_two.pk,
        )
        self.assertEqual(
            response.context["top_receipts"][0].amount,
            Decimal("19.00"),
        )
        self.assertEqual(
            response.context["top_sellers"][0]["registration__seller_number"],
            "001",
        )
        self.assertEqual(response.context["top_sellers"][0]["revenue"], Decimal("22.00"))
        self.assertEqual(
            response.context["registration_total_chart"]["points"][-1]["total"],
            2,
        )
        self.assertEqual(response.context["completed_receipt_count"], 2)
        self.assertEqual(response.context["average_receipt"], Decimal("17.00"))
        self.assertEqual(response.context["median_receipt"], Decimal("17.00"))
        self.assertEqual(response.context["cancelled_item_count"], 1)
        self.assertEqual(response.context["total_item_count"], 6)
        self.assertEqual(response.context["cancellation_rate"], Decimal("16.7"))
        self.assertEqual(response.context["peak_periods"][0]["receipt_count"], 1)
        self.assertEqual(response.context["peak_periods"][0]["revenue"], Decimal("19.00"))
        self.assertEqual(response.context["cashier_revenue"][0]["item_count"], 4)
        self.assertEqual(response.context["cashier_revenue"][0]["revenue"], Decimal("34.00"))
        self.assertEqual(response.context["receipt_page"].paginator.count, 3)
        self.assertContains(response, "Rechnungen")
        self.assertContains(response, "Offen")
        self.assertContains(response, "Top-Rechnungen nach Betrag")
        self.assertContains(response, "Durchschnitt, Median und Stornoquote")
        self.assertContains(response, "Stoßzeiten")
        self.assertContains(response, "Umsatz je Kassierer")
        self.assertContains(response, 'class="line-chart-line"')
        self.assertContains(response, "Y-Achse: Artikelanzahl")
        self.assertContains(response, "Y-Achse: Umsatz (€)")
        self.assertContains(response, 'class="line-chart-axis-value"')
        self.assertContains(response, ".line-chart-point:hover")
        self.assertContains(response, 'data-tooltip="')
        self.assertContains(response, 'point.addEventListener("pointerenter"')

    def test_sales_within_the_same_five_minute_bucket_are_combined(self):
        extra_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=4,
            status=Receipt.Status.COMPLETE,
            completed_at=self.receipt_one.completed_at.replace(
                minute=(self.receipt_one.completed_at.minute // 5) * 5,
                second=0,
                microsecond=0,
            )
            + timedelta(minutes=4),
        )
        ReceiptItem.objects.create(
            receipt=extra_receipt,
            registration=self.registration_one,
            price=Decimal("2.00"),
        )

        response = self.client.get(reverse("bazaar_statistics", args=[self.bazaar.pk]))
        first_bucket = response.context["sales_series"][0]

        self.assertEqual(first_bucket["item_count"], 3)
        self.assertEqual(first_bucket["revenue"], Decimal("17.00"))

    def test_receipt_details_include_cancelled_lines_and_live_open_amounts(self):
        response = self.client.get(
            reverse(
                "bazaar_receipt_detail",
                args=[self.bazaar.pk, self.receipt_two.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Storniert")
        self.assertContains(response, "100,00")
        self.assertContains(response, "19,00")

        open_response = self.client.get(
            reverse(
                "bazaar_receipt_detail",
                args=[self.bazaar.pk, self.open_receipt.pk],
            )
        )
        self.assertEqual(open_response.status_code, 200)
        self.assertContains(open_response, "Zwischenstand")
        self.assertContains(open_response, "3,00")

    def test_only_administrators_can_view_statistics(self):
        self.client.force_login(self.seller_one)

        response = self.client.get(reverse("management_statistics"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class ScannerTests(TestCase):
    """Tests für QR-Code Scanner Funktionalität"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@example.com",
            password="SecurePass123!",
        )
        UserConsent.objects.create(
            user=self.admin,
            version=GlobalSettings.load().consent_version,
            text=GlobalSettings.load().consent_text,
            accepted_at=timezone.now(),
        )

        self.bazaar = Bazaar.objects.create(
            name="Test Basar",
            starts_at=timezone.now() + timedelta(hours=1),
            seller_registration_deadline=timezone.now() - timedelta(hours=1),
            status="open",
            seller_fee=Decimal("2.50"),
            operator_percent=Decimal("10.00"),
        )

        self.registration = SellerRegistration.objects.create(
            bazaar=self.bazaar,
            seller_number="001",
            seller=self.admin,
        )

    def test_scanner_code_generation(self):
        """Kassierer kann Scanner-Code generieren"""
        self.admin.is_cashier = True
        self.admin.save()
        self.client.force_login(self.admin)
        
        # Basar in Session setzen (wie nach /cashier/ POST)
        session = self.client.session
        session["cashier_bazaar_id"] = self.bazaar.pk
        session.save()

        response = self.client.get(reverse("scanner_generate"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("code", data)
        self.assertIn("qr_url", data)
        self.assertIn("qr_base64", data)
        self.assertTrue(len(data["code"]) >= 50)

        # Session sollte in DB existieren
        session_obj = ScannerSession.objects.get(code=data["code"])
        self.assertEqual(session_obj.bazaar, self.bazaar)
        self.assertEqual(session_obj.user, self.admin)

    def test_scanner_code_expires_after_30_minutes(self):
        """Scanner-Session läuft nach 30 Minuten ab"""
        session = ScannerSession.objects.create(
            code="test-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        self.assertTrue(session.is_expired)

    def test_scanner_join_with_valid_code(self):
        """Handy kann sich mit gültigem Code anmelden"""
        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="valid-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        response = self.client.get(reverse("scanner_join") + "?code=valid-code")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.bazaar.name)
        self.assertContains(response, "Scanner")
        self.assertContains(response, 'id="scanner-flash"')
        self.assertContains(response, 'id="manual-registration"')
        self.assertContains(response, 'id="manual-price"')
        self.assertContains(response, self.registration.seller_number)
        self.assertContains(response, "scanCooldownUntil")
        self.assertContains(response, "playConfirmationTone")

    def test_scanner_join_with_invalid_code(self):
        """Handy wird abgelehnt bei ungültigem Code"""
        response = self.client.get(reverse("scanner_join") + "?code=invalid")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nicht gefunden")

    def test_scanner_join_with_expired_code(self):
        """Handy wird abgelehnt bei abgelaufenem Code"""
        expires_at = timezone.now() - timedelta(minutes=1)
        session = ScannerSession.objects.create(
            code="expired-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        response = self.client.get(reverse("scanner_join") + "?code=expired-code")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "abgelaufen")

    def test_scanner_submit_creates_submission(self):
        """Handy kann Artikel einreichen"""
        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="submit-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        response = self.client.post(
            reverse("scanner_submit"),
            {
                "code": "submit-code",
                "qr_data": f"VKNr={self.registration.id}&price=2.50",
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Submission sollte in DB existieren
        submission = ScannerSubmission.objects.get(session=session)
        self.assertEqual(submission.registration_id, self.registration.id)
        self.assertEqual(submission.price, Decimal("2.50"))

    def test_scanner_accepts_repeated_identical_scans(self):
        """Gleiche Preisschilder dürfen mehrfach nacheinander erfasst werden."""
        ScannerSession.objects.create(
            code="repeated-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        scan_data = {
            "code": "repeated-code",
            "qr_data": f"VKNr={self.registration.id}&price=2.50",
        }

        first_response = self.client.post(reverse("scanner_submit"), scan_data)
        second_response = self.client.post(reverse("scanner_submit"), scan_data)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            ScannerSubmission.objects.filter(session__code="repeated-code").count(),
            2,
        )

    def test_scanner_submit_with_invalid_qr_format(self):
        """Handy wird abgelehnt bei ungültigem QR-Format"""
        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="submit-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        response = self.client.post(
            reverse("scanner_submit"),
            {
                "code": "submit-code",
                "qr_data": "invalid-format",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_scanner_pending_returns_unprocessed_submissions(self):
        """Kasse ruft unverarbeitete Submissions ab"""
        self.admin.is_cashier = True
        self.admin.save()
        self.client.force_login(self.admin)

        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="pending-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        # Zwei Submissions erstellen
        ScannerSubmission.objects.create(
            session=session,
            registration_id=self.registration.id,
            price=Decimal("2.50"),
        )
        ScannerSubmission.objects.create(
            session=session,
            registration_id=self.registration.id,
            price=Decimal("3.00"),
        )

        response = self.client.get(
            reverse("scanner_pending") + "?code=pending-code"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["submissions"]), 2)

        # Der Abruf allein darf die Submissions nicht als verarbeitet markieren.
        submissions = ScannerSubmission.objects.filter(session=session)
        for sub in submissions:
            self.assertFalse(sub.processed)

    def test_adding_scanned_item_marks_submission_processed(self):
        """Ein Scan wird erst mit dem erfolgreichen Kassen-Eintrag verarbeitet."""
        response = HttpResponse()
        response.set_signed_cookie(
            COOKIE_CONSENT_COOKIE,
            COOKIE_CONSENT_VALUE,
            salt=COOKIE_CONSENT_SALT,
        )
        self.client.cookies.update(response.cookies)
        self.admin.is_cashier = True
        self.admin.save(update_fields=["is_cashier"])
        self.client.force_login(self.admin)
        session = self.client.session
        session["cashier_bazaar_id"] = self.bazaar.pk
        session.save()
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
        )
        scanner_session = ScannerSession.objects.create(
            code="add-scan-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        submission = ScannerSubmission.objects.create(
            session=scanner_session,
            receipt=receipt,
            registration_id=self.registration.pk,
            price=Decimal("2.50"),
        )

        response = self.client.post(
            reverse("add_item", args=[receipt.pk]),
            {
                "scanner_submission_id": submission.pk,
                "registration_id": self.registration.pk,
                "price": "2.50",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReceiptItem.objects.filter(receipt=receipt).count(), 1)
        submission.refresh_from_db()
        self.assertTrue(submission.processed)

    def test_scanner_pending_is_not_redirected_for_open_receipt(self):
        """Polling bleibt JSON, auch wenn der aktuelle Beleg Artikel enthält."""
        self.admin.is_cashier = True
        self.admin.save(update_fields=["is_cashier"])
        self.client.force_login(self.admin)
        browser_session = self.client.session
        browser_session["cashier_bazaar_id"] = self.bazaar.pk
        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
            first_item_at=timezone.now(),
        )
        browser_session["cashier_receipt_id"] = receipt.pk
        browser_session.save()
        scanner_session = ScannerSession.objects.create(
            code="pending-open-receipt-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.get(
            reverse("scanner_pending") + f"?code={scanner_session.code}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"submissions": []})

    def test_scanner_submissions_do_not_move_to_next_receipt(self):
        """Ein Scan bleibt beim Beleg aktiv, der beim Scan geöffnet war."""
        self.admin.is_cashier = True
        self.admin.save(update_fields=["is_cashier"])
        self.client.force_login(self.admin)
        browser_session = self.client.session
        browser_session["cashier_bazaar_id"] = self.bazaar.pk

        first_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
        )
        browser_session["cashier_receipt_id"] = first_receipt.pk
        browser_session.save()
        scanner_session = ScannerSession.objects.create(
            code="receipt-bound-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.post(
            reverse("scanner_submit"),
            {
                "code": scanner_session.code,
                "qr_data": f"VKNr={self.registration.id}&price=2.50",
            },
        )

        self.assertEqual(response.status_code, 200)
        submission = ScannerSubmission.objects.get(session=scanner_session)
        self.assertEqual(submission.receipt, first_receipt)

        first_receipt.status = Receipt.Status.COMPLETE
        first_receipt.save(update_fields=["status"])
        next_receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=2,
        )
        browser_session = self.client.session
        browser_session["cashier_receipt_id"] = next_receipt.pk
        browser_session.save()

        response = self.client.get(
            reverse("scanner_pending") + f"?code={scanner_session.code}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["submissions"], [])

    def test_completing_receipt_keeps_scanner_session_active(self):
        """Scanner bleibt für den nächsten Beleg derselben Kasse verbunden."""
        response = HttpResponse()
        response.set_signed_cookie(
            COOKIE_CONSENT_COOKIE,
            COOKIE_CONSENT_VALUE,
            salt=COOKIE_CONSENT_SALT,
        )
        self.client.cookies.update(response.cookies)
        self.admin.is_cashier = True
        self.admin.save(update_fields=["is_cashier"])
        self.client.force_login(self.admin)
        session = self.client.session
        session["cashier_bazaar_id"] = self.bazaar.pk
        session.save()

        receipt = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
            first_item_at=timezone.now(),
        )
        scanner_session = ScannerSession.objects.create(
            code="persistent-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.post(reverse("finish_receipt", args=[receipt.pk]))

        self.assertRedirects(response, reverse("receipt_complete", args=[receipt.pk]))
        scanner_session.refresh_from_db()
        self.assertTrue(scanner_session.is_active)

    def test_completing_receipt_does_not_clear_scanner_browser_state(self):
        """Der Abschluss eines Belegs darf den Scanner im Browser nicht zurücksetzen."""
        response = HttpResponse()
        response.set_signed_cookie(
            COOKIE_CONSENT_COOKIE,
            COOKIE_CONSENT_VALUE,
            salt=COOKIE_CONSENT_SALT,
        )
        self.client.cookies.update(response.cookies)
        self.admin.is_cashier = True
        self.admin.save(update_fields=["is_cashier"])
        self.client.force_login(self.admin)
        session = self.client.session
        session["cashier_bazaar_id"] = self.bazaar.pk
        session["cashier_receipt_id"] = Receipt.objects.create(
            bazaar=self.bazaar,
            cashier=self.admin,
            receipt_number=1,
        ).pk
        session.save()

        response = self.client.get(reverse("cashier"))

        self.assertNotContains(response, "&& resetScanner();")

    def test_scanner_close_deactivates_session(self):
        """Kassierer kann Scanner-Session schließen"""
        self.admin.is_cashier = True
        self.admin.save()
        self.client.force_login(self.admin)

        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="close-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
            is_active=True,
        )

        response = self.client.post(
            reverse("scanner_close"),
            {"code": "close-code"},
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertFalse(session.is_active)

    def test_scanner_rate_limiting(self):
        """Rate-Limiting für Scanner-Submissions"""
        expires_at = timezone.now() + timedelta(minutes=30)
        session = ScannerSession.objects.create(
            code="ratelimit-code",
            bazaar=self.bazaar,
            user=self.admin,
            expires_at=expires_at,
        )

        # 100 Submissions in letzter Minute erstellen
        for i in range(100):
            ScannerSubmission.objects.create(
                session=session,
                registration_id=self.registration.id,
                price=Decimal("2.50"),
                submitted_at=timezone.now() - timedelta(seconds=30),
            )

        # 101. Submission sollte abgelehnt werden
        response = self.client.post(
            reverse("scanner_submit"),
            {
                "code": "ratelimit-code",
                "qr_data": f"VKNr={self.registration.id}&price=2.50",
            },
        )

        self.assertEqual(response.status_code, 429)
