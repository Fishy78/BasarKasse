from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .emailing import send_verification_email
from .forms import AdminUserCreationForm
from .models import (
    BazaarSettlement,
    Receipt,
    ReceiptItem,
    SellerRegistration,
    User,
    UserConsent,
)

admin.site.site_header = _("Basarverwaltung")
admin.site.site_title = _("Basarverwaltung")
admin.site.index_title = _("Verwaltung")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = AdminUserCreationForm
    add_form_template = "admin/core/user/add_form.html"
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "is_anonymized", "is_active", "is_cashier", "is_staff"]
    list_filter = ["is_active", "is_cashier", "is_staff"]
    search_fields = ["email", "first_name", "last_name", "phone_number"]
    readonly_fields = ["is_anonymized"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        (_("Kontaktdaten"), {"fields": ["first_name", "last_name", "phone_number", "address_street", "address_postal_code", "address_city", "is_anonymized"]}),
        (_("Berechtigungen"), {"fields": ["is_active", "is_cashier", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        (_("Zeitstempel"), {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": [
                    "email",
                    "first_name",
                    "last_name",
                    "phone_number",
                    "address_street",
                    "address_postal_code",
                    "address_city",
                    "password1",
                    "password2",
                    "consent",
                    "is_cashier",
                    "is_staff",
                ],
            },
        ),
    ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and form.cleaned_data.get("consent"):
            global_settings = form.global_settings
            UserConsent.objects.create(
                user=obj,
                version=global_settings.consent_version,
                text=global_settings.consent_text,
                accepted_at=timezone.now(),
            )
            if global_settings.email_confirmation_required:
                send_verification_email(request, obj)


@admin.register(SellerRegistration)
class SellerRegistrationAdmin(admin.ModelAdmin):
    list_display = ["bazaar", "seller_number", "seller"]
    list_filter = ["bazaar"]
    search_fields = ["seller_number", "seller__email"]


@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = ["user", "version", "accepted_at"]
    list_filter = ["version"]
    search_fields = ["user__email"]
    readonly_fields = ["user", "version", "text", "accepted_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 0
    readonly_fields = ["registration", "price", "created_at", "cancelled_at", "cancelled_by"]


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ["bazaar", "receipt_number", "cashier", "status", "created_at", "first_item_at"]
    list_filter = ["bazaar", "status"]
    readonly_fields = ["bazaar", "cashier", "receipt_number", "status", "created_at", "first_item_at", "completed_at"]
    inlines = [ReceiptItemInline]


@admin.register(BazaarSettlement)
class BazaarSettlementAdmin(admin.ModelAdmin):
    list_display = ["bazaar", "created_at", "cancelled_at", "cancelled_by"]
    readonly_fields = ["bazaar", "created_at", "snapshot", "pdf", "cancelled_at", "cancelled_by"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
