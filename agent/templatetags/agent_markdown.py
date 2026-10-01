import re
from html import escape

import markdown
from django import template
from django.utils.safestring import mark_safe

from agent.presentation import without_generated_sources


register = template.Library()


TABLE_DELIMITER_CELL = re.compile(r"^:?-{3,}:?$")


def _table_cells(line):
    content = line.rstrip("\r\n")
    if "\t" in content or len(content) - len(content.lstrip(" ")) > 3:
        return None
    content = content.strip()
    if "|" not in content:
        return None
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|"):
        content = content[:-1]
    cells = tuple(cell.strip() for cell in content.split("|"))
    return cells if len(cells) >= 2 and all(cells) else None


def _is_table_start(header, delimiter):
    header_cells = _table_cells(header)
    delimiter_cells = _table_cells(delimiter)
    return bool(
        header_cells
        and delimiter_cells
        and len(header_cells) == len(delimiter_cells)
        and all(TABLE_DELIMITER_CELL.fullmatch(cell) for cell in delimiter_cells)
    )


def _line_ending(line, default):
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return default


def _normalize_table_boundaries(value):
    lines = value.splitlines(keepends=True)
    default_ending = "\r\n" if "\r\n" in value else "\n"
    normalized = []
    for index, line in enumerate(lines):
        if (
            index > 0
            and index + 1 < len(lines)
            and lines[index - 1].strip()
            and _is_table_start(line, lines[index + 1])
        ):
            normalized.append(_line_ending(line, default_ending))
        normalized.append(line)
    return "".join(normalized)


@register.filter
def safe_markdown(value):
    """Render Markdown after escaping every HTML fragment supplied by the model."""
    escaped = escape(str(value or ""))
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    escaped = _normalize_table_boundaries(escaped)
    rendered = markdown.markdown(escaped, extensions=["sane_lists", "tables"])
    return mark_safe(rendered)


@register.filter
def without_sources(value):
    return without_generated_sources(str(value or ""))
