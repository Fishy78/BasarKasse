from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.translation import gettext as _


CONSENT_SALT = "core.seller-consent"


def send_verification_email(request, user):
    token = signing.dumps(user.pk, salt=CONSENT_SALT)
    verify_url = request.build_absolute_uri(
        reverse("verify_email", args=[user.pk, token])
    )
    send_mail(
        _("Basar-Konto bestätigen"),
        _("Bitte bestätige deine E-Mail-Adresse über diesen Link: %(verify_url)s")
        % {"verify_url": verify_url},
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
