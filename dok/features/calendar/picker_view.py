"""Small date/time dropdowns; edits remain local until the owner applies them."""
from datetime import date, time
from enum import StrEnum

from ...core.theme import ThemeRole
from ...locales import Message as M
from ...popups.config import PopupRect
from ...rendering.interactive import part as fragment, button, fitted, centered
from .selection import DateSelection, TimePart, TimeSelection


class PickerAction(StrEnum):
    APPLY = "apply"
    CANCEL = "cancel"

    def translated(self, locale):
        return locale.text(M.APPLY if self == self.APPLY else M.CANCEL)


class DateFocus(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    TODAY = "today"


class DatePicker:
    WIDTH = 34
    HEIGHT = 10
    FOCUS = (*DateFocus, *PickerAction)

    def __init__(self, value: date, week_start: int, today, apply, cancel, changed, day_role):
        self.selection = DateSelection(value, week_start)
        self.focus = DateFocus.DAY
        self._today, self._apply, self._cancel, self._changed, self._day_role = today, apply, cancel, changed, day_role

    def _navigate(self, focus, amount):
        self.focus = focus
        if focus == DateFocus.YEAR:
            self.selection.years(amount)
        elif focus == DateFocus.MONTH:
            self.selection.months(amount)
        else:
            self.selection.days(amount)
        self._changed()

    def _choose(self, value):
        self.selection.select(value)
        self._apply(self.selection.value)

    def _set_focus(self, focus):
        self.focus = focus
        self._changed()

    def _activate(self, focus):
        if focus == PickerAction.CANCEL:
            self._cancel()
        elif focus == DateFocus.TODAY:
            self._choose(self._today())
        else:
            self._apply(self.selection.value)

    def key(self, key, data):
        if key == "escape":
            self._cancel()
        elif key in ("tab", "s-tab"):
            self._set_focus(self.FOCUS[(self.FOCUS.index(self.focus) + (1 if key == "tab" else -1)) % len(self.FOCUS)])
        elif key in ("enter",) or data == " ":
            self._activate(self.focus)
        elif key in ("pageup", "pagedown", "scroll-up", "scroll-down"):
            self.selection.months(-1 if key in ("pageup", "scroll-up") else 1)
        elif key == "home":
            self.selection.select(self._today())
        elif key == "end":
            self.selection.last_day()
        elif key in ("left", "right", "up", "down"):
            if self.focus == DateFocus.DAY:
                self._navigate(self.focus, {"left": -1, "right": 1,
                                           "up": -self.selection.DAYS_PER_WEEK, "down": self.selection.DAYS_PER_WEEK}[key])
            elif self.focus in (DateFocus.YEAR, DateFocus.MONTH):
                self._navigate(self.focus, -1 if key in ("left", "down") else 1)
            else:
                self._set_focus(self.FOCUS[(self.FOCUS.index(self.focus) + (-1 if key in ("left", "up") else 1)) % len(self.FOCUS)])
        self._changed()
        return True

    def scroll_at(self, x, y, amount):
        self.selection.months(1 if amount > 0 else -1)
        self._changed()

    def status_text(self, locale):
        return locale.text(M.DATE_PICKER_HINT)

    def draw(self, cells, rows, theme, locale):
        p = lambda text, role=ThemeRole.MENU_TEXT, **kw: fragment(theme, text, role, **{"background": ThemeRole.BUTTON_BG, **kw})
        fill = lambda parts: fitted(parts, cells, theme, background=ThemeRole.BUTTON_BG)
        center = lambda parts: centered(parts, cells, theme, background=ThemeRole.BUTTON_BG)
        value = self.selection.value
        nav = []
        for focus, label in ((DateFocus.YEAR, str(value.year)), (DateFocus.MONTH, locale.month(value.month))):
            if nav:
                nav.append(p(" "))
            nav.extend((button(theme, "‹", action=lambda focus=focus: self._navigate(focus, -1)),
                        button(theme, label, selected=self.focus == focus, action=lambda focus=focus: self._set_focus(focus)),
                        button(theme, "›", action=lambda focus=focus: self._navigate(focus, 1))))
        body = [center(nav)]
        weekdays = self.selection.weekdays
        cell = max(1, cells // len(weekdays))
        body.append(center([item for weekday in weekdays for item in centered(
            [p(locale.weekday(weekday, abbreviated=True), self._day_role(weekday=weekday), bold=True)], cell, theme,
            background=ThemeRole.BUTTON_BG)]))
        weeks = self.selection.weeks
        count = min(len(weeks), max(1, rows - 4))
        selected_week = next(i for i, week in enumerate(weeks) if value in week)
        start = max(0, min(selected_week - count + 1, len(weeks) - count))
        today = self._today()
        for week in weeks[start:start + count]:
            line = []
            for day in week:
                chosen = day == value
                role, bg = ((ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if chosen else
                            (self._day_role(day=day) if day else ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG))
                line.extend(centered([p(f"{day.day:02}" if day else "", role, background=bg,
                                        bold=day == today, action=(lambda day=day: self._choose(day)) if day else None)],
                                     cell, theme, background=bg))
            body.append(center(line))
        body.append(fill([]))
        footer = [button(theme, locale.text(M.TODAY), selected=self.focus == DateFocus.TODAY,
                         action=lambda: self._activate(DateFocus.TODAY)), p(" ")]
        for action in PickerAction:
            footer.extend((button(theme, action.translated(locale), role=ThemeRole.SAVE if action == PickerAction.APPLY else ThemeRole.FOREGROUND,
                                  selected=self.focus == action, action=lambda action=action: self._activate(action)), p(" ")))
        body.append(center(footer))
        return body[:rows]


class TimePicker:
    WIDTH = 30
    HEIGHT = 9
    VISIBLE_VALUES = 5
    FOCUS = (*TimePart, *PickerAction)

    def __init__(self, value: time, apply, cancel, changed):
        self.selection = TimeSelection(value)
        self.focus = TimePart.HOUR
        self._apply, self._cancel, self._changed = apply, cancel, changed
        self._columns = ()

    def _set_focus(self, focus):
        self.focus = focus
        self._changed()

    def _choose(self, part, value):
        self.focus = part
        self.selection.select(part, value)
        self._changed()

    def _activate(self, action):
        if action == PickerAction.CANCEL:
            self._cancel()
        else:
            self._apply(self.selection.value)

    def key(self, key, data):
        if key == "escape":
            self._cancel()
        elif key in ("tab", "s-tab", "left", "right"):
            self._set_focus(self.FOCUS[(self.FOCUS.index(self.focus) + (-1 if key in ("s-tab", "left") else 1)) % len(self.FOCUS)])
        elif key == "enter" or data == " ":
            self._activate(self.focus)
        elif isinstance(self.focus, TimePart):
            if key in ("up", "down", "pageup", "pagedown", "scroll-up", "scroll-down"):
                self.selection.step(self.focus, {"up": -1, "down": 1, "pageup": -self.VISIBLE_VALUES,
                                                "pagedown": self.VISIBLE_VALUES, "scroll-up": -1, "scroll-down": 1}[key])
            elif key in ("home", "end"):
                self.selection.select(self.focus, self.focus.minimum if key == "home" else self.focus.maximum)
        self._changed()
        return True

    def scroll_at(self, x, y, amount):
        for rect, part in self._columns:
            if rect.contains(x, y):
                self.focus = part
                self.selection.step(part, amount)
                self._changed()
                break

    def status_text(self, locale):
        return locale.text(M.TIME_PICKER_HINT, steps=self.VISIBLE_VALUES)

    def draw(self, cells, rows, theme, locale):
        p = lambda text, role=ThemeRole.MENU_TEXT, **kw: fragment(theme, text, role, **{"background": ThemeRole.BUTTON_BG, **kw})
        center = lambda parts: centered(parts, cells, theme, background=ThemeRole.BUTTON_BG)
        parts = tuple(TimePart) if cells >= len(TimePart) * 4 else (self.focus if isinstance(self.focus, TimePart) else TimePart.HOUR,)
        cell = max(1, cells // len(parts))
        value_rows = max(1, min(self.VISIBLE_VALUES, rows - 4))
        choices = {part: self.selection.window(part, value_rows) for part in parts}
        self._columns = tuple((PopupRect(index * cell, 1, cell, value_rows + 1), part) for index, part in enumerate(parts))
        body = [center([p(self.selection.value.isoformat(timespec="seconds"), ThemeRole.TITLE, bold=True)])]
        body.append([item for part in parts for item in centered(
            [p(part.translated(locale), ThemeRole.TITLE if self.focus == part else ThemeRole.MENU_TEXT,
               action=lambda part=part: self._set_focus(part))], cell, theme, background=ThemeRole.BUTTON_BG)])
        for row in range(value_rows):
            line = []
            for part in parts:
                value = choices[part][row]
                current = value == self.selection.current(part)
                active = current and self.focus == part
                role, bg = ((ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if active else
                            (ThemeRole.TITLE if current else ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG))
                line.extend(centered([p(f"{value:02}", role, background=bg, bold=current,
                                        action=lambda part=part, value=value: self._choose(part, value))],
                                     cell, theme, background=bg))
            body.append(line)
        body.append(center([]))
        footer = []
        for action in PickerAction:
            if footer:
                footer.append(p("  "))
            footer.append(button(theme, action.translated(locale), role=ThemeRole.SAVE if action == PickerAction.APPLY else ThemeRole.FOREGROUND,
                                 selected=self.focus == action, action=lambda action=action: self._activate(action)))
        body.append(center(footer))
        return [fitted(row, cells, theme, background=ThemeRole.BUTTON_BG) for row in body[:rows]]
