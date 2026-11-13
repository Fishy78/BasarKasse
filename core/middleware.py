from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.middleware.locale import LocaleMiddleware
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext as _

from .models import GlobalSettings, Receipt


COOKIE_CONSENT_COOKIE = "basar_cookie_consent"
COOKIE_CONSENT_SALT = "basar.cookie-consent.v1"
COOKIE_CONSENT_VALUE = "accepted"


class AdminDefaultLocaleMiddleware(LocaleMiddleware):
    def process_request(self, request):
        language = translation.get_language_from_path(request.path_info)
        if language is None:
            language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language is None:
            language = GlobalSettings.load().default_language

        try:
            language = translation.get_supported_language_variant(
                language,
                strict=True,
            )
        except LookupError:
            try:
                language = translation.get_supported_language_variant(
                    GlobalSettings.load().default_language,
                    strict=True,
                )
            except LookupError:
                language = settings.LANGUAGE_CODE

        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()

    def process_response(self, request, response):
        response = super().process_response(request, response)
        patch_vary_headers(response, ("Cookie",))
        return response


class CookieConsentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        allowed_paths = (
            "/cookies/consent/",
            "/datenschutz/",
            "/static/",
            "/media/",
            "/i18n/",
            "/api/scanner/",
            "/scanner/",
        )
        if not request.path_info.startswith(allowed_paths):
            consent = request.get_signed_cookie(
                COOKIE_CONSENT_COOKIE,
                default="",
                salt=COOKIE_CONSENT_SALT,
            )
            if consent != COOKIE_CONSENT_VALUE:
                consent_url = reverse("cookie_consent")
                query = urlencode({"next": request.get_full_path()})
                return redirect(f"{consent_url}?{query}")
        return self.get_response(request)


class ConsentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        allowed_paths = (
            "/accounts/consent/",
            "/accounts/profile/",
            "/accounts/delete/",
            "/accounts/logout/",
            "/i18n/",
            "/static/",
            "/media/",
        )
        if (
            user.is_authenticated
            and not request.path_info.startswith(allowed_paths)
            and not self.has_current_consent(user)
        ):
            return redirect("accept_consent")
        return self.get_response(request)

    @staticmethod
    def has_current_consent(user):
        current = GlobalSettings.load()
        if not current.consent_text.strip():
            return True
        latest = user.consents.order_by("-accepted_at").first()
        return bool(
            latest
            and latest.version == current.consent_version
            and latest.text == current.consent_text
        )


class CashierReceiptMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        receipt_id = request.session.get("cashier_receipt_id")
        allowed_paths = (
            "/cashier/",
            "/scanner/",
            "/api/scanner/",
            "/accounts/consent/",
        )
        if (
            user.is_authenticated
            and receipt_id
            and (user.is_cashier or user.is_superuser)
            and not request.path_info.startswith(allowed_paths)
            and Receipt.objects.filter(
                pk=receipt_id,
                cashier=user,
                status=Receipt.Status.OPEN,
                first_item_at__isnull=False,
            ).exists()
        ):
            messages.warning(
                request,
                _(
                    "Schließe zuerst den offenen Kassenbeleg ab oder korrigiere seine Artikel."
                ),
            )
            return redirect("cashier")
        return self.get_response(request)
