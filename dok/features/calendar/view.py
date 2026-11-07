"""Interactive month and event editor, using the calendar runtime's typed API."""
import asyncio
import calendar
from datetime import date
from enum import StrEnum
from ...locales import Locale, Message as M

from ...core.theme import ThemeRole, ThemeColor
from ...popups.models import PopupAction, PopupContent
from ...rendering.interactive import InteractivePopup, part, fitted, centered, cropped, button
from ...rendering.presentation import TextRow
from ...rendering.text import fit, width
from .editor import EditorField, EventDraft
from .models import RepeatUnit
from .store import CalendarSnapshot
from ...locales.messages import WEEKDAYS


class CalendarColors:
    @staticmethod
    def role(weekday: int, *, holiday=False, default=ThemeRole.FOREGROUND):
        if holiday:
            return ThemeRole.HOLIDAY
        return ThemeRole.SUNDAY if weekday == calendar.SUNDAY else ThemeRole.SATURDAY if weekday == calendar.SATURDAY else default


class Confirmation(StrEnum):
    SAVE = "save"
    DELETE = "delete"

    def message(self, locale):
        return locale.text(M.DELETE_SERIES if self == self.DELETE else M.SAVE_CHANGES)


class CalendarPage(InteractivePopup):
    WEEKDAYS = tuple(day[:3] for day in WEEKDAYS)
    EVENT_ROWS = 4
    TIME_ARROW = ""

    def __init__(self, clock, config, runtime=None, invalidate=lambda: None, dismiss=lambda: None, *, locale=Locale):
        self._clock, self._config, self.runtime = clock, config, runtime
        self._locale = locale
        self._invalidate, self._dismiss = invalidate, dismiss
        self.selected = clock().now().date()
        self._month = self.selected.replace(day=1)
        self.editing = False
        self.field = EditorField.TITLE
        self.draft = None
        self.event_index = 0
        self.dropdown = None
        self.menu_index = 0
        self.confirm = None
        self.confirm_index = 0
        self._pending = None
        self.error = ""
        self._task = None
        self._scroll = 0
        self._preview_key = None
        self._preview = None
        self._field_rows = {}
        self._field_columns = {}
        self._empty_snapshot = CalendarSnapshot()

    @property
    def snapshot(self):
        return self.runtime.snapshot if self.runtime else self._empty_snapshot

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    def _guard(self, action):
        if self.draft and self.draft.dirty:
            self.confirm, self._pending, self.confirm_index = Confirmation.SAVE, action, 0
        else:
            action()
        self._invalidate()

    def request_close(self):
        if self.busy:
            return False
        if self.draft and self.draft.dirty:
            self._guard(self._dismiss)
            return False
        return True

    def dispose(self):
        if self.busy:
            self._task.cancel()

    def _select(self, day):
        def change():
            self.selected, self._month = day, day.replace(day=1)
            self.draft, self.event_index, self._scroll = None, 0, 0
            self.editing = False
        self._guard(change)

    def _move(self, amount):
        index = (self._month.year - 1) * 12 + self._month.month - 1 + amount
        index = min(date.max.year * 12 - 1, max(0, index))
        year, month = index // 12 + 1, index % 12 + 1
        self._select(date(year, month, min(self.selected.day, calendar.monthrange(year, month)[1])))

    def _today(self):
        self._select(self._clock().now().date())

    def _begin(self, *, new=False):
        if self.draft is None or new:
            events = self.snapshot.on_day(self.selected)
            event = self.snapshot.get(events[min(self.event_index, len(events) - 1)].id) if events and not new else None
            self.draft = EventDraft(self.selected, getattr(self._clock(), "timezone", None), event)
        self.editing, self.field = True, EditorField.TITLE
        self._invalidate()

    def _new(self):
        self._guard(lambda: self._begin(new=True))

    def _focus(self, field):
        if not self.editing:
            self._begin()
        self.field = field
        self._invalidate()

    def _next_field(self, amount):
        fields = self.draft.fields()
        self.field = fields[(fields.index(self.field) + amount) % len(fields)]

    def _save(self, *, delete=False):
        if self.busy or self.runtime is None:
            return
        try:
            event = self.draft.original if delete else self.draft.event()
            if event is None:
                return
        except ValueError as exc:
            self.error = str(exc)
            return
        async def save():
            try:
                if delete:
                    await self.runtime.delete(event)
                    self.draft = None
                    self.editing = False
                else:
                    saved = await self.runtime.save(event)
                    self.draft = EventDraft(self.selected, saved.timezone, saved)
                self.error = ""
                self._task = None
                pending, self._pending, self.confirm = self._pending, None, None
                if pending:
                    pending()
            except Exception as exc:
                self.error = str(exc) or type(exc).__name__
                self.confirm, self._pending = None, None
            finally:
                self._invalidate()
        self._task = asyncio.create_task(save())

    def _activate(self):
        field = self.field
        if field == EditorField.SAVE:
            self._save()
        elif field == EditorField.CANCEL:
            self.draft = None
            self.editing = False
        elif field == EditorField.DELETE and self.draft.original:
            self.confirm, self.confirm_index = Confirmation.DELETE, 1
        elif field == EditorField.ALL_DAY:
            self.draft.toggle_all_day()
        elif field == EditorField.HOLIDAY:
            self.draft.holiday = not self.draft.holiday
        elif field == EditorField.REPEAT:
            self.dropdown = "repeat"
            self.menu_index = tuple(RepeatUnit).index(self.draft.unit)
        else:
            self._next_field(1)

    def key(self, key, data):
        if self.busy:
            return True
        if self.confirm:
            choices = 2 if self.confirm == Confirmation.DELETE else 3
            if key in ("tab", "right", "s-tab", "left"):
                self.confirm_index = (self.confirm_index + (-1 if key in ("s-tab", "left") else 1)) % choices
            elif key == "escape":
                self.confirm, self._pending = None, None
            elif key == "enter" or data == " ":
                if self.confirm == Confirmation.DELETE:
                    if self.confirm_index == 0:
                        self._save(delete=True)
                    self.confirm = None
                elif self.confirm_index == 0:
                    self._save()
                elif self.confirm_index == 1:
                    action, self._pending, self.confirm, self.draft = self._pending, None, None, None
                    if action:
                        action()
                else:
                    self.confirm, self._pending = None, None
            return True
        if self.dropdown:
            choices = tuple(RepeatUnit) if self.dropdown == "repeat" else self.snapshot.on_day(self.selected)
            if key in ("up", "down", "pageup", "pagedown", "home", "end"):
                self.menu_index = (0 if key == "home" else len(choices) - 1 if key == "end" else
                    max(0, min(len(choices) - 1, self.menu_index + {"up": -1, "down": 1, "pageup": -4, "pagedown": 4}[key])))
            elif key == "escape":
                self.dropdown = None
            elif key in ("enter", "tab", "s-tab") or data == " ":
                if choices:
                    if self.dropdown == "repeat":
                        self.draft.unit = choices[self.menu_index]
                    else:
                        self.event_index, self.draft = self.menu_index, None
                        self._begin()
                self.dropdown = None
                if key in ("tab", "s-tab"):
                    self._next_field(-1 if key == "s-tab" else 1)
            return True
        if key in ("scroll-up", "scroll-down"):
            self._scroll = max(0, self._scroll + (-3 if key == "scroll-up" else 3))
            return True
        if self.editing:
            if key == "escape":
                self.editing = False
            elif key == "f2":
                self._save()
            elif key in ("tab", "s-tab"):
                self._next_field(-1 if key == "s-tab" else 1)
            elif self.draft.edit(self.field, key, data):
                pass
            elif key == "enter" or data == " ":
                self._activate()
            return True
        if key in ("left", "right", "up", "down"):
            ordinal = self.selected.toordinal() + {"left": -1, "right": 1, "up": -7, "down": 7}[key]
            self._select(date.fromordinal(max(1, min(date.max.toordinal(), ordinal))))
        elif key == "pageup":
            self._move(-1)
        elif key == "pagedown":
            self._move(1)
        elif key == "home":
            self._today()
        elif key in ("enter", "tab") or data == " ":
            self._begin()
        elif data == "n":
            self._new()
        elif data == "e":
            self._guard(lambda: setattr(self, "dropdown", "events"))
        elif key == "escape":
            return False
        return True

    def content(self):
        locale = self._locale()
        title = locale.month(self._month.month) + f" {self._month.year}"
        return PopupContent(title, (TextRow.plain(locale.text(M.CALENDAR), ""),), (
            PopupAction(locale.text(M.PREVIOUS_MONTH), lambda: self._move(-1)), PopupAction(locale.text(M.TODAY), self._today),
            PopupAction(locale.text(M.NEXT_MONTH), lambda: self._move(1))))

    def status_text(self):
        locale = self._locale()
        message = self.error or (self.runtime.error if self.runtime else "")
        if message:
            return locale.text(M.ERROR_HINT, message=message)
        if self.busy:
            return locale.text(M.SAVING)
        if self.confirm:
            return locale.text(M.CONFIRM_HINT)
        if self.dropdown:
            return locale.text(M.MENU_HINT)
        if not self.editing:
            return locale.text(M.CALENDAR_HINT)
        buffer = self.draft.buffer(self.field)
        action = locale.text(M.MULTILINE_HINT if self.field == EditorField.NOTES else M.TEXT_HINT if buffer is not None else M.ACTION_HINT)
        return locale.text(M.EDITOR_HINT, field=self.field.translated(locale), action=action)

    def _month_rows(self, cells, theme):
        locale = self._locale()
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **kw)
        month = calendar.Calendar(self._config().week_start)
        weekdays = tuple(month.iterweekdays())
        nav = [p(" < ", ThemeRole.TITLE, background=ThemeRole.BUTTON_BG, action=lambda: self._move(-1)),
               p(" " + locale.month(self._month.month) + f" {self._month.year} ", bold=True),
               p(" > ", ThemeRole.TITLE, background=ThemeRole.BUTTON_BG, action=lambda: self._move(1))]
        today = self._clock().now().date()
        tail = locale.text(M.TODAY) + f" {today.day:02}"
        nav += [p(" " * max(0, cells - sum(width(item[1]) for item in nav) - width(tail))), p(tail, ThemeRole.MUTED, action=self._today)]
        rows = [fitted(nav, cells, theme), fitted([], cells, theme)]
        cell = max(1, cells // 7)
        rows.append([item for weekday in weekdays for item in centered([p(locale.weekday(weekday, abbreviated=True), CalendarColors.role(weekday), bold=True)], cell, theme)])
        selected_row = 3
        snapshot = self.snapshot
        for week in month.monthdayscalendar(self._month.year, self._month.month):
            rows.append([p("─" * cells, ThemeRole.SEPARATOR)])
            days = tuple(date(self._month.year, self._month.month, number) if number else None for number in week)
            events_by_day = {day: snapshot.on_day(day) for day in days if day is not None}
            for offset in range(self.EVENT_ROWS + 1):
                row = []
                for weekday, day in zip(weekdays, days):
                    if day is None:
                        row.extend(fitted([], cell, theme))
                        continue
                    events = events_by_day[day]
                    selected = day == self.selected
                    if selected and offset == 0:
                        selected_row = len(rows)
                    role = CalendarColors.role(weekday, holiday=any(e.holiday for e in events))
                    text = f" {day.day:02}" + (" •" if day == today else "") if offset == 0 else " " + events[offset - 1].title if offset <= len(events) else ""
                    if offset == self.EVENT_ROWS and len(events) > self.EVENT_ROWS:
                        text = " " + locale.text(M.MORE_EVENTS, count=len(events) - self.EVENT_ROWS + 1)
                    if offset:
                        role = ThemeRole.TITLE if selected else ThemeRole.MUTED
                    label = fit(text, cell)
                    row.append(p(label + " " * max(0, cell - width(label)), role, background=ThemeRole.BUTTON_BG if selected else ThemeRole.BACKGROUND,
                                 bold=offset == 0, action=lambda day=day: self._select(day)))
                rows.append(fitted(row, cells, theme))
        return rows, selected_row

    def _editor_rows(self, cells, height, theme):
        locale = self._locale()
        background = ThemeRole.EDITOR_BG
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **{"background": background, **kw})
        fill = lambda parts: fitted(parts, cells, theme, background=background)
        center = lambda parts: centered(parts, cells, theme, background=background)
        draft = self.draft
        if draft is None:
            snapshot = self.snapshot
            preview_key = (snapshot, self.selected, self.event_index, getattr(self._clock(), "timezone", None))
            if preview_key != self._preview_key:
                self._preview_key = preview_key
                events = snapshot.on_day(self.selected)
                self._preview = EventDraft(self.selected, preview_key[-1],
                    snapshot.get(events[min(self.event_index, len(events) - 1)].id) if events else None)
            draft = self._preview
        rows = [fill([p(self.selected.isoformat(), ThemeRole.TITLE, bold=True)]), fill([])]
        self._field_rows = {}
        self._field_columns = {}

        def text_input(field, size, *, role=ThemeRole.FOREGROUND, multiline_rows=1):
            buffer = draft.buffer(field)
            active = self.editing and self.field == field
            self._field_rows[field] = len(rows)
            text = buffer.text
            if active:
                pos = buffer.cursor_position
                text = text[:pos] + "▏" + text[pos:]
            lines = text.split("\n")
            cursor_line = buffer.document.cursor_position_row if active else 0
            start = max(0, cursor_line - multiline_rows + 1)
            result = []
            for line in lines[start:start + multiline_rows]:
                if active and "▏" in line:
                    while width(line[:line.index("▏") + 1]) > size and line.index("▏") > 0:
                        line = line[1:]
                result.append([p(fit(line, size, marker=""), role,
                                 background=ThemeRole.INPUT_BG, action=lambda field=field: self._focus(field))])
            result += [[] for _ in range(multiline_rows - len(result))]
            # The input background includes all empty cells, with the same click target.
            for row in result:
                used = sum(width(item[1]) for item in row)
                row.append(p(" " * max(0, size - used), role, background=ThemeRole.INPUT_BG,
                             action=lambda field=field: self._focus(field)))
            return result

        def control(field, label, role=ThemeRole.FOREGROUND):
            self._field_rows[field] = len(rows)
            def action():
                self._focus(field)
                self._activate()
                self._invalidate()
            return button(theme, label, role=role, selected=self.editing and self.field == field, action=action)

        def section(field):
            rows.append(fill([p("─" * cells, ThemeRole.SEPARATOR)]))
            rows.append(fill([p(field.translated(locale), ThemeRole.MUTED)]))

        rows.append(fill([p(EditorField.TITLE.translated(locale), ThemeRole.MUTED)]))
        rows.extend(text_input(EditorField.TITLE, cells))
        section(EditorField.ALL_DAY)
        rows.append(center([control(EditorField.ALL_DAY, ("● " if draft.all_day else "○ ") + locale.text(M.ALL_DAY))]))
        rows.append(fill([]))
        padding = min(2, max(0, (cells - 1) // 2))
        room = max(1, cells - 2 * padding)
        arrow = f" {self.TIME_ARROW} " if draft.all_day else "  "
        side = max(1, (room - width(arrow)) // 2)
        date_role = ThemeColor.COMMENT if draft.all_day else ThemeRole.FOREGROUND
        rows.append(fill([p(" " * padding), *text_input(EditorField.START_DATE, side, role=date_role)[0],
                          p(arrow, date_role), *text_input(EditorField.END_DATE, max(1, room-side-width(arrow)), role=date_role)[0], p(" " * padding)]))
        if not draft.all_day:
            rows.append(center([p(self.TIME_ARROW, ThemeRole.MUTED)]))
            rows.append(fill([p(" " * padding), *text_input(EditorField.START_TIME, side)[0], p("  "),
                              *text_input(EditorField.END_TIME, max(1, room-side-2))[0], p(" " * padding)]))
        for field in (EditorField.LOCATION, EditorField.ALARM):
            section(field)
            rows.extend(text_input(field, cells))
        section(EditorField.INTERVAL)
        repeat = control(EditorField.REPEAT, draft.unit.translated(locale) + " ")
        holiday = control(EditorField.HOLIDAY, ("● " if draft.holiday else "○ ") + locale.text(M.HOLIDAY))
        number_width = max(1, cells - width(repeat[1]) - width(holiday[1]) - 4)
        self._field_columns[EditorField.REPEAT] = number_width + 2
        rows.append(fill([*text_input(EditorField.INTERVAL, number_width)[0], p("  "), repeat, p("  "), holiday]))
        section(EditorField.NOTES)
        footer_height = min(3, max(0, height - 1))
        memo_height = max(1, height - footer_height - len(rows))
        rows.extend(text_input(EditorField.NOTES, cells, multiline_rows=memo_height))
        content_height = max(1, height - footer_height)
        focus_row = self._field_rows.get(self.field, 0) if self.editing else 0
        start = min(max(0, len(rows) - content_height), max(0, focus_row - content_height + 2))
        visible = [fill(row) for row in rows[start:start + content_height]]
        while len(visible) < content_height:
            visible.append(fill([]))
        self._field_rows = {field: row - start for field, row in self._field_rows.items()}
        buttons = []
        for field, role in ((EditorField.SAVE, ThemeRole.SAVE), (EditorField.CANCEL, ThemeRole.FOREGROUND), (EditorField.DELETE, ThemeRole.DELETE)):
            if buttons:
                buttons.append(p("   "))
            buttons.append(control(field, field.translated(locale), role))
        footer = [fill([]), center(buttons), fill([])]
        if footer_height == 1:
            footer = [center(buttons)]
        elif footer_height == 2:
            footer = [center(buttons), fill([])]
        return (visible + footer[:footer_height])[:height]

    def draw_body(self, columns, rows, theme):
        if columns <= 0 or rows <= 0:
            return []
        padding = int(rows > 1)
        body = self._draw_body(columns, rows - padding, theme)
        # Reserve a full-width row above the shared status bar, outside both panes.
        return body + [fitted([], columns, theme) for _ in range(rows - len(body))]

    def _draw_body(self, columns, rows, theme):
        if columns <= 0 or rows <= 0:
            return []
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **kw)
        if self.confirm:
            labels = (M.DELETE, M.CANCEL) if self.confirm == Confirmation.DELETE else (M.SAVE, M.DISCARD, M.CANCEL)
            choices = []
            for index, label in enumerate(labels):
                def choose(index=index):
                    self.confirm_index = index
                    self.key("enter", "")
                    self._invalidate()
                choices += [button(theme, self._locale().text(label), selected=index == self.confirm_index, action=choose), p(" ")]
            return [fitted([p(self.confirm.message(self._locale()), ThemeRole.WARNING)], columns, theme), centered(choices, columns, theme), fitted([p(self.error, ThemeRole.ERROR)], columns, theme)][:rows]
        split = columns >= 70
        left_w = (columns - 3) * 2 // 3 if split else columns
        right_w = columns - left_w - 3 if split else columns
        left, selected_row = self._month_rows(left_w, theme) if split or not self.editing else ([], 0)
        right = self._editor_rows(right_w, rows, theme) if split or self.editing else []
        available = rows
        left_start = max(0, selected_row - available + self.EVENT_ROWS + 1) if len(left) > available else 0
        left_start = min(max(0, len(left) - available), left_start + self._scroll)
        body = []
        for index in range(available):
            l = left[index + left_start] if index + left_start < len(left) else []
            r = right[index] if index < len(right) else []
            body.append([*fitted(l, left_w, theme), p(" │ ", ThemeRole.SEPARATOR), *fitted(r, right_w, theme, background=ThemeRole.EDITOR_BG)] if split else fitted(r if self.editing else l, columns, theme))
        if self.dropdown:
            choices = [unit.translated(self._locale()) for unit in RepeatUnit] if self.dropdown == "repeat" else [e.title for e in self.snapshot.on_day(self.selected)]
            choices = choices or [self._locale().text(M.NO_EVENTS)]
            self.menu_index = min(self.menu_index, len(choices) - 1)
            size = min(len(choices), max(1, rows - 1))
            start = max(0, min(self.menu_index - size + 1, len(choices) - size))
            menu_w = min(columns, max(12, max(width(label) for label in choices) + 3))
            origin = left_w + 3 if split else 0
            field_x = self._field_columns.get(EditorField.REPEAT, 0) if self.dropdown == "repeat" else 0
            anchor_x = min(max(0, columns - menu_w), origin + field_x)
            anchor_y = (self._field_rows.get(EditorField.REPEAT, 0) + 1 if self.dropdown == "repeat" else 2)
            anchor_y = max(0, min(rows - size, anchor_y))
            for index, label in enumerate(choices[start:start + size]):
                selected = index + start == self.menu_index
                def choose(index=index):
                    self.menu_index = index + start
                    self.key("enter", "")
                    self._invalidate()
                role, bg = (ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if selected else (ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG)
                label = fit(" " + label, menu_w - 1)
                row = [p(label + " " * max(0, menu_w - 1 - width(label)), role, background=bg, action=choose),
                       p("█" if selected else " ", ThemeRole.SCROLLBAR, background=ThemeRole.BUTTON_BG)]
                target = anchor_y + index
                body[target] = [*cropped(body[target], 0, anchor_x, theme), *row,
                                *cropped(body[target], anchor_x + menu_w, columns - anchor_x - menu_w, theme)]
        return body[:rows]
