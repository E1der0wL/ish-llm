"""Clock sections and bounded, cached FIGlet weekday rendering."""
from __future__ import annotations

from pyfiglet import Figlet

from ...rendering.presentation import TextRow
from ...rendering.text import fit, width
from ...locales import Locale
from ...locales.messages import WEEKDAYS


class ClockView:
    def __init__(self):
        self._weekday = WeekdayRenderer()

    def sections(self, date, time, weekday, now, available, locale=Locale()):
        art = tuple(TextRow.plain(line, weekday.style.color)
                    for line in self._weekday.render(weekday, now.weekday(), available))
        return ((weekday, art), (date, date.rows(now, locale)), (time, time.rows(now, locale)))


class WeekdayRenderer:
    NAMES = tuple(day.upper() for day in WEEKDAYS)
    RENDER_WIDTH = 4096

    def __init__(self):
        self._key = None
        self._lines = ()

    def render(self, config, weekday, available):
        if not config.enabled:
            return ()
        key = (config, weekday, available)
        if self._key == key:
            return self._lines
        name = self.NAMES[weekday]
        candidates = (name[:3],) if config.abbreviated else (name, name[:3])
        lines = ()
        for text in candidates:
            try:
                lines = tuple(Figlet(font=config.font, width=self.RENDER_WIDTH).renderText(text).splitlines())
            except Exception:
                lines = ()
            while lines and not lines[-1].strip():
                lines = lines[:-1]
            if lines and max(map(width, lines)) <= available:
                break
        else:
            lines = (fit(candidates[-1], available),)
        # Pad each row to the same width to preserve the art's horizontal offsets.
        block_width = max(map(width, lines), default=0)
        self._lines = tuple(line + " " * (block_width - width(line)) for line in lines)
        self._key = key
        return self._lines
