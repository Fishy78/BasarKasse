from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    UserAttributeSimilarityValidator,
)
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _, ngettext


class GlobalPasswordValidator:
    def validate(self, password, user=None):
        from .models import GlobalSettings

        settings = GlobalSettings.load()
        errors = []
        if len(password) < settings.password_min_length:
            errors.append(
                ngettext(
                    "Das Passwort muss mindestens %(count)d Zeichen lang sein.",
                    "Das Passwort muss mindestens %(count)d Zeichen lang sein.",
                    settings.password_min_length,
                )
                % {"count": settings.password_min_length}
            )
        if settings.password_require_uppercase and not any(char.isupper() for char in password):
            errors.append(_("Das Passwort muss mindestens einen Großbuchstaben enthalten."))
        if settings.password_require_lowercase and not any(char.islower() for char in password):
            errors.append(_("Das Passwort muss mindestens einen Kleinbuchstaben enthalten."))
        if settings.password_require_number and not any(char.isdigit() for char in password):
            errors.append(_("Das Passwort muss mindestens eine Ziffer enthalten."))
        if settings.password_require_special and not any(
            not char.isalnum() for char in password
        ):
            errors.append(_("Das Passwort muss mindestens ein Sonderzeichen enthalten."))
        if settings.password_block_common:
            try:
                CommonPasswordValidator().validate(password, user)
            except ValidationError:
                errors.append(_("Dieses Passwort ist zu häufig und darf nicht verwendet werden."))
        if settings.password_block_numeric_only and password.isdigit():
            errors.append(_("Das Passwort darf nicht ausschließlich aus Zahlen bestehen."))
        if settings.password_check_similarity:
            try:
                UserAttributeSimilarityValidator().validate(password, user)
            except ValidationError:
                errors.append(_("Das Passwort ähnelt zu stark deinen Benutzerdaten."))
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        from .models import GlobalSettings

        settings = GlobalSettings.load()
        rules = [
            ngettext(
                "Mindestens %(count)d Zeichen",
                "Mindestens %(count)d Zeichen",
                settings.password_min_length,
            )
            % {"count": settings.password_min_length}
        ]
        if settings.password_require_uppercase:
            rules.append(_("ein Großbuchstabe"))
        if settings.password_require_lowercase:
            rules.append(_("ein Kleinbuchstabe"))
        if settings.password_require_number:
            rules.append(_("eine Ziffer"))
        if settings.password_require_special:
            rules.append(_("ein Sonderzeichen"))
        if settings.password_block_common:
            rules.append(_("kein häufig verwendetes Passwort"))
        if settings.password_block_numeric_only:
            rules.append(_("nicht ausschließlich Zahlen"))
        if settings.password_check_similarity:
            rules.append(_("nicht zu ähnlich zu deinen Benutzerdaten"))
        return _("Passwortregeln: %(rules)s.") % {"rules": "; ".join(rules)}
