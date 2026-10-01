from html import escape

import markdown
from django import template
from django.utils.safestring import mark_safe

from agent.presentation import without_generated_sources


register = template.Library()


@register.filter
def safe_markdown(value):
    """Render Markdown after escaping every HTML fragment supplied by the model."""
    escaped = escape(str(value or ""))
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    rendered = markdown.markdown(escaped, extensions=["sane_lists", "tables"])
    return mark_safe(rendered)


@register.filter
def without_sources(value):
    return without_generated_sources(str(value or ""))
