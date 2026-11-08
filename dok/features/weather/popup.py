"""Daily forecast details reuse the weather owner's snapshot and settings."""
from dataclasses import dataclass
from datetime import date

from ...core.records import Boolean, Nested, Nullable, Number, Record, setting
from ...core.theme import ThemeRole
from ...integrations.ish.keys import KeyRule, KeySequence
from ...popups.config import PopupPanel, PopupDimension, PopupHeight
from ...popups.models import PopupAction, PopupContent
from ...rendering.presentation import TextRow, TextSpan
from .models import WeatherSnapshot, WeatherHour
from ..clock.config import FontRule
from ...rendering.interactive import InteractivePopup, part, fitted, centered, button
from ...rendering.text import fit, width
from pyfiglet import Figlet, CharNotPrinted
from ...locales import Locale, Message as M


@dataclass(frozen=True, slots=True)
class WeatherWidth(PopupDimension):
    value: int | float = setting(Number(), default=112)


@dataclass(frozen=True, slots=True)
class WeatherHeight(PopupHeight):
    value: int | float = setting(Number(), default=36)


@dataclass(frozen=True, slots=True)
class WeatherPanel(PopupPanel):
    width: WeatherWidth = setting(Nested(WeatherWidth), factory=WeatherWidth)
    height: WeatherHeight = setting(Nested(WeatherHeight), factory=WeatherHeight)


@dataclass(frozen=True, slots=True)
class WeatherPopupConfig(Record):
    enabled: bool = setting(Boolean(), default=True, description="Enable current, daily and hourly weather details, sharing the dashboard cache.")
    key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "w"), description="Open weather details. Default Alt+W; null disables this binding.")
    panel: WeatherPanel | None = setting(Nullable(Nested(WeatherPanel)), factory=WeatherPanel, description="Weather panel dimensions. null uses popups.general.")
    font: str = setting(FontRule(), default="future", description="pyfiglet font for current temperature; plain text is used when the art cannot fit.")
    hour_width: int = setting(Number(minimum=1, integer=True), default=8, description="Width of each hourly column, fitted to the viewport.")
    graph_height: int = setting(Number(minimum=1, integer=True), default=5, description="Temperature graph height, fitted to the viewport.")


