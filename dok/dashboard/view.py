"""Compose feature-owned widgets and maximize-only information rows."""

from ..core.state import DockMode
from ..features.clock.view import ClockView
from ..features.weather.view import WeatherPresentation
from ..rendering.presentation import DockLine, DockStyle, RenderContext, TextRow, swallow_mouse
from ..rendering.text import width
from .widgets import body_widgets


class DashboardView:
    def __init__(self):
        self._clock = ClockView()

    def rows(self, config, snapshot, available, *, max_rows=None):
        now = config.clock.now()
        sections = (
            (config.widgets.weather, WeatherPresentation.rows(
                config.widgets.weather, snapshot, available, today=now.date()
            )),
            *self._clock.sections(
                config.widgets.date,
                config.widgets.time,
                config.widgets.weekday_art,
                now,
                available
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
               dashboard_rows=(), mode=DockMode.BASIC):
        context = RenderContext(snapshot, audio, diagnostic, body_width)
        lines = []
        for row, content in enumerate(dashboard_rows):
            content = content.fitted(body_width)
            occupied = content.cells
            left = (total_width - body_width) // 2 + (body_width - occupied) // 2
            fragments = tuple((DockStyle.foreground(span.color, bold=span.bold), span.text, swallow_mouse)
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
        return tuple(lines)
