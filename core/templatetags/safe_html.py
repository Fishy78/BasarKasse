import bleach
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "i",
    "h2",
    "h3",
    "h4",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "u",
    "ul",
}


@register.filter
def safe_html(value):
    cleaned = bleach.clean(
        str(value or ""),
        tags=ALLOWED_TAGS,
        attributes={"a": ["href", "title"]},
        protocols=["http", "https", "mailto"],
        strip=True,
        strip_comments=True,
    )
    return mark_safe(cleaned)
