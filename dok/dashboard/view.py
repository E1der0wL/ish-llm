"""Compose feature-owned widgets and maximize-only information rows."""

from ..core.state import DockMode
from ..core.theme import ThemeRole
from ..features.clock.view import ClockView
from ..features.weather.view import WeatherPresentation
from ..rendering.presentation import DockLine, RenderContext, TextRow, swallow_mouse
from ..rendering.text import width
from .widgets import body_widgets
from ..locales import Locale


class DashboardView:
    def __init__(self):
        self._clock = ClockView()

    def rows(self, config, snapshot, available, *, max_rows=None, locale=Locale()):
        now = config.clock.now()
        sections = (
            (config.widgets.weather, WeatherPresentation.rows(
                config.widgets.weather, snapshot, available, today=now.date(), locale=locale
            )),
            *self._clock.sections(
                config.widgets.date,
                config.widgets.time,
                config.widgets.weekday,
                now,
                available,
                locale
            ),
        )
        if max_rows is not None and sum(element.row_count(len(rows), DockMode.MAXIMIZED)
                                        for element, rows in sections) > max_rows:
            return None
        result = []
        for element, rows in sections:
            result.extend(element.arrange(rows, DockMode.MAXIMIZED, TextRow()))
        return tuple(result)


class DockView:
    def __init__(self, execute, media=None):
        self._execute = execute
        self._media = media

    def render(self, config, snapshot, audio, diagnostic, total_width, body_width,
               dashboard_rows=(), mode=DockMode.BASIC, *, locale=Locale()):
        context = RenderContext(snapshot, audio, diagnostic, body_width, config.theme, locale)
        lines = []
        for row, content in enumerate(dashboard_rows):
            content = content.fitted(body_width)
            occupied = content.cells
            left = (total_width - body_width) // 2 + (body_width - occupied) // 2
            fragments = tuple((config.theme.span(span.color, bold=span.bold), span.text, swallow_mouse)
                              for span in content.spans)
            lines.append(DockLine(row, left, occupied, fragments))
        for widget in body_widgets(config, self._execute, self._media):
            rows = widget.rows(mode)
            if not rows:
                continue
            content = widget.view.render(context)
            occupied = sum(width(part[1]) for part in content)
            left = (total_width - body_width) // 2 + (body_width - occupied) // 2
            lines.append(DockLine(len(lines), left, occupied, content))
            for _ in rows[1:]:
                blank_left = (total_width - body_width) // 2 + body_width // 2
                lines.append(DockLine(len(lines), blank_left, 0, ()))
        if config.theme.resolve(ThemeRole.BACKGROUND) is not None:
            panel_left = (total_width - body_width) // 2
            filled = []
            for line in lines:
                before = max(0, line.left - panel_left)
                after = max(0, body_width - before - line.width)
                padding = config.theme.paint()
                fragments = ((padding, " " * before, swallow_mouse), *line.fragments,
                             (padding, " " * after, swallow_mouse))
                filled.append(DockLine(line.row, panel_left, body_width, fragments))
            lines = filled
        return tuple(lines)
