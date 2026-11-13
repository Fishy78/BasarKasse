from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", views.home, name="home"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/consent/", views.accept_consent, name="accept_consent"),
    path("accounts/profile/", views.profile_edit, name="profile_edit"),
    path("accounts/delete/", views.delete_account, name="delete_account"),
    path("accounts/resend-verification/", views.resend_verification, name="resend_verification"),
    path("accounts/verify/<int:user_id>/<str:token>/", views.verify_email, name="verify_email"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("cookies/consent/", views.cookie_consent, name="cookie_consent"),
    path("datenschutz/", views.privacy_policy, name="privacy_policy"),
    path(
        "accounts/password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html"
        ),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html"
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("role/", views.choose_role, name="choose_role"),
    path("verwaltung/", views.management_home, name="management_home"),
    path(
        "verwaltung/statistiken/",
        views.management_statistics,
        name="management_statistics",
    ),
    path(
        "verwaltung/statistiken/basar/<int:bazaar_id>/",
        views.bazaar_statistics,
        name="bazaar_statistics",
    ),
    path(
        "verwaltung/statistiken/basar/<int:bazaar_id>/rechnung/<int:receipt_id>/",
        views.bazaar_receipt_detail,
        name="bazaar_receipt_detail",
    ),
    path("verwaltung/einstellungen/", views.manage_settings, name="manage_settings"),
    path(
        "verwaltung/rechnungsvorlagen/",
        views.manage_seller_invoice_templates,
        name="manage_seller_invoice_templates",
    ),
    path(
        "verwaltung/rechnungsvorlagen/neu/",
        views.manage_seller_invoice_template,
        name="manage_seller_invoice_template_create",
    ),
    path(
        "verwaltung/rechnungsvorlagen/<int:template_id>/",
        views.manage_seller_invoice_template,
        name="manage_seller_invoice_template_edit",
    ),
    path(
        "verwaltung/rechnungsvorlagen/<int:template_id>/loeschen/",
        views.delete_seller_invoice_template,
        name="delete_seller_invoice_template",
    ),
    path("verwaltung/basare/", views.manage_bazaars, name="manage_bazaars"),
    path("verwaltung/basare/neu/", views.manage_bazaar, name="manage_bazaar_create"),
    path(
        "verwaltung/basare/<int:bazaar_id>/",
        views.manage_bazaar,
        name="manage_bazaar_edit",
    ),
    path(
        "verwaltung/benutzer/",
        views.user_management_list,
        name="user_management_list",
    ),
    path(
        "verwaltung/benutzer/<int:user_id>/",
        views.user_management_edit,
        name="user_management_edit",
    ),
    path(
        "verwaltung/benutzer/<int:user_id>/loeschen/",
        views.user_anonymize,
        name="user_anonymize",
    ),
    path("seller/", views.seller_dashboard, name="seller_dashboard"),
    path(
        "seller/bazaar/<int:bazaar_id>/",
        views.seller_bazaar_details,
        name="seller_bazaar_details",
    ),
    path("seller/register/<int:bazaar_id>/", views.join_bazaar, name="join_bazaar"),
    path(
        "seller/bazaar/<int:bazaar_id>/price-labels/",
        views.seller_price_labels_form,
        name="seller_price_labels_form",
    ),
    path(
        "seller/bazaar/<int:bazaar_id>/price-labels/generate/",
        views.seller_price_labels_generate,
        name="seller_generate_price_labels",
    ),
    path("cashier/", views.cashier, name="cashier"),
    path("cashier/receipt/<int:receipt_id>/add/", views.add_item, name="add_item"),
    path("cashier/receipt/<int:receipt_id>/item/<int:item_id>/cancel/", views.cancel_item, name="cancel_item"),
    path("cashier/receipt/<int:receipt_id>/finish/", views.finish_receipt, name="finish_receipt"),
    path("cashier/next/", views.next_sale, name="next_sale"),
    path("cashier/complete/<int:receipt_id>/", views.receipt_complete, name="receipt_complete"),
    path("settlements/<int:bazaar_id>/", views.settlement_start, name="settlement_start"),
    path("settlements/<int:bazaar_id>/pdf/", views.settlement_pdf, name="settlement_pdf"),
    path("settlements/<int:bazaar_id>/cancel/", views.settlement_cancel, name="settlement_cancel"),
    path(
        "settlements/history/<int:settlement_id>/download/",
        views.settlement_history_download,
        name="settlement_history_download",
    ),
    path(
        "settlements/<int:bazaar_id>/download/",
        views.settlement_download,
        name="settlement_download",
    ),
    # QR-Code Scanner
    path("scanner/generate/", views.scanner_generate_code, name="scanner_generate"),
    path("scanner/join/", views.scanner_join, name="scanner_join"),
    path("api/scanner/submit/", views.scanner_submit, name="scanner_submit"),
    path("api/scanner/pending/", views.scanner_pending, name="scanner_pending"),
    path("api/scanner/close/", views.scanner_close, name="scanner_close"),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
