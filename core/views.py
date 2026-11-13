from collections import defaultdict
from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal
from decimal import ROUND_HALF_UP
from io import BytesIO
import re

import bleach
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core import signing
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMinute
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import date_format, number_format
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .emailing import CONSENT_SALT, send_verification_email
from .forms import (
    BazaarManagementForm,
    DefaultSellerInvoiceTemplateForm,
    GlobalSettingsForm,
    ProfileForm,
    ReceiptItemForm,
    RegistrationForm,
    SellerInvoiceTemplateForm,
    UserManagementForm,
    UserSearchForm,
)
from .invoice_templates import DEFAULT_SELLER_INVOICE_TEMPLATE, render_invoice_template
from .middleware import COOKIE_CONSENT_COOKIE, COOKIE_CONSENT_SALT, COOKIE_CONSENT_VALUE
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

User = get_user_model()
CONSENT_MAX_AGE = 24 * 60 * 60


def _format_amount(value):
    return number_format(value, decimal_pos=2)


def home(request):
    if request.user.is_authenticated:
        return redirect("choose_role")
    return render(request, "home.html")


def privacy_policy(request):
    return render(
        request,
        "privacy_policy.html",
        {"global_settings": GlobalSettings.load()},
    )


def cookie_consent(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if request.method == "POST":
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = reverse("home")
        response = redirect(next_url)
        response.set_signed_cookie(
            COOKIE_CONSENT_COOKIE,
            COOKIE_CONSENT_VALUE,
            salt=COOKIE_CONSENT_SALT,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            secure=django_settings.SESSION_COOKIE_SECURE,
            samesite="Lax",
            path="/",
        )
        return response
    return render(request, "cookie_consent.html", {"next_url": next_url})


def register(request):
    if request.user.is_authenticated:
        return redirect("choose_role")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        global_settings = form.global_settings
        if not global_settings.consent_text.strip():
            messages.error(request, _("Die Datenschutzhinweise sind noch nicht eingerichtet."))
            return render(request, "registration/register.html", {"form": form})
        user = form.save()
        UserConsent.objects.create(
            user=user,
            version=global_settings.consent_version,
            text=global_settings.consent_text,
            accepted_at=timezone.now(),
        )
        if global_settings.email_confirmation_required:
            send_verification_email(request, user)
            return render(request, "registration/registration_sent.html", {"email": user.email})
        user.is_active = True
        user.save(update_fields=["is_active"])
        login(request, user)
        messages.success(request, _("Dein Konto wurde erstellt."))
        return redirect("choose_role")
    return render(request, "registration/register.html", {"form": form})


@require_POST
def resend_verification(request):
    """
    Verifizierungs-E-Mail erneut versenden mit Rate-Limiting zum Schutz vor E-Mail-Spam.
    Rate-Limit: max. 3 Versuche pro E-Mail-Adresse pro Stunde.
    """
    email = request.POST.get("email", "").strip().lower()
    
    # Rate-Limiting: Cache-Schlüssel für diese E-Mail
    rate_limit_key = f"resend_verification:{email}"
    attempt_count = cache.get(rate_limit_key, 0)
    
    # Maximale Versuche pro Stunde
    MAX_ATTEMPTS_PER_HOUR = 3
    
    if attempt_count >= MAX_ATTEMPTS_PER_HOUR:
        messages.warning(
            request,
            _("Zu viele Verifizierungsanfragen. Bitte versuche es in einer Stunde erneut.")
        )
        return render(
            request,
            "registration/registration_sent.html",
            {"email": email, "resent": True},
        )
    
    user = User.objects.filter(email__iexact=email, is_active=False, is_superuser=False).first()
    if user:
        send_verification_email(request, user)
        # Zähler erhöhen und auf 1 Stunde setzen
        cache.set(rate_limit_key, attempt_count + 1, timeout=3600)
    
    return render(
        request,
        "registration/registration_sent.html",
        {"email": email, "resent": True},
    )


def verify_email(request, user_id, token):
    user = get_object_or_404(User, pk=user_id, is_active=False)
    try:
        signed_user_id = signing.loads(token, salt=CONSENT_SALT, max_age=CONSENT_MAX_AGE)
    except signing.SignatureExpired:
        return render(
            request,
            "registration/registration_sent.html",
            {"email": user.email, "expired": True},
        )
    except signing.BadSignature:
        raise Http404
    if signed_user_id != user.pk:
        raise Http404
    user.is_active = True
    user.save(update_fields=["is_active"])
    login(request, user)
    messages.success(request, _("Dein Konto wurde bestätigt."))
    return redirect("choose_role")


def _needs_consent(user):
    current = GlobalSettings.load()
    if not current.consent_text.strip():
        return False
    latest = user.consents.order_by("-accepted_at").first()
    return not latest or latest.version != current.consent_version or latest.text != current.consent_text


@login_required
def choose_role(request):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    can_cashier = request.user.is_superuser or request.user.is_cashier
    if not can_cashier:
        request.session["active_role"] = "seller"
        return redirect("seller_dashboard")
    if request.method == "POST":
        role = request.POST.get("role")
        if role == "seller":
            request.session["active_role"] = "seller"
            return redirect("seller_dashboard")
        if role == "cashier" and can_cashier:
            request.session["active_role"] = "cashier"
            return redirect("cashier")
        messages.error(request, _("Für diese Ansicht hast du keine Berechtigung."))
    return render(request, "role.html", {"can_cashier": can_cashier})


@login_required
def accept_consent(request):
    global_settings = GlobalSettings.load()
    if not global_settings.consent_text.strip():
        messages.error(request, _("Die Datenschutzhinweise sind noch nicht eingerichtet."))
        return redirect("home")
    if request.method == "POST" and request.POST.get("consent") == "accepted":
        UserConsent.objects.create(
            user=request.user,
            version=global_settings.consent_version,
            text=global_settings.consent_text,
            accepted_at=timezone.now(),
        )
        messages.success(request, _("Deine Zustimmung wurde gespeichert."))
        return redirect("choose_role")
    return render(request, "registration/accept_consent.html", {"settings": global_settings})


@login_required
def profile_edit(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Deine Benutzerdaten wurden gespeichert."))
        return redirect("profile_edit")
    return render(request, "registration/profile_edit.html", {"form": form})


@login_required
@require_POST
def delete_account(request):
    if request.POST.get("confirm_delete") != "on":
        messages.error(request, _("Bitte bestätige zuerst die Löschung deines Kontos."))
        return redirect("profile_edit")

    with transaction.atomic():
        active_superusers = []
        if request.user.is_superuser:
            active_superusers = list(
                User.objects.select_for_update()
                .filter(is_superuser=True, is_active=True)
                .order_by("pk")
            )
            user = next(
                (admin for admin in active_superusers if admin.pk == request.user.pk),
                None,
            )
            if user is None:
                user = User.objects.select_for_update().get(pk=request.user.pk)
        else:
            user = User.objects.select_for_update().get(pk=request.user.pk)
        if user.is_superuser and not any(
            admin.pk != user.pk for admin in active_superusers
        ):
            messages.error(
                request,
                _(
                    "Der letzte aktive Superadmin kann nicht gelöscht werden. "
                    "Richte zuerst einen weiteren Superadmin ein."
                ),
            )
            return redirect("profile_edit")

        user.consents.all().delete()
        user.groups.clear()
        user.user_permissions.clear()
        user.email = f"deleted-account-{user.pk}@deleted.invalid"
        user.first_name = ""
        user.last_name = ""
        user.phone_number = ""
        user.address_street = ""
        user.address_postal_code = ""
        user.address_city = ""
        user.last_login = None
        user.is_active = False
        user.is_cashier = False
        user.is_staff = False
        user.is_superuser = False
        user.is_anonymized = True
        user.set_unusable_password()
        user.save(
            update_fields=[
                "email",
                "first_name",
                "last_name",
                "phone_number",
                "address_street",
                "address_postal_code",
                "address_city",
                "last_login",
                "is_active",
                "is_cashier",
                "is_staff",
                "is_superuser",
                "is_anonymized",
                "password",
            ]
        )

    logout(request)
    return render(request, "registration/account_deleted.html")


def cashier_required(view):
    def check(user):
        return user.is_authenticated and (user.is_superuser or user.is_cashier)

    return login_required(user_passes_test(check)(view))


def management_required(view):
    return login_required(user_passes_test(lambda user: user.is_superuser)(view))


def settlement_access_required(view):
    """Decorator für Zugriff auf Settlement-Abrechnungen."""
    def check(user):
        if user.is_superuser:
            return True
        # Prüfe explizite Settlement-Berechtigung
        return user.has_perm('core.view_settlements')
    
    return login_required(user_passes_test(check)(view))


@management_required
def management_home(request):
    return render(
        request,
        "management/home.html",
        {"bazaars": Bazaar.objects.all()},
    )


def _statistics_bars(rows, value_key):
    maximum = max((row[value_key] for row in rows), default=0)
    return [
        {
            **row,
            "bar_width": (
                f"{float(row[value_key] / maximum * 100):.1f}" if maximum else "0"
            ),
        }
        for row in rows
    ]


def _statistics_line_chart(rows, value_key, display_key=None):
    if not rows:
        return {"points": [], "x_ticks": [], "y_ticks": []}

    left, right, top, bottom = 70, 970, 30, 220
    values = [Decimal(str(row[value_key])) for row in rows]
    maximum = max(values, default=Decimal("0"))
    scale = maximum or Decimal("1")
    points = []
    for index, row in enumerate(rows):
        x = (
            left + (right - left) * index / (len(rows) - 1)
            if len(rows) > 1
            else (left + right) / 2
        )
        value = Decimal(str(row[value_key]))
        y = bottom - float(value / scale) * (bottom - top)
        points.append(
            {
                **row,
                "x": f"{x:.1f}",
                "y": f"{y:.1f}",
                "display_value": (
                    row[display_key] if display_key else str(row[value_key])
                ),
            }
        )

    tick_step = max(1, (len(points) - 1 + 6) // 7)
    tick_indices = list(range(0, len(points), tick_step))
    if tick_indices[-1] != len(points) - 1:
        tick_indices.append(len(points) - 1)
    x_ticks = [
        {"x": points[index]["x"], "label": points[index]["label"]}
        for index in tick_indices
    ]

    integer_values = all(isinstance(row[value_key], int) for row in rows)
    tick_unit = Decimal("1") if integer_values else Decimal("0.01")
    maximum_units = int(maximum / tick_unit)
    tick_count = min(5, maximum_units + 1)
    tick_units = (
        [0]
        if tick_count == 1
        else [
            round(maximum_units * index / (tick_count - 1))
            for index in range(tick_count)
        ]
    )
    y_ticks = []
    for units in tick_units:
        value = tick_unit * units
        label = str(int(value)) if integer_values else _format_amount(value)
        y = bottom - float(value / scale) * (bottom - top)
        y_ticks.append(
            {
                "y": f"{y:.1f}",
                "label_y": f"{y + 4:.1f}",
                "label": label,
            }
        )
    return {"points": points, "x_ticks": x_ticks, "y_ticks": y_ticks}


def _receipts_with_amount(receipts):
    return receipts.annotate(
        amount=Sum(
            "items__price",
            filter=Q(items__cancelled_at__isnull=True),
            default=Decimal("0.00"),
        )
    )


@management_required
def management_statistics(request):
    global_settings = GlobalSettings.load()
    valid_item_filter = Q(
        receipts__status=Receipt.Status.COMPLETE,
        receipts__items__cancelled_at__isnull=True,
    )
    bazaars = list(
        Bazaar.objects.annotate(
            item_count=Count("receipts__items", filter=valid_item_filter),
            revenue=Sum("receipts__items__price", filter=valid_item_filter),
        ).order_by("starts_at", "name")
    )
    for bazaar in bazaars:
        bazaar.revenue = bazaar.revenue or Decimal("0.00")

    price_distribution = list(
        ReceiptItem.objects.filter(
            receipt__status=Receipt.Status.COMPLETE,
            cancelled_at__isnull=True,
        )
        .values("price")
        .annotate(item_count=Count("pk"))
        .order_by("price")
    )
    return render(
        request,
        "management/statistics.html",
        {
            "bazaars": bazaars,
            "sales_chart": _statistics_bars(
                [
                    {
                        "label": bazaar.name,
                        "value": bazaar.item_count,
                        "bazaar_id": bazaar.pk,
                    }
                    for bazaar in bazaars
                ],
                "value",
            ),
            "revenue_chart": _statistics_bars(
                [
                    {
                        "label": bazaar.name,
                        "value": bazaar.revenue,
                        "bazaar_id": bazaar.pk,
                    }
                    for bazaar in bazaars
                ],
                "value",
            ),
            "price_chart": _statistics_bars(
                [
                    {
                        "label": (
                            f'{_format_amount(row["price"])} '
                            f'{global_settings.currency_display}'
                        ),
                        "value": row["item_count"],
                        "price": row["price"],
                        "item_count": row["item_count"],
                    }
                    for row in price_distribution
                ],
                "value",
            ),
            "currency": global_settings.currency_display,
        },
    )


@management_required
def bazaar_statistics(request, bazaar_id):
    bazaar = get_object_or_404(Bazaar, pk=bazaar_id)
    global_settings = GlobalSettings.load()
    minute_rows = (
        bazaar.receipts.filter(
            status=Receipt.Status.COMPLETE,
            completed_at__isnull=False,
        )
        .annotate(
            period=TruncMinute(
                "completed_at",
                tzinfo=datetime_timezone.utc,
            )
        )
        .values("period")
        .annotate(
            receipt_count=Count("pk", distinct=True),
            item_count=Count(
                "items__pk",
                filter=Q(items__cancelled_at__isnull=True),
            ),
            revenue=Sum(
                "items__price",
                filter=Q(items__cancelled_at__isnull=True),
                default=Decimal("0.00"),
            ),
        )
        .order_by("period")
    )
    five_minute_totals = defaultdict(
        lambda: {
            "receipt_count": 0,
            "item_count": 0,
            "revenue": Decimal("0.00"),
        }
    )
    for row in minute_rows:
        minute = row["period"]
        period = minute.replace(
            minute=(minute.minute // 5) * 5,
            second=0,
            microsecond=0,
        )
        five_minute_totals[period]["receipt_count"] += row["receipt_count"]
        five_minute_totals[period]["item_count"] += row["item_count"]
        five_minute_totals[period]["revenue"] += row["revenue"] or Decimal("0.00")

    sales_series = []
    if five_minute_totals:
        period = min(five_minute_totals)
        last_period = max(five_minute_totals)
        while period <= last_period:
            row = five_minute_totals.get(
                period,
                {
                    "receipt_count": 0,
                    "item_count": 0,
                    "revenue": Decimal("0.00"),
                },
            )
            local_period = timezone.localtime(period)
            label = date_format(local_period, "SHORT_DATETIME_FORMAT")
            revenue = row.get("revenue") or Decimal("0.00")
            sales_series.append(
                {
                    "label": label,
                    "period": period,
                    "receipt_count": row.get("receipt_count", 0),
                    "item_count": row.get("item_count", 0),
                    "revenue": revenue,
                    "item_display": str(row.get("item_count", 0)),
                    "revenue_display": (
                        f"{_format_amount(revenue)} {global_settings.currency_display}"
                    ),
                }
            )
            period += timedelta(minutes=5)

    peak_periods = sorted(
        (row for row in sales_series if row["receipt_count"]),
        key=lambda row: (
            -row["receipt_count"],
            -row["revenue"],
            row["period"],
        ),
    )[:5]

    completed_receipts = _receipts_with_amount(
        bazaar.receipts.filter(status=Receipt.Status.COMPLETE)
    )
    receipt_amounts = list(completed_receipts.values_list("amount", flat=True))
    average_receipt = None
    median_receipt = None
    if receipt_amounts:
        receipt_amounts.sort()
        average_receipt = (
            sum(receipt_amounts, Decimal("0.00")) / len(receipt_amounts)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        middle = len(receipt_amounts) // 2
        if len(receipt_amounts) % 2:
            median_value = receipt_amounts[middle]
        else:
            median_value = (
                receipt_amounts[middle - 1] + receipt_amounts[middle]
            ) / Decimal("2")
        median_receipt = median_value.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    cashier_revenue = list(
        bazaar.receipts.filter(status=Receipt.Status.COMPLETE)
        .values(
            "cashier__first_name",
            "cashier__last_name",
            "cashier__email",
        )
        .annotate(
            receipt_count=Count("pk", distinct=True),
            item_count=Count(
                "items__pk",
                filter=Q(items__cancelled_at__isnull=True),
            ),
            revenue=Sum(
                "items__price",
                filter=Q(items__cancelled_at__isnull=True),
                default=Decimal("0.00"),
            ),
        )
        .order_by("-revenue", "-receipt_count", "cashier__last_name")
    )
    for row in cashier_revenue:
        row["cashier_name"] = (
            f'{row["cashier__first_name"]} {row["cashier__last_name"]}'.strip()
            or row["cashier__email"]
        )

    item_counts = ReceiptItem.objects.filter(receipt__bazaar=bazaar).aggregate(
        total=Count("pk"),
        cancelled=Count("pk", filter=Q(cancelled_at__isnull=False)),
    )
    cancellation_rate = None
    if item_counts["total"]:
        cancellation_rate = (
            Decimal(item_counts["cancelled"])
            * Decimal("100")
            / item_counts["total"]
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    registrations = bazaar.registrations.filter(registered_at__isnull=False)
    registration_by_day = {
        row["period"]: row["count"]
        for row in registrations.annotate(
            period=TruncDate("registered_at")
        ).values("period").annotate(count=Count("pk"))
    }
    registration_chart = []
    if registration_by_day:
        day = min(registration_by_day)
        last_day = max(registration_by_day)
        cumulative = 0
        while day <= last_day:
            new_registrations = registration_by_day.get(day, 0)
            cumulative += new_registrations
            registration_chart.append(
                {
                    "label": date_format(day, "SHORT_DATE_FORMAT"),
                    "new": new_registrations,
                    "total": cumulative,
                }
            )
            day += timedelta(days=1)

    top_sellers = list(
        ReceiptItem.objects.filter(
            receipt__bazaar=bazaar,
            receipt__status=Receipt.Status.COMPLETE,
            cancelled_at__isnull=True,
        )
        .values("registration__seller_number")
        .annotate(item_count=Count("pk"), revenue=Sum("price"))
        .order_by("-revenue", "-item_count", "registration__seller_number")[:10]
    )
    all_receipts = _receipts_with_amount(
        bazaar.receipts.all()
    ).order_by("-created_at", "-receipt_number")
    receipt_page = Paginator(all_receipts, 50).get_page(request.GET.get("page"))
    for receipt in receipt_page.object_list:
        receipt.display_time = receipt.completed_at or receipt.created_at
    top_receipts = _receipts_with_amount(
        bazaar.receipts.filter(status=Receipt.Status.COMPLETE)
    ).order_by("-amount", "-created_at", "-receipt_number")[:10]

    return render(
        request,
        "management/bazaar_statistics.html",
        {
            "bazaar": bazaar,
            "currency": global_settings.currency_display,
            "sales_chart": _statistics_line_chart(
                sales_series, "item_count", "item_display"
            ),
            "revenue_chart": _statistics_line_chart(
                sales_series, "revenue", "revenue_display"
            ),
            "sales_series": sales_series,
            "peak_periods": peak_periods,
            "completed_receipt_count": len(receipt_amounts),
            "average_receipt": average_receipt,
            "median_receipt": median_receipt,
            "cashier_revenue": cashier_revenue,
            "total_item_count": item_counts["total"],
            "cancelled_item_count": item_counts["cancelled"],
            "cancellation_rate": cancellation_rate,
            "registration_new_chart": _statistics_line_chart(
                registration_chart, "new"
            ),
            "registration_total_chart": _statistics_line_chart(
                registration_chart, "total"
            ),
            "registrations_with_time": registrations.count(),
            "registrations_without_time": bazaar.registrations.filter(
                registered_at__isnull=True
            ).count(),
            "top_sellers": top_sellers,
            "top_receipts": top_receipts,
            "receipt_page": receipt_page,
            "has_completed_sales": any(row["item_count"] for row in sales_series),
            "has_registration_history": bool(registration_chart),
        },
    )


@management_required
def bazaar_receipt_detail(request, bazaar_id, receipt_id):
    bazaar = get_object_or_404(Bazaar, pk=bazaar_id)
    receipt = get_object_or_404(
        _receipts_with_amount(
            Receipt.objects.select_related("cashier").filter(bazaar=bazaar)
        ),
        pk=receipt_id,
    )
    items = list(
        receipt.items.select_related("registration").order_by("created_at", "pk")
    )
    return render(
        request,
        "management/bazaar_receipt_detail.html",
        {
            "bazaar": bazaar,
            "receipt": receipt,
            "receipt_time": receipt.completed_at or receipt.created_at,
            "items": items,
            "currency": GlobalSettings.load().currency_display,
        },
    )


@management_required
def manage_settings(request):
    settings = GlobalSettings.load()
    form = GlobalSettingsForm(request.POST or None, request.FILES or None, instance=settings)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Systemeinstellungen wurden gespeichert."))
        return redirect("manage_settings")
    return render(request, "management/settings.html", {"form": form})


@management_required
def manage_seller_invoice_templates(request):
    settings = GlobalSettings.load()
    templates = settings.seller_invoice_templates.annotate(
        bazaar_count=Count("bazaars")
    )
    if request.method == "POST":
        if "copy_template" in request.POST:
            source = get_object_or_404(templates, pk=request.POST["copy_template"])
            base_name = f"{source.name[:90]} ({_('Kopie')})"
            name = base_name
            suffix = 2
            while settings.seller_invoice_templates.filter(name=name).exists():
                extra = f" ({_('Kopie')} {suffix})"
                name = f"{source.name[:100 - len(extra)]}{extra}"
                suffix += 1
            invoice_template = SellerInvoiceTemplate.objects.create(
                global_settings=settings,
                name=name,
                body=source.body,
            )
            messages.success(request, _("Kopie wurde angelegt und kann bearbeitet werden."))
            return redirect(
                "manage_seller_invoice_template_edit",
                template_id=invoice_template.pk,
            )
        if "edit_template" in request.POST:
            return redirect(
                "manage_seller_invoice_template_edit",
                template_id=request.POST["edit_template"],
            )
        if "delete_template" in request.POST:
            invoice_template = get_object_or_404(
                settings.seller_invoice_templates,
                pk=request.POST["delete_template"],
            )
            _delete_seller_invoice_template(request, settings, invoice_template)
            return redirect("manage_seller_invoice_templates")

    default_form = DefaultSellerInvoiceTemplateForm(
        request.POST or None,
        templates=templates,
        initial={"template": settings.default_seller_invoice_template_id},
    )
    if request.method == "POST" and default_form.is_valid():
        settings.default_seller_invoice_template = default_form.cleaned_data["template"]
        settings.save(update_fields=["default_seller_invoice_template"])
        messages.success(request, _("Standardvorlage für neue Basare wurde gespeichert."))
        return redirect("manage_seller_invoice_templates")
    return render(
        request,
        "management/invoice_templates.html",
        {
            "templates": templates,
            "default_form": default_form,
            "default_template_id": settings.default_seller_invoice_template_id,
        },
    )


@management_required
def manage_seller_invoice_template(request, template_id=None):
    settings = GlobalSettings.load()
    invoice_template = (
        get_object_or_404(
            settings.seller_invoice_templates,
            pk=template_id,
            is_read_only=False,
        )
        if template_id
        else SellerInvoiceTemplate(global_settings=settings)
    )
    form = SellerInvoiceTemplateForm(request.POST or None, instance=invoice_template)
    if request.method == "POST" and form.is_valid():
        invoice_template = form.save(commit=False)
        invoice_template.global_settings = settings
        invoice_template.save()
        messages.success(request, _("Verkäuferrechnungsvorlage wurde gespeichert."))
        return redirect("manage_seller_invoice_templates")
    return render(
        request,
        "management/invoice_template_form.html",
        {"form": form, "invoice_template": invoice_template},
    )


@management_required
@require_POST
def delete_seller_invoice_template(request, template_id):
    settings = GlobalSettings.load()
    invoice_template = get_object_or_404(
        settings.seller_invoice_templates,
        pk=template_id,
    )
    _delete_seller_invoice_template(request, settings, invoice_template)
    return redirect("manage_seller_invoice_templates")


def _delete_seller_invoice_template(request, settings, invoice_template):
    if invoice_template.is_read_only:
        messages.error(request, _("Die schreibgeschützte Standardvorlage kann nicht gelöscht werden."))
    elif invoice_template.pk == settings.default_seller_invoice_template_id:
        messages.error(
            request,
            _("Bitte wähle zuerst eine andere Standardvorlage für neue Basare."),
        )
    elif settings.seller_invoice_templates.count() == 1:
        messages.error(request, _("Mindestens eine Verkäuferrechnungsvorlage muss vorhanden sein."))
    else:
        try:
            invoice_template.delete()
            messages.success(request, _("Verkäuferrechnungsvorlage wurde gelöscht."))
        except ProtectedError:
            messages.error(
                request,
                _("Diese Vorlage wird noch von mindestens einem Basar verwendet."),
            )


@management_required
def manage_bazaars(request):
    return render(
        request,
        "management/bazaars.html",
        {
            "bazaars": Bazaar.objects.prefetch_related("settlements"),
            "now": timezone.now(),
        },
    )


def _build_poster_prompt(bazaar, global_settings):
    return "\n".join(
        [
            _("Erstelle ein Plakat für folgende Veranstaltung."),
            "",
            _("Standardvorgaben:"),
            global_settings.poster_prompt_instructions.strip(),
            "",
            _("Veranstaltungsdaten:"),
            _("Basar: %(name)s") % {"name": bazaar.name},
            _("Beginn: %(starts_at)s")
            % {
                "starts_at": date_format(
                    timezone.localtime(bazaar.starts_at),
                    "SHORT_DATETIME_FORMAT",
                )
            },
            _("Nummernvergabe möglich bis: %(deadline)s")
            % {
                "deadline": date_format(
                    timezone.localtime(bazaar.seller_registration_deadline),
                    "SHORT_DATETIME_FORMAT",
                )
            },
        ]
    )


@management_required
def manage_bazaar(request, bazaar_id=None):
    bazaar = get_object_or_404(Bazaar, pk=bazaar_id) if bazaar_id else Bazaar()
    form = BazaarManagementForm(request.POST or None, request.FILES or None, instance=bazaar)
    if request.method == "POST" and form.is_valid():
        bazaar = form.save(commit=False)
        bazaar.poster_prompt = _build_poster_prompt(bazaar, GlobalSettings.load())
        bazaar.save()
        messages.success(request, _("Basar wurde gespeichert."))
        return redirect("manage_bazaar_edit", bazaar_id=bazaar.pk)
    return render(
        request,
        "management/bazaar_form.html",
        {"form": form, "bazaar": bazaar, "now": timezone.now()},
    )


@login_required
def seller_dashboard(request):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    now = timezone.now()
    current_bazaars = Bazaar.objects.filter(
        status__in=[Bazaar.Status.PLANNED, Bazaar.Status.OPEN],
        seller_registration_open=True,
        seller_registration_deadline__gte=now,
    )
    current_registrations = SellerRegistration.objects.filter(
        seller=request.user,
    ).filter(
        Q(bazaar__status__in=[Bazaar.Status.PLANNED, Bazaar.Status.OPEN])
    ).select_related("bazaar").annotate(
        total_sales=Sum(
            "items__price",
            filter=Q(
                items__receipt__status=Receipt.Status.COMPLETE,
                items__cancelled_at__isnull=True,
            ),
        )
    )
    history_registrations = SellerRegistration.objects.filter(
        seller=request.user,
        bazaar__status=Bazaar.Status.CLOSED,
        bazaar__seller_can_view_history=True,
    ).select_related("bazaar").annotate(
        total_sales=Sum(
            "items__price",
            filter=Q(
                items__receipt__status=Receipt.Status.COMPLETE,
                items__cancelled_at__isnull=True,
            ),
        )
    )
    registered_bazaar_ids = set(
        SellerRegistration.objects.filter(seller=request.user).values_list("bazaar_id", flat=True)
    )
    available_bazaars = current_bazaars.exclude(pk__in=registered_bazaar_ids)
    previous_registrations = (
        SellerRegistration.objects.filter(seller=request.user)
        .filter(
            Q(
                bazaar__status=Bazaar.Status.CLOSED,
                bazaar__seller_can_view_history=True,
            )
            | Q(
                bazaar__status__in=[Bazaar.Status.PLANNED, Bazaar.Status.OPEN],
                bazaar__seller_can_view_current=True,
            )
        )
        .select_related("bazaar")
    )
    return render(
        request,
        "seller/dashboard.html",
        {
            "current_bazaars": available_bazaars,
            "previous_registrations": previous_registrations,
            "current_registrations": current_registrations,
            "history_registrations": history_registrations,
        },
    )


@login_required
def seller_bazaar_details(request, bazaar_id):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    registration = get_object_or_404(
        SellerRegistration.objects.select_related("bazaar"),
        bazaar_id=bazaar_id,
        seller=request.user,
    )
    bazaar = registration.bazaar
    finished = bazaar.status == Bazaar.Status.CLOSED
    can_view = (
        bazaar.seller_can_view_history if finished else bazaar.seller_can_view_current
    )
    if not can_view:
        raise Http404

    global_settings = GlobalSettings.load()
    settlement = _active_settlement(bazaar) if finished else None
    seller_data = None
    if settlement:
        seller_data = next(
            (
                seller
                for seller in settlement.snapshot.get("sellers", [])
                if seller["seller_number"] == registration.seller_number
            ),
            None,
        )

    if seller_data:
        lines = [
            {
                "price": Decimal(line["price"]),
                "count": line["count"],
                "total": Decimal(line["price"]) * line["count"],
            }
            for line in seller_data["lines"]
        ]
        gross = Decimal(seller_data["gross"])
        seller_fee = Decimal(seller_data["seller_fee"])
        operator_percent = Decimal(seller_data["operator_percent"])
        operator_fee = Decimal(seller_data["operator_fee"])
        payout = _seller_payout(gross, seller_fee, operator_fee)
        currency_code = settlement.snapshot.get("currency", global_settings.currency)
    else:
        grouped_items = (
            ReceiptItem.objects.filter(
                registration=registration,
                receipt__status=Receipt.Status.COMPLETE,
                cancelled_at__isnull=True,
            )
            .values("price")
            .annotate(count=Count("id"))
            .order_by("price")
        )
        lines = [
            {
                "price": Decimal(row["price"]),
                "count": row["count"],
                "total": Decimal(row["price"]) * row["count"],
            }
            for row in grouped_items
        ]
        gross = sum(
            (line["total"] for line in lines),
            start=Decimal("0.00"),
        )
        seller_fee = bazaar.seller_fee
        operator_percent = bazaar.operator_percent
        operator_fee = (gross * operator_percent / Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        payout = _seller_payout(gross, seller_fee, operator_fee)
        currency_code = global_settings.currency

    currency = "€" if currency_code == GlobalSettings.Currency.EUR else currency_code
    return render(
        request,
        "seller/bazaar_details.html",
        {
            "registration": registration,
            "finished": finished,
            "lines": lines,
            "gross": gross,
            "seller_fee": seller_fee,
            "operator_percent": operator_percent,
            "operator_fee": operator_fee,
            "payout": payout,
            "currency": currency,
        },
    )


@login_required
@require_POST
def join_bazaar(request, bazaar_id):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    reuse_id = request.POST.get("reuse_registration")
    if not request.user.consents.exists():
        messages.error(request, _("Bitte stimme zuerst den Datenschutzhinweisen zu."))
        return redirect("seller_dashboard")
    try:
        with transaction.atomic():
            bazaar = Bazaar.objects.select_for_update().get(pk=bazaar_id)
            if (
                bazaar.status == Bazaar.Status.CLOSED
                or not bazaar.seller_registration_open
                or timezone.now() > bazaar.seller_registration_deadline
            ):
                messages.error(
                    request,
                    _("Die Nummernvergabe für diesen Basar ist geschlossen."),
                )
                return redirect("seller_dashboard")
            if SellerRegistration.objects.filter(bazaar=bazaar, seller=request.user).exists():
                messages.info(request, _("Du bist für diesen Basar bereits registriert."))
                return redirect("seller_dashboard")
            registration_count = SellerRegistration.objects.filter(bazaar=bazaar).count()
            if registration_count >= bazaar.seller_number_limit:
                messages.error(
                    request,
                    _("Für diesen Basar sind bereits alle Verkäufernummern vergeben."),
                )
                return redirect("seller_dashboard")
            if reuse_id:
                previous = get_object_or_404(
                    SellerRegistration.objects.filter(seller=request.user).filter(
                        Q(
                            bazaar__status=Bazaar.Status.CLOSED,
                            bazaar__seller_can_view_history=True,
                        )
                        | Q(
                            bazaar__status__in=[Bazaar.Status.PLANNED, Bazaar.Status.OPEN],
                            bazaar__seller_can_view_current=True,
                        )
                    ),
                    pk=reuse_id,
                )
                seller_number = previous.seller_number
                if SellerRegistration.objects.filter(
                    bazaar=bazaar,
                    seller_number=seller_number,
                ).exists():
                    messages.error(request, _("Diese Verkäufernummer ist für den Basar bereits vergeben."))
                    return redirect("seller_dashboard")
            else:
                number = bazaar.seller_number_start
                while SellerRegistration.objects.filter(
                    bazaar=bazaar,
                    seller_number=bazaar.format_seller_number(number),
                ).exists():
                    number += 1
                seller_number = bazaar.format_seller_number(number)
            SellerRegistration.objects.create(
                bazaar=bazaar,
                seller=request.user,
                seller_number=seller_number,
            )
    except IntegrityError:
        messages.error(
            request,
            _("Die Verkäufernummer konnte nicht vergeben werden. Bitte erneut versuchen."),
        )
        return redirect("seller_dashboard")
    messages.success(
        request,
        _("Deine Verkäufernummer lautet %(number)s.") % {"number": seller_number},
    )
    # Weiterleiten zu Preisschildern für neue Registrierung
    return redirect("seller_price_labels_form", bazaar_id=bazaar_id)


@cashier_required
def cashier(request):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    bazaars = Bazaar.objects.filter(status=Bazaar.Status.OPEN).order_by("name")
    if request.method == "POST":
        if request.session.get("cashier_bazaar_id"):
            return redirect("cashier")
        bazaar_id = request.POST.get("bazaar_id")
        try:
            receipt = _create_cashier_receipt(request.user, bazaar_id)
        except (Bazaar.DoesNotExist, ValueError, TypeError):
            messages.error(request, _("Bitte wähle einen gültigen Basar."))
            return redirect("cashier")
        request.session["cashier_bazaar_id"] = receipt.bazaar_id
        request.session["cashier_receipt_id"] = receipt.pk
        return redirect("cashier")

    bazaar_id = request.session.get("cashier_bazaar_id")
    selected_bazaar = Bazaar.objects.filter(
        pk=bazaar_id,
        status=Bazaar.Status.OPEN,
    ).first()
    if not selected_bazaar:
        request.session.pop("cashier_bazaar_id", None)
        request.session.pop("cashier_receipt_id", None)
        request.session.pop("cashier_registration_id", None)
        return render(request, "cashier/cashier.html", {"bazaars": bazaars})

    receipt = None
    active_receipt_id = request.session.get("cashier_receipt_id")
    if active_receipt_id:
        receipt = Receipt.objects.filter(
            pk=active_receipt_id,
            bazaar=selected_bazaar,
            cashier=request.user,
            status=Receipt.Status.OPEN,
        ).first()
    if receipt is None:
        receipt = (
            Receipt.objects.filter(
                bazaar=selected_bazaar,
                cashier=request.user,
                status=Receipt.Status.OPEN,
            )
            .order_by("-created_at")
            .first()
        )
    if receipt is None:
        receipt = _create_cashier_receipt(request.user, selected_bazaar.pk)
    request.session["cashier_receipt_id"] = receipt.pk

    registrations = SellerRegistration.objects.filter(
        bazaar=selected_bazaar
    ).select_related("seller")
    registration_id = request.GET.get("registration")
    if registration_id:
        selected_registration = registrations.filter(pk=registration_id).first()
        if selected_registration:
            request.session["cashier_registration_id"] = selected_registration.pk
    else:
        selected_registration = registrations.filter(
            pk=request.session.get("cashier_registration_id")
        ).first()

    return render(
        request,
        "cashier/cashier.html",
        {
            "bazaars": bazaars,
            "selected_bazaar": selected_bazaar,
            "registrations": registrations,
            "selected_registration": selected_registration,
            "receipt": receipt,
            "form": ReceiptItemForm(),
            "common_prices": GlobalSettings.load().common_prices,
            "has_recorded_items": receipt.first_item_at is not None,
        },
    )


def _create_cashier_receipt(cashier_user, bazaar_id):
    with transaction.atomic():
        bazaar = Bazaar.objects.select_for_update().get(
            pk=bazaar_id,
            status=Bazaar.Status.OPEN,
        )
        receipt_number = bazaar.next_receipt_number
        bazaar.next_receipt_number = receipt_number + 1
        bazaar.save(update_fields=["next_receipt_number"])
        return Receipt.objects.create(
            bazaar=bazaar,
            cashier=cashier_user,
            receipt_number=receipt_number,
        )


@cashier_required
@require_POST
@cashier_required
@require_POST
def add_item(request, receipt_id):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    
    form = ReceiptItemForm(request.POST)
    registration_id = request.POST.get("registration_id")
    scanner_submission_id = request.POST.get("scanner_submission_id")
    
    if not form.is_valid():
        error_message = _("Bitte gib einen gültigen Preis ein.")
        # AJAX-Request? Gib JSON zurück
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({"error": error_message}, status=400)
        messages.error(request, error_message)
        return redirect(f"{reverse('cashier')}?registration={registration_id}")
    
    try:
        with transaction.atomic():
            receipt = get_object_or_404(
                Receipt.objects.select_for_update().select_related("bazaar"),
                pk=receipt_id,
                cashier=request.user,
                status=Receipt.Status.OPEN,
                bazaar_id=request.session.get("cashier_bazaar_id"),
            )
            if receipt.bazaar.status != Bazaar.Status.OPEN:
                error_message = _("Dieser Basar ist nicht mehr aktiv.")
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({"error": error_message}, status=400)
                messages.error(request, error_message)
                return redirect("cashier")
            
            registration = get_object_or_404(
                SellerRegistration,
                pk=registration_id,
                bazaar=receipt.bazaar,
            )
            item = form.save(commit=False)
            scanner_submission = None
            if scanner_submission_id:
                scanner_submission = get_object_or_404(
                    ScannerSubmission.objects.select_for_update(),
                    pk=scanner_submission_id,
                    session__user=request.user,
                    session__bazaar=receipt.bazaar,
                    receipt=receipt,
                    processed=False,
                )
                if (
                    scanner_submission.registration_id != registration.pk
                    or scanner_submission.price != item.price
                ):
                    error_message = _("Scanner-Eingabe stimmt nicht mit dem Artikel überein.")
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return JsonResponse({"error": error_message}, status=400)
                    messages.error(request, error_message)
                    return redirect("cashier")
            item.receipt = receipt
            item.registration = registration
            item.save()
            if scanner_submission:
                scanner_submission.mark_processed()
            if receipt.first_item_at is None:
                receipt.first_item_at = timezone.now()
                receipt.save(update_fields=["first_item_at"])
        
        # AJAX-Request? Gib JSON zurück
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({"success": True, "item_id": item.id})
        
        return redirect(f"{reverse('cashier')}?registration={registration.pk}")
    
    except Exception as e:
        error_message = str(e)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({"error": error_message}, status=500)
        messages.error(request, error_message)
        return redirect("cashier")


@cashier_required
@require_POST
def cancel_item(request, receipt_id, item_id):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    with transaction.atomic():
        receipt = get_object_or_404(
            Receipt.objects.select_for_update(),
            pk=receipt_id,
            cashier=request.user,
            status=Receipt.Status.OPEN,
            bazaar_id=request.session.get("cashier_bazaar_id"),
        )
        item = get_object_or_404(
            ReceiptItem.objects.select_for_update(),
            pk=item_id,
            receipt=receipt,
            cancelled_at__isnull=True,
        )
        item.cancelled_at = timezone.now()
        item.cancelled_by = request.user
        item.save(update_fields=["cancelled_at", "cancelled_by"])
        registration_id = item.registration_id
    return redirect(f"{reverse('cashier')}?registration={registration_id}")


@cashier_required
@require_POST
def finish_receipt(request, receipt_id):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    with transaction.atomic():
        receipt = get_object_or_404(
            Receipt.objects.select_for_update().select_related("bazaar"),
            pk=receipt_id,
            cashier=request.user,
            status=Receipt.Status.OPEN,
            bazaar_id=request.session.get("cashier_bazaar_id"),
        )
        if receipt.bazaar.status != Bazaar.Status.OPEN:
            messages.error(request, _("Dieser Basar ist nicht mehr aktiv."))
            return redirect("cashier")
        if receipt.first_item_at is None:
            messages.error(request, _("Ein leerer Beleg kann nicht abgeschlossen werden."))
            return redirect("cashier")
        receipt.status = Receipt.Status.COMPLETE
        receipt.completed_at = timezone.now()
        receipt.save(update_fields=["status", "completed_at"])
        total = receipt.total
        seller_totals = (
            receipt.items.filter(cancelled_at__isnull=True)
            .values("registration__seller_number")
            .annotate(total=Sum("price"))
            .order_by("registration__seller_number")
        )
        
    request.session.pop("cashier_receipt_id", None)
    request.session.pop("cashier_registration_id", None)
    request.session["cashier_last_receipt_id"] = receipt.pk
    return redirect("receipt_complete", receipt_id=receipt.pk)


@cashier_required
def receipt_complete(request, receipt_id):
    receipt = get_object_or_404(
        Receipt.objects.select_related("bazaar"),
        pk=receipt_id,
        cashier=request.user,
        status=Receipt.Status.COMPLETE,
    )
    seller_totals = (
        receipt.items.filter(cancelled_at__isnull=True)
        .values("registration__seller_number")
        .annotate(total=Sum("price"))
        .order_by("registration__seller_number")
    )
    return render(
        request,
        "cashier/receipt_complete.html",
        {"receipt": receipt, "total": receipt.total, "seller_totals": seller_totals},
    )


@cashier_required
@require_POST
def next_sale(request):
    if _needs_consent(request.user):
        return redirect("accept_consent")
    bazaar_id = request.session.get("cashier_bazaar_id")
    if not bazaar_id:
        messages.error(request, _("Bitte wähle zuerst einen Basar aus."))
        return redirect("cashier")
    active_receipt_id = request.session.get("cashier_receipt_id")
    if active_receipt_id and Receipt.objects.filter(
        pk=active_receipt_id,
        cashier=request.user,
        status=Receipt.Status.OPEN,
    ).exists():
        return redirect("cashier")
    try:
        receipt = _create_cashier_receipt(request.user, bazaar_id)
    except Bazaar.DoesNotExist:
        request.session.pop("cashier_bazaar_id", None)
        messages.error(request, _("Der ausgewählte Basar ist nicht mehr aktiv."))
        return redirect("cashier")
    request.session["cashier_receipt_id"] = receipt.pk
    request.session.pop("cashier_last_receipt_id", None)
    return redirect("cashier")


def _settlement_data(bazaar):
    global_settings = GlobalSettings.load()
    currency = global_settings.currency
    invoice_template = (
        bazaar.invoice_template or global_settings.default_seller_invoice_template
    )
    registrations = SellerRegistration.objects.filter(bazaar=bazaar).select_related("seller")
    data = []
    completed_items = (
        ReceiptItem.objects.filter(
            receipt__bazaar=bazaar,
            receipt__status=Receipt.Status.COMPLETE,
            cancelled_at__isnull=True,
        )
        .values("registration_id", "price")
        .annotate(count=Count("id"))
        .order_by()
    )
    grouped = defaultdict(list)
    for row in completed_items:
        grouped[row["registration_id"]].append(
            {"price": str(row["price"]), "count": row["count"]}
        )
    for registration in registrations:
        lines = grouped[registration.pk]
        gross = sum(
            (Decimal(line["price"]) * line["count"] for line in lines),
            start=Decimal("0.00"),
        )
        operator_fee = (gross * bazaar.operator_percent / Decimal("100")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        seller_fee = Decimal(bazaar.seller_fee)
        data.append(
            {
                "seller_number": registration.seller_number,
                "name": registration.seller.last_name,
                "vorname": registration.seller.first_name,
                "lines": lines,
                "gross": str(gross),
                "seller_fee": str(seller_fee),
                "operator_percent": str(bazaar.operator_percent),
                "operator_fee": str(operator_fee),
                "payout": str(_seller_payout(gross, seller_fee, operator_fee)),
            }
        )
    open_receipts = []
    unfinished = (
        Receipt.objects.filter(
            bazaar=bazaar,
            status=Receipt.Status.OPEN,
            first_item_at__isnull=False,
        )
        .prefetch_related("items__registration")
        .order_by("first_item_at", "receipt_number")
    )
    for receipt in unfinished:
        seller_totals = defaultdict(lambda: Decimal("0.00"))
        cancelled_count = 0
        for item in receipt.items.all():
            if item.cancelled_at:
                cancelled_count += 1
            else:
                seller_totals[item.registration.seller_number] += item.price
        open_receipts.append(
            {
                "receipt_number": receipt.receipt_number,
                "first_item_at": date_format(
                    timezone.localtime(receipt.first_item_at),
                    "SHORT_DATETIME_FORMAT",
                ),
                "seller_totals": [
                    {"seller_number": number, "total": str(total)}
                    for number, total in sorted(seller_totals.items())
                ],
                "total": str(sum(seller_totals.values(), start=Decimal("0.00"))),
                "cancelled_count": cancelled_count,
            }
        )
    return {
        "sellers": data,
        "overall": _bazaar_overall_totals(data),
        "open_receipts": open_receipts,
        "currency": currency,
        "invoice_template": (
            str(DEFAULT_SELLER_INVOICE_TEMPLATE)
            if not invoice_template or invoice_template.is_read_only
            else invoice_template.body
        ),
        "bazaar_name": bazaar.name,
        "bazaar_date": date_format(
            timezone.localtime(bazaar.starts_at),
            "SHORT_DATE_FORMAT",
        ),
    }


def _seller_payout(gross, seller_fee, operator_fee):
    return max(Decimal("0.00"), gross - seller_fee - operator_fee)


def _bazaar_overall_totals(sellers):
    price_counts = defaultdict(int)
    gross = Decimal("0.00")
    seller_fees = Decimal("0.00")
    operator_fees = Decimal("0.00")
    payouts = Decimal("0.00")
    for seller in sellers:
        seller_gross = Decimal(seller["gross"])
        seller_fee = Decimal(seller["seller_fee"])
        operator_fee = Decimal(seller["operator_fee"])
        gross += seller_gross
        seller_fees += seller_fee
        operator_fees += operator_fee
        payouts += _seller_payout(seller_gross, seller_fee, operator_fee)
        for line in seller["lines"]:
            price_counts[Decimal(line["price"])] += line["count"]
    lines = [
        {
            "price": str(price),
            "count": count,
            "total": str(price * count),
        }
        for price, count in sorted(price_counts.items())
    ]
    return {
        "lines": lines,
        "gross": str(gross),
        "seller_fees": str(seller_fees),
        "operator_fees": str(operator_fees),
        "payouts": str(payouts),
    }


def _build_pdf(bazaar, snapshot):
    if isinstance(snapshot, list):
        snapshot = {"sellers": snapshot, "open_receipts": []}
    global_settings = GlobalSettings.load()
    currency_code = snapshot.get("currency", global_settings.currency)
    currency = "€" if currency_code == GlobalSettings.Currency.EUR else currency_code
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=_("Abrechnung - %(name)s") % {"name": bazaar.name},
    )
    styles = getSampleStyleSheet()
    story = []
    logo_data = None
    if global_settings.logo:
        with global_settings.logo.open("rb") as logo_file:
            logo_data = logo_file.read()
    sellers = snapshot["sellers"]
    overall = snapshot.get("overall") or _bazaar_overall_totals(sellers)
    template = snapshot.get("invoice_template") or DEFAULT_SELLER_INVOICE_TEMPLATE
    if logo_data:
        story.extend(
            [
                Image(BytesIO(logo_data), width=35 * mm, height=20 * mm, kind="proportional"),
                Spacer(1, 5 * mm),
            ]
        )
    story.append(
        Paragraph(
            _("Gesamtabrechnung: %(name)s") % {"name": bazaar.name},
            styles["Title"],
        )
    )
    story.append(
        Paragraph(
            _(
                "Zusammenfassung aller abgeschlossenen Verkäufe; offene Belege sind separat aufgeführt."
            ),
            styles["Normal"],
        )
    )
    overall_rows = [[_("Preis"), _("Anzahl"), _("Summe (%(currency)s)") % {"currency": currency}]]
    overall_rows.extend(
        [
            (
                f"{_format_amount(Decimal(line['price']))} {currency}",
                str(line["count"]),
                f"{_format_amount(Decimal(line['total']))} {currency}",
            )
            for line in overall["lines"]
        ]
    )
    if not overall["lines"]:
        overall_rows.append([_("Keine abgeschlossenen Verkäufe."), "", ""])
    overall_table = Table(
        overall_rows,
        colWidths=[60 * mm, 25 * mm, 70 * mm],
        repeatRows=1,
    )
    overall_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(Spacer(1, 6 * mm))
    story.append(overall_table)
    overall_totals = [
        [_("Gesamtumsatz"), f"{_format_amount(Decimal(overall['gross']))} {currency}"],
        [
            _("Verkäufergebühren"),
            f"-{_format_amount(Decimal(overall['seller_fees']))} {currency}",
        ],
        [
            _("Betreiberanteile"),
            f"-{_format_amount(Decimal(overall['operator_fees']))} {currency}",
        ],
        [_("Gesamtauszahlung"), f"{_format_amount(Decimal(overall['payouts']))} {currency}"],
    ]
    overall_totals_table = Table(overall_totals, colWidths=[105 * mm, 50 * mm])
    overall_totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(overall_totals_table)
    if sellers:
        story.append(PageBreak())
    for index, seller in enumerate(sellers):
        if index:
            story.append(PageBreak())
        if logo_data:
            story.extend(
                [
                    Image(BytesIO(logo_data), width=35 * mm, height=20 * mm, kind="proportional"),
                    Spacer(1, 5 * mm),
                ]
            )
        item_lines = []
        for line in seller["lines"]:
            price = Decimal(line["price"])
            item_lines.append((price, line["count"], price * line["count"]))
        seller_payout = _seller_payout(
            Decimal(seller["gross"]),
            Decimal(seller["seller_fee"]),
            Decimal(seller["operator_fee"]),
        )
        rendered_template = render_invoice_template(
            template,
            {
                "basar_name": snapshot.get("bazaar_name", bazaar.name),
                "basar_datum": snapshot.get(
                    "bazaar_date",
                    date_format(
                        timezone.localtime(bazaar.starts_at),
                        "SHORT_DATE_FORMAT",
                    ),
                ),
                "verkaeufernummer": seller["seller_number"],
                "name": seller.get("name", ""),
                "vorname": seller.get("vorname", ""),
                "artikel_zeilen": "__ARTIKELTABELLE__",
                "summen": "__SUMMENTABELLE__",
                "bruttoumsatz": _format_amount(Decimal(seller["gross"])),
                "verkaeufergebuehr": _format_amount(Decimal(seller["seller_fee"])),
                "betreiberanteil_prozent": _format_amount(
                    Decimal(seller["operator_percent"])
                ),
                "betreiberanteil": _format_amount(Decimal(seller["operator_fee"])),
                "auszahlung": _format_amount(seller_payout),
                "waehrung": currency,
            },
        )
        rendered_template = bleach.clean(
            rendered_template,
            tags={"b", "strong", "i", "em", "u", "br"},
            strip=True,
            strip_comments=True,
        )
        rendered_template = re.sub(r"<br\s*/?>", "<br/>", rendered_template)
        rendered_template = (
            rendered_template.replace("<strong>", "<b>")
            .replace("</strong>", "</b>")
            .replace("<em>", "<i>")
            .replace("</em>", "</i>")
        )
        item_rows = [[_("Preis"), _("Anzahl"), _("Summe (%(currency)s)") % {"currency": currency}]]
        item_rows.extend(
            [
                (
                    f"{_format_amount(price)} {currency}",
                    str(count),
                    f"{_format_amount(total)} {currency}",
                )
                for price, count, total in item_lines
            ]
        )
        if not item_lines:
            item_rows.append([_("Keine verkauften Artikel."), "", ""])
        items_table = Table(item_rows, colWidths=[60 * mm, 25 * mm, 70 * mm], repeatRows=1)
        items_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        summary_rows = [
            [
                _("Bruttoumsatz"),
                f"{_format_amount(Decimal(seller['gross']))} {currency}",
            ],
            [
                _("Verkäufergebühr"),
                f"-{_format_amount(Decimal(seller['seller_fee']))} {currency}",
            ],
            [
                _("Betreiberanteil (%(percent)s %%)")
                % {"percent": _format_amount(Decimal(seller["operator_percent"]))},
                f"-{_format_amount(Decimal(seller['operator_fee']))} {currency}",
            ],
            [_("Auszahlung"), f"{_format_amount(seller_payout)} {currency}"],
        ]
        summary_table = Table(summary_rows, colWidths=[105 * mm, 50 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        for line in rendered_template.splitlines():
            for part in re.split(r"(__ARTIKELTABELLE__|__SUMMENTABELLE__)", line):
                if part == "__ARTIKELTABELLE__":
                    story.append(items_table)
                elif part == "__SUMMENTABELLE__":
                    story.append(summary_table)
                elif part.strip():
                    story.append(Paragraph(part, styles["Normal"]))
                else:
                    story.append(Spacer(1, 3 * mm))
    if snapshot["open_receipts"]:
        story.append(PageBreak())
        story.append(
            Paragraph(
                _("Nicht abgerechnete, offene Kassenbelege"),
                styles["Heading1"],
            )
        )
        story.append(
            Paragraph(
                _("Diese Belege sind nicht in den Verkäuferabrechnungen enthalten."),
                styles["Normal"],
            )
        )
        rows = [
            [
                _("Erste Erfassung"),
                _("Beleg"),
                _("Verkäufernummer"),
                _("Erfasster Betrag"),
            ]
        ]
        for receipt in snapshot["open_receipts"]:
            if receipt["seller_totals"]:
                for seller_total in receipt["seller_totals"]:
                    rows.append(
                        [
                            receipt["first_item_at"],
                            str(receipt["receipt_number"]),
                            seller_total["seller_number"],
                            f"{_format_amount(Decimal(seller_total['total']))} {currency}",
                        ]
                    )
            else:
                rows.append(
                    [
                        receipt["first_item_at"],
                        str(receipt["receipt_number"]),
                        "–",
                        f"{_format_amount(Decimal(receipt['total']))} {currency}",
                    ]
                )
        table = Table(
            rows,
            colWidths=[43 * mm, 20 * mm, 42 * mm, 50 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)
    document.build(story)
    return output.getvalue()


def _active_settlement(bazaar):
    return BazaarSettlement.objects.filter(
        bazaar=bazaar,
        cancelled_at__isnull=True,
    ).order_by("-created_at").first()


def _settlement_is_available(bazaar):
    return (
        bazaar.status == Bazaar.Status.CLOSED
        or bazaar.is_settled
        or bazaar.starts_at <= timezone.now()
    )


def _close_bazaar_for_settlement(bazaar, settlement=None):
    if bazaar.status == Bazaar.Status.CLOSED and not bazaar.seller_registration_open:
        if not settlement or bazaar.settlement_created_at:
            return
    bazaar.status = Bazaar.Status.CLOSED
    bazaar.seller_registration_open = False
    update_fields = ["status", "seller_registration_open"]
    if settlement and not bazaar.settlement_created_at:
        bazaar.settlement_created_at = settlement.created_at
        update_fields.append("settlement_created_at")
    bazaar.save(update_fields=update_fields)


@management_required
def settlement_start(request, bazaar_id):
    """
    Settlement-Bestätigungsseite für Basar-Abrechnung.
    - Zeigt Abrechnungsdaten und offene Belege an
    - Schließt Basar automatisch bei erstem Aufruf (nach Basar-Abschlussdatum)
    """
    bazaar = get_object_or_404(Bazaar, pk=bazaar_id)
    settlement = _active_settlement(bazaar)
    settlement_snapshot = settlement.snapshot if settlement else _settlement_data(bazaar)
    overall = settlement_snapshot.get("overall") or _bazaar_overall_totals(
        settlement_snapshot.get("sellers", [])
    )
    currency_code = settlement_snapshot.get(
        "currency",
        GlobalSettings.load().currency,
    )
    currency = "€" if currency_code == GlobalSettings.Currency.EUR else currency_code
    settlement_history = (
        BazaarSettlement.objects.filter(bazaar=bazaar)
        .select_related("cancelled_by")
        .order_by("-created_at")
    )
    can_create_settlement = _settlement_is_available(bazaar)
    
    if not can_create_settlement and not settlement_history.exists():
        messages.error(request, _("Die Abrechnung ist erst nach Abschluss des Basars verfügbar."))
        return redirect("manage_bazaars")
    
    # Basar schließen, wenn Bedingungen erfüllt sind
    if can_create_settlement:
        _close_bazaar_for_settlement(bazaar, settlement)
    
    return render(
        request,
        "settlement_confirm.html",
        {
            "bazaar": bazaar,
            "settlement": settlement,
            "settlement_history": settlement_history,
            "can_cancel_settlement": request.user.is_superuser,
            "can_create_settlement": can_create_settlement,
            "overall": overall,
            "currency": currency,
            "open_receipts": settlement_snapshot.get("open_receipts", []),
        },
    )


@management_required
@require_POST
def settlement_pdf(request, bazaar_id):
    bazaar = get_object_or_404(Bazaar, pk=bazaar_id)
    with transaction.atomic():
        locked_bazaar = Bazaar.objects.select_for_update().get(pk=bazaar.pk)
        settlement = _active_settlement(locked_bazaar)
        if not _settlement_is_available(locked_bazaar):
            messages.error(request, _("Die Abrechnung ist erst nach Abschluss des Basars verfügbar."))
            return redirect("manage_bazaars")
        _close_bazaar_for_settlement(locked_bazaar, settlement)
        created = settlement is None
        if created:
            settlement = BazaarSettlement.objects.create(
                bazaar=locked_bazaar,
                snapshot=_settlement_data(locked_bazaar),
            )
        if created:
            locked_bazaar.settlement_created_at = timezone.now()
            locked_bazaar.save(update_fields=["settlement_created_at"])
        if not settlement.pdf:
            pdf_content = _build_pdf(locked_bazaar, settlement.snapshot)
            settlement.pdf.save(
                _("basar-%(id)s-abrechnung.pdf") % {"id": locked_bazaar.pk},
                ContentFile(pdf_content),
                save=True,
            )
    return redirect("settlement_download", bazaar_id=locked_bazaar.pk)


@management_required
@require_POST
def settlement_cancel(request, bazaar_id):
    with transaction.atomic():
        bazaar = get_object_or_404(
            Bazaar.objects.select_for_update(),
            pk=bazaar_id,
        )
        settlement = _active_settlement(bazaar)
        if settlement is None:
            messages.error(
                request,
                _("Für diesen Basar gibt es keine aktive Abrechnung zum Stornieren."),
            )
            return redirect("manage_bazaar_edit", bazaar_id=bazaar.pk)
        settlement.cancelled_at = timezone.now()
        settlement.cancelled_by = request.user
        settlement.save(update_fields=["cancelled_at", "cancelled_by"])
        bazaar.settlement_created_at = None
        bazaar.save(update_fields=["settlement_created_at"])
    messages.success(
        request,
        _("Die Abrechnung wurde storniert. Der Basar kann jetzt wieder aktiviert werden."),
    )
    return redirect("manage_bazaar_edit", bazaar_id=bazaar.pk)


@settlement_access_required
def settlement_download(request, bazaar_id):
    settlement = get_object_or_404(
        BazaarSettlement,
        bazaar_id=bazaar_id,
        cancelled_at__isnull=True,
    )
    if not settlement.pdf:
        raise Http404
    with settlement.pdf.open("rb") as saved_pdf:
        pdf_content = saved_pdf.read()
    response = HttpResponse(pdf_content, content_type="application/pdf")
    response["Content-Disposition"] = (
        'attachment; filename="%s"'
        % (_("basar-%(id)s-abrechnung.pdf") % {"id": bazaar_id})
    )
    return response


@settlement_access_required
def settlement_history_download(request, settlement_id):
    settlement = get_object_or_404(BazaarSettlement, pk=settlement_id)
    if not settlement.pdf:
        raise Http404
    with settlement.pdf.open("rb") as saved_pdf:
        pdf_content = saved_pdf.read()
    response = HttpResponse(pdf_content, content_type="application/pdf")
    response["Content-Disposition"] = (
        'attachment; filename="%s"'
        % (
            _("basar-%(bazaar_id)s-abrechnung-%(settlement_id)s.pdf")
            % {
                "bazaar_id": settlement.bazaar_id,
                "settlement_id": settlement.pk,
            }
        )
    )
    return response


# =============================================================================
# Benutzerverwaltung (ohne Django Admin)
# =============================================================================


@management_required
def user_management_list(request):
    """Liste aller Benutzer mit Filtermöglichkeiten."""
    form = UserSearchForm(request.GET or None)
    queryset = User.objects.all().order_by("-date_joined")

    # Suchfilter
    if form.is_valid():
        search = form.cleaned_data.get("search", "").strip()
        filter_type = form.cleaned_data.get("filter", "")

        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
            )

        if filter_type == "active":
            queryset = queryset.filter(is_active=True)
        elif filter_type == "inactive":
            queryset = queryset.filter(is_active=False)
        elif filter_type == "cashier":
            queryset = queryset.filter(is_cashier=True)
        elif filter_type == "admin":
            queryset = queryset.filter(is_superuser=True)

    return render(
        request,
        "admin/user_management_list.html",
        {
            "users": queryset,
            "form": form,
        },
    )


@management_required
def user_management_edit(request, user_id):
    """Benutzer bearbeiten und Rechte vergeben."""
    user_to_edit = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        form = UserManagementForm(
            request.POST,
            instance=user_to_edit,
            current_user=request.user,
        )
        if form.is_valid():
            form.save()
            messages.success(
                request,
                _("Benutzer '%(name)s' wurde aktualisiert.") % {"name": user_to_edit.email},
            )
            return redirect("user_management_list")
    else:
        form = UserManagementForm(
            instance=user_to_edit,
            current_user=request.user,
        )

    return render(
        request,
        "admin/user_management_edit.html",
        {
            "form": form,
            "user_to_edit": user_to_edit,
        },
    )


@management_required
@require_POST
def user_anonymize(request, user_id):
    """Benutzer anonymisieren (ähnlich wie Kontolöschung)."""
    user_to_delete = get_object_or_404(User, pk=user_id)

    # Verhindere Selbstlöschung
    if user_to_delete.pk == request.user.pk:
        messages.error(request, _("Du kannst dein eigenes Konto nicht löschen."))
        return redirect("user_management_list")

    # Verhindere Löschung des letzten aktiven Admins
    if user_to_delete.is_superuser:
        other_admins = User.objects.filter(is_superuser=True, is_active=True).exclude(
            pk=user_to_delete.pk
        )
        if not other_admins.exists():
            messages.error(
                request,
                _(
                    "Der letzte aktive Administrator kann nicht gelöscht werden. "
                    "Richte zuerst einen weiteren Administrator ein."
                ),
            )
            return redirect("user_management_list")

    with transaction.atomic():
        user_to_delete.consents.all().delete()
        user_to_delete.groups.clear()
        user_to_delete.user_permissions.clear()
        user_to_delete.email = f"deleted-account-{user_to_delete.pk}@deleted.invalid"
        user_to_delete.first_name = ""
        user_to_delete.last_name = ""
        user_to_delete.phone_number = ""
        user_to_delete.address_street = ""
        user_to_delete.address_postal_code = ""
        user_to_delete.address_city = ""
        user_to_delete.last_login = None
        user_to_delete.is_active = False
        user_to_delete.is_cashier = False
        user_to_delete.is_staff = False
        user_to_delete.is_superuser = False
        user_to_delete.is_anonymized = True
        user_to_delete.set_unusable_password()
        user_to_delete.save()

    messages.success(
        request,
        _("Benutzer wurde anonymisiert und gelöscht."),
    )
    return redirect("user_management_list")


# ============================================================================
# QR-CODE SCANNER VIEWS
# ============================================================================


@cashier_required
def scanner_generate_code(request):
    """Generiert einen eindeutigen Scanner-Anmeldecode für die Kasse"""
    from django.http import JsonResponse
    
    bazaar_id = request.session.get("cashier_bazaar_id")
    if not bazaar_id:
        return JsonResponse({"error": _("Kein Basar ausgewählt")}, status=400)

    try:
        bazaar = Bazaar.objects.get(pk=bazaar_id, status="open")
    except Bazaar.DoesNotExist:
        return JsonResponse({"error": _("Basar nicht gefunden oder nicht mehr aktiv")}, status=404)

    # Eindeutigen Code generieren (50 Zeichen)
    import secrets
    code = secrets.token_urlsafe(37)[:50]

    # Session erstellen, gültig für 30 Minuten
    expires_at = timezone.now() + timedelta(minutes=30)

    session = ScannerSession.objects.create(
        code=code,
        bazaar=bazaar,
        user=request.user,
        expires_at=expires_at,
        device_name=request.GET.get("device", "").strip()[:100],
    )

    # QR-Code generieren
    try:
        import qrcode
    except ImportError:
        return JsonResponse(
            {"error": _("QR-Code-Bibliothek nicht installiert")},
            status=500,
        )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(request.build_absolute_uri(f"/scanner/join?code={code}"))
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Als Base64 zurückgeben
    from base64 import b64encode
    from io import BytesIO

    img_io = BytesIO()
    qr_img.save(img_io, format="PNG")
    img_io.seek(0)
    qr_base64 = b64encode(img_io.getvalue()).decode()

    return JsonResponse(
        {
            "code": code,
            "expires_at": expires_at.isoformat(),
            "qr_url": request.build_absolute_uri(f"/scanner/join?code={code}"),
            "qr_base64": f"data:image/png;base64,{qr_base64}",
        }
    )


def scanner_join(request):
    """Scanner-Handy öffnet diese Seite und startet QR-Scanner"""
    code = request.GET.get("code", "").strip()
    if not code:
        return render(request, "scanner/error.html", {"error": _("Ungültiger Code")})

    try:
        session = ScannerSession.objects.get(code=code)
    except ScannerSession.DoesNotExist:
        return render(request, "scanner/error.html", {"error": _("Code nicht gefunden")})

    if session.is_expired:
        session.mark_expired()
        return render(
            request,
            "scanner/error.html",
            {"error": _("Session abgelaufen. Neue Session in der Kasse starten.")},
        )

    if not session.is_active:
        return render(
            request,
            "scanner/error.html",
            {"error": _("Scanner-Session wurde geschlossen.")},
        )

    registrations = SellerRegistration.objects.filter(
        bazaar=session.bazaar
    ).order_by("seller_number")

    # Cookie für Scan-Submissions
    response = render(
        request,
        "scanner/join.html",
        {
            "code": code,
            "bazaar": session.bazaar.name,
            "registrations": registrations,
            "session": session,
        },
    )
    response.set_cookie(
        "scanner_session_code",
        code,
        max_age=30 * 60,
        httponly=True,
        samesite="Lax",
    )
    return response


@csrf_exempt
@require_POST
def scanner_submit(request):
    """Handy sendet gescannte Artikel"""
    from django.http import JsonResponse

    code = request.POST.get("code", "").strip()
    qr_data = request.POST.get("qr_data", "").strip()

    if not code or not qr_data:
        return JsonResponse({"error": _("Fehlende Parameter")}, status=400)

    try:
        session = ScannerSession.objects.get(code=code)
    except ScannerSession.DoesNotExist:
        return JsonResponse({"error": _("Code ungültig")}, status=403)

    if session.is_expired:
        session.mark_expired()
        return JsonResponse(
            {"error": _("Session abgelaufen")},
            status=403,
        )

    if not session.is_active:
        return JsonResponse(
            {"error": _("Scanner-Session geschlossen")},
            status=403,
        )

    # QR-Daten parsen: "VKNr=42&price=2.50"
    import re

    match = re.match(r"VKNr=(\d+)&price=([\d.]+)", qr_data)
    if not match:
        return JsonResponse(
            {"error": _("QR-Code-Format ungültig")},
            status=400,
        )

    try:
        registration_id = int(match.group(1))
        price = Decimal(match.group(2))
    except (ValueError, TypeError):
        return JsonResponse(
            {"error": _("Ungültige Werte im QR-Code")},
            status=400,
        )

    # Rate-Limit prüfen (max 100 pro Minute)
    recent_submissions = ScannerSubmission.objects.filter(
        session=session,
        submitted_at__gte=timezone.now() - timedelta(minutes=1),
    ).count()

    if recent_submissions >= 100:
        return JsonResponse(
            {"error": _("Rate-Limit erreicht. Bitte warten.")},
            status=429,
        )

    receipt = (
        Receipt.objects.filter(
            bazaar=session.bazaar,
            cashier=session.user,
            status=Receipt.Status.OPEN,
        )
        .order_by("-created_at")
        .first()
    )

    # Submission erstellen
    submission = ScannerSubmission.objects.create(
        session=session,
        receipt=receipt,
        registration_id=registration_id,
        price=price,
    )

    return JsonResponse(
        {
            "success": True,
            "submission_id": submission.id,
        }
    )


@login_required
def scanner_pending(request):
    """Kasse pollt auf neue gescannte Artikel"""
    from django.http import JsonResponse

    if not request.user.is_cashier and not request.user.is_superuser:
        return JsonResponse({"error": _("Keine Berechtigung")}, status=403)

    code = request.GET.get("code", "").strip()
    if not code:
        return JsonResponse({"error": _("Code erforderlich")}, status=400)

    try:
        session = ScannerSession.objects.get(code=code, user=request.user)
    except ScannerSession.DoesNotExist:
        return JsonResponse({"error": _("Session nicht gefunden")}, status=404)

    if session.is_expired:
        session.mark_expired()
        return JsonResponse({"submissions": []})

    receipt = Receipt.objects.filter(
        pk=request.session.get("cashier_receipt_id"),
        bazaar=session.bazaar,
        cashier=request.user,
        status=Receipt.Status.OPEN,
    ).first()

    submissions = ScannerSubmission.objects.filter(session=session, processed=False)
    if receipt is not None:
        submissions = submissions.filter(receipt=receipt)
    else:
        submissions = submissions.filter(receipt__isnull=True)
    submissions = submissions.order_by("submitted_at")

    result = []
    for sub in submissions:
        result.append(
            {
                "id": sub.id,
                "registration_id": sub.registration_id,
                "price": float(sub.price),
                "submitted_at": sub.submitted_at.isoformat(),
            }
        )

    return JsonResponse({"submissions": result})


@require_POST
@login_required
def scanner_close(request):
    """Kassierer schließt Scanner-Session manuell"""
    from django.http import JsonResponse

    if not request.user.is_cashier and not request.user.is_superuser:
        return JsonResponse({"error": _("Keine Berechtigung")}, status=403)

    code = request.POST.get("code", "").strip()
    if not code:
        return JsonResponse({"error": _("Code erforderlich")}, status=400)

    try:
        session = ScannerSession.objects.get(code=code, user=request.user)
    except ScannerSession.DoesNotExist:
        return JsonResponse({"error": _("Session nicht gefunden")}, status=404)

    session.is_active = False
    session.save(update_fields=["is_active"])

    return JsonResponse({"success": True})


@login_required
def seller_price_labels_form(request, bazaar_id):
    """Formular zur Auswahl von Preisen und Mengen für Preisschilder"""
    from decimal import Decimal
    
    if _needs_consent(request.user):
        return redirect("accept_consent")
    
    registration = get_object_or_404(
        SellerRegistration.objects.select_related("bazaar"),
        bazaar_id=bazaar_id,
        seller=request.user,
    )
    bazaar = registration.bazaar
    
    # Nur offene und geplante Basare
    if bazaar.status not in [Bazaar.Status.OPEN, Bazaar.Status.PLANNED]:
        raise Http404
    
    global_settings = GlobalSettings.load()
    
    # Preise kategorisieren
    prices_data = []
    price_index_map = {}  # Mapping von Preis zu Index
    if global_settings.common_prices:
        sorted_prices = sorted([Decimal(str(p)) for p in global_settings.common_prices])
        
        # Kategorien definieren basierend auf Anzahl und Wert
        total = len(sorted_prices)
        low_count = (total + 2) // 3  # Unteren Drittel
        high_start = total - (total // 3)  # Oberes Drittel
        
        for i, price in enumerate(sorted_prices):
            price_index_map[str(price)] = i
            
            if i < low_count:
                category = 'low'
                default_count = 10
            elif i >= high_start:
                category = 'high'
                default_count = 1
            else:
                category = 'medium'
                default_count = 5
            
            prices_data.append({
                'price': price,
                'index': i,
                'category': category,
                'default_count': default_count,
            })
    
    return render(
        request,
        "seller/price_labels.html",
        {
            "registration": registration,
            "prices": prices_data,
            "currency": global_settings.currency,
            "label_config": {
                "columns": global_settings.price_label_columns,
                "rows": global_settings.price_label_rows,
                "total": global_settings.price_label_columns * global_settings.price_label_rows,
            }
        },
    )


@login_required
def seller_price_labels_generate(request, bazaar_id):
    """Generiert PDF mit QR-Code-Preisschildern"""
    from decimal import Decimal
    import qrcode
    from io import BytesIO
    
    if _needs_consent(request.user):
        return redirect("accept_consent")
    
    if request.method != "POST":
        return redirect("seller_price_labels_form", bazaar_id=bazaar_id)
    
    registration = get_object_or_404(
        SellerRegistration.objects.select_related("bazaar"),
        bazaar_id=bazaar_id,
        seller=request.user,
    )
    bazaar = registration.bazaar
    
    # Nur offene und geplante Basare
    if bazaar.status not in [Bazaar.Status.OPEN, Bazaar.Status.PLANNED]:
        raise Http404
    
    global_settings = GlobalSettings.load()
    
    # Ausgewählte Preise und Mengen sammeln
    label_data = []
    if global_settings.common_prices:
        sorted_prices = sorted([Decimal(str(p)) for p in global_settings.common_prices])
        
        for i, price in enumerate(sorted_prices):
            # Formularfeld auslesen - mit Index statt Preis
            count_str = request.POST.get(f"price_{i}")
            if count_str:
                try:
                    count = int(count_str)
                    if count > 0:
                        label_data.append({
                            'price': price,
                            'count': count,
                        })
                except (ValueError, TypeError):
                    pass
    
    if not label_data:
        messages.error(request, _("Bitte wählen Sie mindestens einen Preis."))
        return redirect("seller_price_labels_form", bazaar_id=bazaar_id)
    
    # PDF generieren (Konfigurierbar: Spalten und Reihen aus Systemeinstellungen)
    try:
        pdf_buffer = _generate_price_labels_pdf(
            label_data,
            registration.seller_number,
            global_settings.currency,
            global_settings.price_label_columns,
            global_settings.price_label_rows,
        )
    except Exception as e:
        messages.error(request, _("Fehler beim Generieren des PDFs: {}").format(str(e)))
        return redirect("seller_price_labels_form", bazaar_id=bazaar_id)
    
    # PDF als Download zurückgeben
    response = HttpResponse(pdf_buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="preisschilder_{registration.seller_number}.pdf"'
    return response


def _generate_price_labels_pdf(label_data, seller_number, currency, cols, rows):
    """Generiert ein PDF mit QR-Code-Preisschildern (konfigurierbar: cols x rows pro A4)"""
    import qrcode
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from io import BytesIO
    from decimal import Decimal
    
    buffer = BytesIO()
    
    # Konfigurierbare Grid-Größe
    labels_per_page = cols * rows
    
    # Schildgröße berechnen
    margin_x = 5 * mm
    margin_y = 5 * mm
    available_width = 210 * mm - 2 * margin_x
    available_height = 297 * mm - 2 * margin_y
    
    label_width = available_width / cols
    label_height = available_height / rows
    
    # Alle Labels vorbereiten
    all_labels = []
    for item in label_data:
        for _ in range(item['count']):
            all_labels.append(item)
    
    # PDF erstellen
    c = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4
    
    # Labels zeichnen
    page_label_count = 0
    
    for label_item in all_labels:
        price = label_item['price']
        
        # Neue Seite, wenn nötig
        if page_label_count >= labels_per_page:
            c.showPage()
            page_label_count = 0
        
        # Position auf der Seite berechnen
        row = page_label_count // cols
        col = page_label_count % cols
        
        x = margin_x + col * label_width
        y = page_height - margin_y - (row + 1) * label_height
        
        # Label zeichnen
        _draw_price_label(
            c, x, y, label_width, label_height,
            seller_number, price, currency
        )
        
        page_label_count += 1
    
    # Letzte Seite speichern
    if page_label_count > 0:
        c.showPage()
    
    c.save()
    buffer.seek(0)
    return buffer


def _draw_price_label(canvas_obj, x, y, width, height, seller_number, price, currency):
    """Zeichnet ein einzelnes Preisschild mit QR-Code mit 1mm Rand"""
    import qrcode
    import tempfile
    import os
    from reportlab.lib.units import mm
    
    # Border zeichnen (dünn)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.rect(x, y, width, height, stroke=1, fill=0)
    
    # QR-Code generieren
    qr_data = f"VKNr={seller_number}&price={price}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=2,
        border=0,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # QR-Code in temporäre Datei speichern
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
        qr_img.save(tmp_file.name, format="PNG")
        qr_file_path = tmp_file.name
    
    try:
        # Layout: QR-Code links mit 3mm Rand, Preis rechts raumfüllend
        qr_margin = 3 * mm  # 3mm Rand um QR-Code
        
        # Verfügbarer Platz für QR-Code (mit Rand)
        available_qr_space = width * 0.4  # 40% der Breite für QR
        qr_size = available_qr_space - 2 * qr_margin
        
        # QR-Code Position (links mit 1mm Rand)
        qr_x = x + qr_margin
        qr_y = y + (height - qr_size) / 2  # Vertikal zentriert
        
        # QR-Code zeichnen
        canvas_obj.drawImage(
            qr_file_path, 
            qr_x, 
            qr_y, 
            width=qr_size, 
            height=qr_size,
            preserveAspectRatio=True
        )
    finally:
        # Temporäre Datei löschen
        try:
            os.unlink(qr_file_path)
        except:
            pass
    
    # Bereich für Text (rechts neben QR-Code)
    text_area_x = x + width * 0.4
    text_area_width = width * 0.6
    text_area_y = y
    text_area_height = height
    text_padding = 0.3 * mm
    
    # Optimale Schriftgröße für den verfügbaren Platz
    base_size = min(
        text_area_width * 0.3,  # Max 30% der Text-Bereichbreite
        text_area_height * 0.4   # Max 40% der Schildhöhe
    )
    
    price_font_size = max(8, int(base_size / mm * 2.8))
    vknr_font_size = max(4, int(price_font_size * 0.5))
    currency_font_size = max(4, int(price_font_size * 0.5))
    
    # Preis formatieren
    price_str = f"{price:.2f}"
    
    # Preis groß und zentriert (vertikal in der Mitte)
    canvas_obj.setFont("Helvetica-Bold", price_font_size)
    text_x = text_area_x + text_padding
    # Preis in der Mitte des Bereichs
    text_y = y + text_area_height * 0.5 - price_font_size * 0.2
    canvas_obj.drawString(text_x, text_y, price_str)
    
    # Währung neben dem Preis
    canvas_obj.setFont("Helvetica", currency_font_size)
    currency_x = text_x + len(price_str) * (price_font_size * 0.5)
    canvas_obj.drawString(currency_x + 0.2 * mm, text_y + 0.1 * mm, currency)
    
    # Verkäufernummer im unteren Bereich des Text-Bereichs
    canvas_obj.setFont("Helvetica", vknr_font_size)
    vknr_text = f"#{seller_number}"
    vknr_x = text_area_x + text_padding
    # Unten platziert, aber noch mit Abstand
    vknr_y = y + text_padding
    canvas_obj.drawString(vknr_x, vknr_y, vknr_text)