class WeatherPage(InteractivePopup):
    def __init__(self, snapshot, config, clock, day=None, popup=WeatherPopupConfig, invalidate=lambda: None, *, locale=Locale):
        if day is not None and type(day) is not date:
            raise TypeError("Weather day must be a date or None")
        self._snapshot, self._config, self._clock = snapshot, config, clock
        self._day = day
        self._popup, self._invalidate = popup, invalidate
        self._locale = locale
        self._offset = None
        self._selected = None
        self._selected_time = None
        self._buttons = False
        self._button = 1
        self._scroll = 0
        self._step = 1
        self._art_key = None
        self._art = ()
        self._hours = ()
        self._graph_cache = None

    def _days(self, config, snapshot):
        return snapshot.days or WeatherSnapshot.unknown(self._clock().now().date(), config.days).days

    def _move(self, amount):
        days = self._days(self._config(), self._snapshot())
        index = next((index for index, day in enumerate(days) if day.day == self._day), 0)
        self._day = days[min(len(days) - 1, max(0, index + amount))].day

    def content(self):
        config, snapshot = self._config(), self._snapshot()
        locale = self._locale()
        days = self._days(config, snapshot)
        day = next((day for day in days if day.day == self._day), days[0])
        high, low = day.temperature_parts
        rows = [TextRow.plain(locale.date(day.day), "", bold=True),
                TextRow.plain(f"{config.icons.for_kind(day.kind)}  {day.kind.label(locale)}", ""),
                TextRow((TextSpan(locale.text(M.HIGH) + f" {high}   ", ThemeRole.DELETE), TextSpan(locale.text(M.LOW) + f" {low}", config.style.low_color))),
                TextRow.plain(locale.text(M.UNIT, unit=config.unit.symbol), "")]
        rows.extend(self._status_rows(config, snapshot, locale))
        return PopupContent(locale.text(M.WEATHER), tuple(rows), (
            PopupAction(locale.text(M.PREVIOUS_DAY), lambda: self._move(-1)),
            PopupAction(locale.text(M.NEXT_DAY), lambda: self._move(1)),
        ))

    @staticmethod
    def _status_rows(config, snapshot, locale):
        if config.location is None:
            return (TextRow.plain(locale.text(M.SET_LOCATION), ""),)
        if not config.enabled:
            return (TextRow.plain(locale.text(M.WEATHER_DISABLED), ""),)
        if snapshot.error:
            return (TextRow.plain(locale.text(M.CACHED_WEATHER, message=locale.text(M.WEATHER_FAILED)), ThemeRole.WARNING),)
        if not snapshot.days:
            return (TextRow.plain(locale.text(M.WEATHER_LOADING), ""),)
        return ()

    def _navigate(self, key):
        if key == "home":
            self._offset, self._day, self._scroll = None, None, 0
            self._selected = self._selected_time = None
        elif key in ("left", "right", "pageup", "pagedown"):
            count = len(self._snapshot().hours)
            change = (-1 if key in ("left", "pageup") else 1) * (self._step if key in ("pageup", "pagedown") else 1)
            self._selected = max(0, min(max(0, count - 1), (self._selected or 0) + change))
            self._selected_time = None
        self._invalidate()

    def key(self, key, data):
        if key == "escape":
            return False
        if key in ("tab", "s-tab"):
            self._buttons = not self._buttons
        elif self._buttons and key in ("left", "right"):
            self._button = (self._button + (-1 if key == "left" else 1)) % 3
        elif self._buttons and (key == "enter" or data == " "):
            self._navigate(("pageup", "home", "pagedown")[self._button])
        elif key in ("home", "left", "right", "pageup", "pagedown"):
            self._navigate(key)
        elif key in ("up", "down", "scroll-up", "scroll-down"):
            self._scroll = max(0, self._scroll + (-1 if key in ("up", "scroll-up") else 1))
        return True

    def status_text(self):
        locale = self._locale()
        if self._buttons:
            return locale.text(M.WEATHER_BUTTON_HINT)
        stamp = self._selected_time.strftime(" %m/%d %H:%M") if self._selected_time else ""
        return locale.text(M.WEATHER_HOUR_HINT, stamp=stamp)

    def _select_hour(self, index):
        self._selected, self._selected_time, self._buttons = index, None, False
        self._invalidate()

    def _click_button(self, index):
        self._button, self._buttons = index, True
        self._navigate(("pageup", "home", "pagedown")[index])

    @staticmethod
    def graph(values, cell, height):
        columns = len(values) * cell
        canvas = [[" "] * columns for _ in range(height)]
        actual = [v for v in values if v is not None]
        if not actual:
            return ["".join(row) for row in canvas]
        low, high = min(actual), max(actual)
        points = [(min(columns - 1, index * cell + cell // 2),
                   round((high - value) / (high - low) * (height - 1)) if high != low else height // 2)
                  if value is not None else None for index, value in enumerate(values)]
        for point in points:
            if point:
                canvas[point[1]][point[0]] = "─"
        for a, b in zip(points, points[1:]):
            if a is None or b is None:
                continue
            x0, y0 = a
            x1, y1 = b
            middle = (x0 + x1) // 2
            for x in range(x0, middle + 1):
                canvas[y0][x] = "─"
            for x in range(middle, x1 + 1):
                canvas[y1][x] = "─"
            if y0 != y1:
                canvas[y0][middle] = "╯" if y1 < y0 else "╮"
                canvas[y1][middle] = "╭" if y1 < y0 else "╰"
                for y in range(min(y0, y1) + 1, max(y0, y1)):
                    canvas[y][middle] = "│"
        return ["".join(row) for row in canvas]

    def draw_body(self, columns, rows, theme):
        if columns <= 0 or rows <= 0:
            return []
        config, snapshot, options = self._config(), self._snapshot(), self._popup()
        locale = self._locale()
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **kw)
        body = []
        def line(text="", role=ThemeRole.FOREGROUND, bold=False):
            body.append(centered([p(text, role, bold=bold)], columns, theme))
        location = config.location.label or f"{config.location.lat:g}, {config.location.lon:g}" if config.location else locale.text(M.LOCATION_MISSING)
        line(location, bold=True)
        current = snapshot.current
        value = "--" if current is None or current.temperature is None else f"{current.temperature:.0f}"
        key = (value, options.font, columns)
        if key != self._art_key:
            self._art_key = key
            try:
                art = Figlet(font=options.font, width=max(1, columns)).renderText(value).rstrip("\n").splitlines()
            except CharNotPrinted:
                art = [value]
            self._art = art if art and max(map(width, art)) + 3 <= columns else [value]
        for index, text in enumerate(self._art):
            line(text + (" " + config.unit.symbol if index == min(1, len(self._art) - 1) else "   "), ThemeRole.TITLE, True)
        days = self._days(config, snapshot)
        day = next((d for d in days if d.day == self._day), days[0])
        high, low = day.temperature_parts
        body.append(centered([p(locale.text(M.HIGH) + " " + high, ThemeRole.DELETE), p("  /  ", ThemeRole.SEPARATOR), p(locale.text(M.LOW) + " " + low, config.style.low_color)], columns, theme))
        apparent = "--°" if current is None or current.apparent is None else f"{current.apparent:.0f}°"
        line(locale.text(M.FEELS_LIKE) + " " + apparent, ThemeRole.MUTED)
        line()
        body.append([p("─" * columns, ThemeRole.SEPARATOR)])
        label_width = min(max(10, width(locale.text(M.TEMPERATURE)), width(locale.text(M.RAIN_CHANCE))), max(0, columns // 5))
        cell = min(options.hour_width, max(1, columns - label_width))
        count = max(1, (columns - label_width) // cell)
        self._step = count
        if self._selected_time is not None and snapshot.hours is not self._hours:
            self._selected = next((i for i, hour in enumerate(snapshot.hours) if hour.time == self._selected_time), self._selected)
        self._hours = snapshot.hours
        if self._selected is None:
            if self._day:
                self._selected = next((i for i, h in enumerate(snapshot.hours) if h.time.date() == self._day), 0)
            else:
                now = current.time if current else self._clock().now()
                self._selected = next((i for i, h in enumerate(snapshot.hours) if h.time.timestamp() >= now.timestamp()), 0)
        self._selected = max(0, min(self._selected, max(0, len(snapshot.hours) - 1)))
        selected = snapshot.hours[self._selected] if snapshot.hours else None
        self._selected_time = selected.time if selected else None
        self._offset = self._selected if self._offset is None else self._offset
        if self._selected < self._offset:
            self._offset = self._selected
        elif self._selected >= self._offset + count:
            self._offset = self._selected - count + 1
        self._offset = max(0, min(self._offset, max(0, len(snapshot.hours) - count)))
        hours = snapshot.hours[self._offset:self._offset + count]
        def hourly(label, texts):
            parts = fitted([p(locale.text(label), ThemeRole.MUTED)], label_width, theme)
            for index, (text, role) in enumerate(texts):
                selected_cell = index + self._offset == self._selected and label == M.HOURS
                parts.extend(centered([p(text, ThemeRole.TITLE if selected_cell else role,
                                         bold=selected_cell, action=lambda index=index: self._select_hour(self._offset + index))], cell, theme))
            body.append(fitted(parts, columns, theme))
        line(locale.date(selected.time.date()) if selected else "--", ThemeRole.MUTED, True)
        hourly(M.HOURS, [(h.time.strftime("%H:%M"), ThemeRole.MUTED) for h in hours] or [("--:--", ThemeRole.MUTED)])
        hourly(M.WEATHER, [(config.icons.for_kind(h.kind), ThemeRole.FOREGROUND) for h in hours] or [(config.icons.unknown, ThemeRole.FOREGROUND)])
        hourly(M.TEMPERATURE, [("--°" if h.temperature is None else f"{h.temperature:.0f}°", ThemeRole.FOREGROUND) for h in hours] or [("--°", ThemeRole.FOREGROUND)])
        height = min(options.graph_height, max(1, rows))
        values = [h.temperature for h in hours]
        graph_key = tuple(values), cell, height
        if self._graph_cache is None or self._graph_cache[0] != graph_key:
            self._graph_cache = graph_key, self.graph(values, cell, height)
        for graph in self._graph_cache[1]:
            body.append(fitted([p(" " * label_width), p(graph, ThemeRole.TITLE)], columns, theme))
        hourly(M.RAIN_CHANCE, [("--%" if h.precipitation_probability is None else f"{h.precipitation_probability:g}%",
                            ThemeRole.MUTED if h.precipitation_probability in (None, 0) else ThemeRole.FOREGROUND) for h in hours] or [("--%", ThemeRole.MUTED)])
        line()
        for index, (icon, label, value) in enumerate(WeatherHour.details(selected, config.unit, locale)):
            if index:
                body.append([p("─" * columns, ThemeRole.SEPARATOR)])
            name = f"{icon}   {label}"
            room = max(0, columns - width(value) - 1)
            name = fit(name, room, marker="")
            body.append(fitted([p(name), p(" " * max(0, columns - width(name) - width(value))), p(value)], columns, theme))
        for row in self._status_rows(config, snapshot, locale):
            line(row.text, row.spans[0].color if row.spans else ThemeRole.FOREGROUND)
        footer_height = min(3, max(0, rows - 1))
        content_height = max(1, rows - footer_height)
        self._scroll = min(self._scroll, max(0, len(body) - content_height))
        visible = body[self._scroll:self._scroll + content_height]
        visible += [fitted([], columns, theme) for _ in range(content_height - len(visible))]
        buttons = []
        for index, label in enumerate(("<", locale.text(M.NOW), ">")):
            if buttons:
                buttons.append(p("  "))
            buttons.append(button(theme, label, selected=self._buttons and index == self._button,
                                  action=lambda index=index: self._click_button(index)))
        footer = [fitted([], columns, theme), centered(buttons, columns, theme), fitted([], columns, theme)]
        if footer_height < 3:
            footer = footer[1:]
        return (visible + footer[:footer_height])[:rows]
