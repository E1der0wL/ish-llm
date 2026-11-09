"""One event form renderer for local and external drafts; draft owners declare capabilities."""
from ...core.theme import ThemeRole, ThemeColor
from ...locales import Message as M
from ...popups.config import PopupRect
from ...rendering.interactive import part, fitted, centered, button
from ...rendering.text import fit, width
from .editor import EditorField
from .models import RepeatUnit
from .recurrence import RepeatEnd
from .form_layout import CalendarFormLayout


class EventForm:
    TIME_ARROW = CalendarFormLayout.TIME_ARROW
    TIME_GAP = CalendarFormLayout.TIME_GAP
    PICKER_MARKER = CalendarFormLayout.PICKER_MARKER
    ALARM_ROWS = 3

    def __init__(self):
        self.field_rows, self.field_columns = {}, {}
        self.alarm_offset = 0
        self.alarm_region = None
        self._offset = 0
        self._follow = True

    def reveal_focus(self):
        self._follow = True

    def scroll(self, amount):
        self._offset = max(0, self._offset + amount)
        self._follow = False

    def draw(self, draft, selected, focused, editing, cells, height, theme, locale, *,
             focus, activate, open_picker, changed, target_label="", notice="",
             on_alarm_select=None, on_alarm_remove=None):
        self.alarm_region = None
        background = ThemeRole.EDITOR_BG
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **{"background": background, **kw})
        fill = lambda parts: fitted(parts, cells, theme, background=background)
        center = lambda parts: centered(parts, cells, theme, background=background)
        # The calendar pane owns its fixed date heading outside this scroll area.
        rows = [fill([])]
        self.field_rows = {}
        self.field_columns = {}

        def text_input(field, size, *, role=ThemeRole.FOREGROUND, multiline_rows=1):
            enabled = draft.enabled(field)
            buffer = draft.alarm_number if field == EditorField.ALARM_NUMBER else draft.buffer(field)
            active = enabled and editing and focused == field
            action = (lambda field=field: focus(field)) if enabled else None
            self.field_rows[field] = len(rows)
            text, pos = (draft.repeat_count_display(locale) if field == EditorField.REPEAT_COUNT else
                         (buffer.text, buffer.cursor_position))
            if active:
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
                                 background=ThemeRole.INPUT_BG, action=action)])
            result += [[] for _ in range(multiline_rows - len(result))]
            # The input background includes all empty cells, with the same click target.
            for row in result:
                used = sum(width(item[1]) for item in row)
                row.append(p(" " * max(0, size - used), role, background=ThemeRole.INPUT_BG,
                             action=action))
            return result

        def control(field, label, role=ThemeRole.FOREGROUND):
            self.field_rows[field] = len(rows)
            def action():
                focus(field)
                activate(field)
                changed()
            enabled = draft.enabled(field)
            return button(theme, label, role=role if enabled else ThemeRole.MUTED,
                          selected=enabled and editing and focused == field, action=action if enabled else None)

        def temporal(field, size, column, role=ThemeRole.FOREGROUND):
            self.field_rows[field], self.field_columns[field] = len(rows), column
            def choose():
                focus(field)
                open_picker(field)
            selected = editing and focused == field
            label = fit(draft.temporal_label(field) + self.PICKER_MARKER, size, marker="")
            remaining = max(0, size - width(label))
            label = " " * (remaining // 2) + label + " " * (remaining - remaining // 2)
            return p(label, ThemeRole.TITLE if selected else role,
                     background=ThemeRole.INPUT_BG, action=choose)

        def section(field):
            rows.append(fill([p("─" * cells, ThemeRole.SEPARATOR)]))
            rows.append(fill([p(field.translated(locale), ThemeRole.MUTED)]))

        if target_label and draft.original is None:
            rows.append(fill([p(EditorField.TARGET.translated(locale), ThemeRole.MUTED)]))
            label = target_label + (self.PICKER_MARKER if draft.enabled(EditorField.TARGET) else "")
            rows.append(fill([control(EditorField.TARGET, label)]))
            rows.append(fill([]))
        if notice:
            rows.append(fill([p(notice, ThemeRole.MUTED)]))
        rows.append(fill([p(EditorField.TITLE.translated(locale), ThemeRole.MUTED)]))
        rows.extend(text_input(EditorField.TITLE, cells))
        section(EditorField.ALL_DAY)
        rows.append(center([control(EditorField.ALL_DAY, ("● " if draft.all_day else "○ ") + locale.text(M.ALL_DAY))]))
        rows.append(fill([]))
        padding = min(CalendarFormLayout.TIME_PADDING, max(0, (cells - 1) // 2))
        room = max(1, cells - 2 * padding)
        arrow = self.TIME_ARROW if draft.all_day else ""
        space = max(0, self.TIME_GAP - width(arrow))
        arrow = " " * (space // 2) + arrow + " " * (space - space // 2)
        side = max(1, (room - width(arrow)) // 2)
        label_width = width(draft.temporal_label(EditorField.START_DATE) + self.PICKER_MARKER)
        # ISO dates and HH:MM:SS have the same cell parity. Leave odd spare
        # cells outside the pair so both labels have equal internal padding.
        if side > label_width and (side - label_width) % 2:
            side -= 1
        spare = max(0, room - side * 2 - width(arrow))
        before, after = padding + spare // 2, padding + spare - spare // 2
        date_role = ThemeColor.COMMENT if draft.all_day else ThemeRole.FOREGROUND
        rows.append(fill([p(" " * before), temporal(EditorField.START_DATE, side, before, date_role),
                          p(arrow, date_role), temporal(EditorField.END_DATE, side,
                                                       before + side + width(arrow), date_role), p(" " * after)]))
        if not draft.all_day:
            rows.append(center([p(self.TIME_ARROW, ThemeRole.MUTED)]))
            rows.append(fill([p(" " * before), temporal(EditorField.START_TIME, side, before), p(" " * self.TIME_GAP),
                              temporal(EditorField.END_TIME, side, before + side + self.TIME_GAP), p(" " * after)]))
        if draft.supports(EditorField.INTERVAL):
            section(EditorField.INTERVAL)
            repeat = control(EditorField.REPEAT, draft.unit.translated(locale) + self.PICKER_MARKER)
            holiday = (control(EditorField.HOLIDAY, ("● " if draft.holiday else "○ ") + locale.text(M.HOLIDAY))
                       if draft.supports(EditorField.HOLIDAY) else p(""))
            number_width = max(1, cells - width(repeat[1]) - width(holiday[1]) - 4)
            self.field_columns[EditorField.REPEAT] = number_width + 2
            rows.append(fill([*text_input(EditorField.INTERVAL, number_width)[0], p("  "), repeat, p("  "), holiday]))
            if draft.unit != RepeatUnit.NONE:
                rows.append(fill([]))
                ending = control(EditorField.REPEAT_END, draft.repeat_end.translated(locale) + self.PICKER_MARKER)
                value_width = max(1, cells - width(ending[1]) - 2)
                self.field_columns[EditorField.REPEAT_END] = value_width + 2
                value = (text_input(EditorField.REPEAT_COUNT, value_width)[0] if draft.repeat_end == RepeatEnd.COUNT else
                         [temporal(EditorField.REPEAT_UNTIL, value_width, 0)])
                rows.append(fill([*value, p("  "), ending]))
        if draft.supports(EditorField.ALARMS):
            section(EditorField.ALARMS)
            unit = control(EditorField.ALARM_UNIT, draft.alarm_unit.translated(locale) + " ")
            add = control(EditorField.ALARM_ADD, locale.text(M.REGISTER), ThemeRole.SAVE)
            number_width = max(1, cells - width(unit[1]) - width(add[1]) - 4)
            self.field_columns[EditorField.ALARM_UNIT] = number_width + 2
            number_role = ThemeRole.FOREGROUND if draft.alarm_unit.uses_number else ThemeColor.COMMENT
            rows.append(fill([*text_input(EditorField.ALARM_NUMBER, number_width, role=number_role)[0], p("  "), unit, p("  "), add]))
            rows.append(fill([]))
            self.field_rows[EditorField.ALARMS] = len(rows)
            alarm_top = len(rows)
            self.alarm_offset = max(0, min(self.alarm_offset, draft.alarm_index, max(0, len(draft.alarms) - self.ALARM_ROWS)))
            if draft.alarm_index >= self.alarm_offset + self.ALARM_ROWS:
                self.alarm_offset = draft.alarm_index - self.ALARM_ROWS + 1
            first_alarm = self.alarm_offset
            scrollbar = int(len(draft.alarms) > self.ALARM_ROWS)
            list_width = max(0, cells - scrollbar)
            thumb_size = max(1, self.ALARM_ROWS * self.ALARM_ROWS // max(1, len(draft.alarms)))
            thumb_top = (self.ALARM_ROWS - thumb_size) * first_alarm // max(1, len(draft.alarms) - self.ALARM_ROWS)
            def alarm_row(parts, index):
                row = fitted(parts, list_width, theme, background=ThemeRole.INPUT_BG)
                if scrollbar:
                    row.append(p("█" if thumb_top <= index - first_alarm < thumb_top + thumb_size else " ",
                                 ThemeRole.SCROLLBAR, background=ThemeRole.BUTTON_BG))
                return fill(row)
            for index in range(first_alarm, first_alarm + self.ALARM_ROWS):
                if index >= len(draft.alarms):
                    rows.append(alarm_row([p(locale.text(M.NO_ALARMS), ThemeRole.MUTED, background=ThemeRole.INPUT_BG)]
                                          if not draft.alarms and index == 0 else [], index))
                    continue
                def select_alarm(index=index):
                    on_alarm_select(index)
                def remove_alarm(index=index):
                    on_alarm_remove(index)
                selected = editing and focused == EditorField.ALARMS and index == draft.alarm_index
                remove = p(" " + locale.text(M.DELETE) + " ", ThemeRole.DELETE,
                           background=ThemeRole.INPUT_BG, action=remove_alarm)
                label = ("› " if selected else "  ") + draft.alarms[index].label(locale)
                label_width = max(0, list_width - width(remove[1]) - 1)
                label = fit(label, label_width)
                rows.append(alarm_row([p(label + " " * max(0, label_width - width(label)),
                                         ThemeRole.TITLE if selected else ThemeRole.FOREGROUND,
                                         background=ThemeRole.INPUT_BG, action=select_alarm),
                                       p(" ", background=ThemeRole.INPUT_BG), remove], index))
        if draft.supports(EditorField.COMMAND):
            section(EditorField.COMMAND)
            rows.extend(text_input(EditorField.COMMAND, cells))
        if draft.supports(EditorField.LOCATION):
            section(EditorField.LOCATION)
            rows.extend(text_input(EditorField.LOCATION, cells))
        section(EditorField.NOTES)
        footer_height = min(3, max(0, height - 1))
        memo_height = max(1, height - footer_height - len(rows))
        rows.extend(text_input(EditorField.NOTES, cells, multiline_rows=memo_height))
        content_height = max(1, height - footer_height)
        focus_row = self.field_rows.get(focused, 0) if editing else 0
        if self._follow:
            self._offset = max(0, focus_row - content_height + 2)
        start = min(max(0, len(rows) - content_height), self._offset)
        visible = [fill(row) for row in rows[start:start + content_height]]
        while len(visible) < content_height:
            visible.append(fill([]))
        self.field_rows = {field: row - start for field, row in self.field_rows.items()}
        if draft.supports(EditorField.ALARMS):
            alarm_visible_top = max(0, alarm_top - start)
            alarm_visible_end = min(content_height, alarm_top - start + self.ALARM_ROWS)
            self.alarm_region = PopupRect(0, alarm_visible_top, cells, max(0, alarm_visible_end - alarm_visible_top))
        buttons = []
        for field, role in ((EditorField.SAVE, ThemeRole.SAVE), (EditorField.CANCEL, ThemeRole.FOREGROUND), (EditorField.DELETE, ThemeRole.DELETE)):
            if not draft.enabled(field) or field == EditorField.DELETE and draft.original is None:
                continue
            if buttons:
                buttons.append(p("   "))
            label = ("* " if field == EditorField.SAVE and draft.dirty else "") + field.translated(locale)
            buttons.append(control(field, label, role))
        footer = [fill([]), center(buttons), fill([])]
        if footer_height == 1:
            footer = [center(buttons)]
        elif footer_height == 2:
            footer = [center(buttons), fill([])]
        return (visible + footer[:footer_height])[:height]
