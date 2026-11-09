"""Read-only external event presentation; never constructs an editable draft."""
from datetime import timedelta

from ...core.theme import ThemeRole
from ...locales import Message as M
from ...rendering.interactive import fitted, part
from ...rendering.text import clusters, display_text, width


def detail_rows(event, columns, height, theme, locale, zone, offset=0):
    background = ThemeRole.EDITOR_BG
    p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, background=background, **kw)
    fill = lambda parts: fitted(parts, columns, theme, background=background)
    rows = [fill([p(locale.text(M.CALENDAR_EXTERNAL), ThemeRole.MUTED)]), fill([])]

    def text(value, role=ThemeRole.FOREGROUND, *, bold=False):
        for line in value.splitlines() or [""]:
            content, cells = "", 0
            for cluster in clusters(display_text(line)):
                size = width(cluster)
                if content and cells + size > columns:
                    rows.append(fill([p(content, role, bold=bold)]))
                    content, cells = "", 0
                content, cells = content + cluster, cells + size
            rows.append(fill([p(content, role, bold=bold)]))

    def divider():
        rows.extend((fill([]), fill([p("─" * columns, ThemeRole.SEPARATOR)]), fill([])))

    if event is None:
        text(locale.text(M.CALENDAR_REMOVED), ThemeRole.MUTED)
    else:
        text(event.title or locale.text(M.CALENDAR_UNTITLED), ThemeRole.TITLE, bold=True)
        text(locale.text(M.CALENDAR_SOURCE) + ": " + event.source, ThemeRole.MUTED)
        divider()
        if event.all_day:
            text(locale.text(M.ALL_DAY), ThemeRole.TITLE)
            text(f"{event.start.date()} → {(event.end - timedelta(days=1)).date()}")
        else:
            text(locale.text(M.CALENDAR_EXTERNAL_TIME, zone=zone or locale.text(M.CALENDAR_LOCAL_TIME)), ThemeRole.TITLE)
            text(event.start.isoformat(sep=" ") + " →")
            text(event.end.isoformat(sep=" "))
        for label, value in ((M.LOCATION, event.location), (M.NOTES, event.notes)):
            if value:
                divider()
                text(locale.text(label), ThemeRole.TITLE)
                text(value)
    offset = min(max(0, offset), max(0, len(rows) - height))
    return (rows[offset:offset + height] + [fill([])] * height)[:height]
