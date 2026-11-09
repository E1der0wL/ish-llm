"""Google-capable calendar form; edits and outcomes use only feature contracts."""
import asyncio
from datetime import date
from enum import StrEnum

from ...core.theme import ThemeRole
from ...locales import Message as M
from ...popups.models import PopupAction
from ...rendering.interactive import fitted, part
from ...rendering.message_form import MessageForm
from ...rendering.presentation import TextRow
from ...rendering.picker import PickerOverlay
from ...rendering.choice_picker import ChoicePicker
from ...rendering.text import clusters, fit, width
from .picker_view import DatePicker, TimePicker
from .remote_draft import RemoteDraft
from .editor import EditorField as F
from .form_view import EventForm
from .writes import CalendarConflict, CalendarWriteError, EventValues, WriteProblem
from .edits import ActionSavePending
from .recurrence import RepeatUnit, RepeatEnd
from .reminders import ReminderUnit


class FormMode(StrEnum):
    ACTIONS = "actions"
    CHOOSE = "choose"
    EDIT = "edit"
    LOAD = "load"
    FAILED = "failed"
    CONFLICT = "conflict"
    REVIEW = "review"
    UNCERTAIN = "uncertain"
    DISCARD = "discard"
    DELETE = "delete"


class RemoteEditor:
    """One owner for focus, draft transitions, confirmation and remote request lifetime."""

    def __init__(self, edits, day, clock, config, locale, changed, back, local, day_role):
        self.edits, self.day, self.clock, self.config = edits, day, clock, config
        self.locale, self.changed, self.back, self.local = locale, changed, back, local
        self._day_role = day_role
        self.mode, self.field = FormMode.CHOOSE, F.TARGET
        self.draft = None
        self.latest = None
        self.notice = None
        self._task = None
        self._writing = self._closed = self._uncertain = False
        self._pending_leave = None
        self._index = 0
        self._choices = (None, *edits.destinations)
        self._picker = self._picker_rect = None
        self._offset = 0
        self._load_target = None
        self._load_delete = False
        self._form = EventForm()
        self._message = MessageForm()
        self._action_pending = None

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    @property
    def heading_date(self):
        return self.day if self.mode == FormMode.EDIT else None

    def _launch(self, action):
        async def run():
            try:
                await action()
            finally:
                self._writing = False
                if not self._closed:
                    self.changed()
        self._task = asyncio.create_task(run())
        self.changed()

    def load(self, source, event_id, *, delete=False):
        self.mode, self.notice = FormMode.LOAD, None
        self._load_target = source, event_id
        self._load_delete = delete
        async def read():
            try:
                event = await self.edits.edit_target(source, event_id)
                if not self._closed:
                    catalog = await self.edits.action_catalog()
                    self.draft = RemoteDraft(source, self.day, self.clock().timezone, original=event, catalog=catalog)
                    self.mode, self.field, self._uncertain = FormMode.EDIT, F.TITLE, False
                    self._pending_leave = None
                    if delete:
                        self.mode, self._index, self._offset = FormMode.DELETE, 1, 0
            except CalendarWriteError as exc:
                self.notice = exc.problem
                self.mode = FormMode.UNCERTAIN if self._uncertain else FormMode.FAILED
            except Exception:
                self.notice, self.mode = WriteProblem.FAILED, FormMode.FAILED
        self._launch(read)

    def _save(self, delete=False):
        if self.busy or self.draft is None or self._uncertain:
            return
        if self._action_pending is not None:
            self._retry_actions()
            return
        try:
            mutation = self.draft.mutation(delete=delete)
        except (ValueError, OverflowError):
            self.notice = M.CALENDAR_INVALID_VALUES
            self.mode = FormMode.EDIT
            return
        self._writing, self.notice = True, M.CALENDAR_DELETING if delete else M.SAVING
        async def save():
            try:
                event = await self.edits.save(mutation, None if delete else self.draft.action_change())
                if self._closed:
                    return
                if delete:
                    self.back()
                    return
                self.draft = RemoteDraft(mutation.source, self.day, self.clock().timezone, original=event,
                                         catalog=await self.edits.action_catalog())
                self.mode, self.notice = FormMode.EDIT, M.CALENDAR_SAVED
                pending, self._pending_leave = self._pending_leave, None
                if pending:
                    pending()
                else:
                    self.back()
            except CalendarConflict as exc:
                self.latest, self.mode, self.notice, self._index = exc.latest, FormMode.CONFLICT, None, 0
            except CalendarWriteError as exc:
                self.notice = exc.problem
                self._uncertain = exc.problem == WriteProblem.UNCERTAIN
                self.mode = FormMode.UNCERTAIN if self._uncertain else FormMode.EDIT
                self._index = 0
            except ActionSavePending as exc:
                self._action_pending, self.mode, self.notice, self._index = exc, FormMode.ACTIONS, None, 0
        self._launch(save)

    def _retry_actions(self):
        self._writing = True
        async def retry():
            try:
                await self.edits.finish_actions(self._action_pending)
            except Exception:
                self.notice = M.CALENDAR_ACTION_PENDING
            else:
                self._action_pending = None
                self.back()
        self._launch(retry)

    def _choose(self, index):
        source = self._choices[index]
        if source is None:
            self.local()
            return
        if self.draft is None:
            self.draft = RemoteDraft(source, self.day, self.clock().timezone, now=self.clock().now())
        else:
            self.draft.change_source(source)
        self.mode, self.field = FormMode.EDIT, F.TITLE
        self._form.reveal_focus()
        self.changed()

    def _rebase(self, keep=False):
        if self.latest is None:
            self._read_latest()
            return
        try:
            self.draft = self.draft.rebased(self.latest, self.day, keep=keep)
        except (ValueError, OverflowError):
            self.notice = M.CALENDAR_INVALID_VALUES
            return
        self.latest, self.mode, self.field = None, FormMode.EDIT, F.TITLE
        self._uncertain = False
        self.notice = M.CALENDAR_REAPPLY_HINT if keep else None
        self._pending_leave = None
        self._form.reveal_focus()
        self.changed()

    def _read_latest(self):
        previous = self.mode
        self.mode, self.notice = FormMode.LOAD, None
        async def read():
            try:
                event = await self.edits.get(self.draft.source, self.draft.event_id)
                if event is None:
                    raise CalendarWriteError(WriteProblem.UNAVAILABLE)
                if not event.editable:
                    raise CalendarWriteError(WriteProblem.DENIED)
                self.latest = event
                self.mode = FormMode.REVIEW if self._uncertain else FormMode.CONFLICT
                self._index = 0
            except CalendarWriteError as exc:
                self.mode, self.notice = previous, exc.problem
            except Exception:
                self.mode, self.notice = previous, WriteProblem.FAILED
        self._launch(read)

    def leave(self, callback=None):
        if self._writing:
            return False
        callback = callback or self.back
        if self.busy and self._task is not asyncio.current_task():
            self._task.cancel()
        if self.draft is not None and (self.draft.dirty or self._uncertain):
            self._pending_leave, self.mode, self._index = callback, FormMode.DISCARD, 0
            self.changed()
            return False
        callback()
        return True

    def request_close(self, dismiss):
        if self._writing:
            return False
        if self.draft is not None and (self.draft.dirty or self._uncertain):
            self.leave(dismiss)
            return False
        return True

    def dispose(self):
        self._closed = True
        if self.busy and self._task is not asyncio.current_task():
            self._task.cancel()

    def _discard(self):
        pending, self._pending_leave = self._pending_leave, None
        self.draft = None
        (pending or self.back)()

    def _cancel_confirmation(self):
        self.mode = FormMode.UNCERTAIN if self._uncertain else FormMode.EDIT
        self._pending_leave = None
        self.changed()

    def _actions(self):
        if self.mode == FormMode.ACTIONS:
            return ((M.CALENDAR_ACTION_RETRY, self._retry_actions), (M.CANCEL, self.back))
        if self.mode in (FormMode.CONFLICT, FormMode.REVIEW):
            return ((M.CALENDAR_LOAD_LATEST, lambda: self._rebase(False)),
                    (M.CALENDAR_REAPPLY, lambda: self._rebase(True))) if self.latest is not None else (
                    (M.CALENDAR_RECHECK, lambda: self._rebase(False)), (M.CANCEL, self._cancel_confirmation))
        if self.mode in (FormMode.UNCERTAIN, FormMode.FAILED):
            target = (self.draft.source, self.draft.event_id) if self.draft else self._load_target
            return ((M.CALENDAR_RECHECK, self._read_latest if self.draft else lambda: self.load(*target, delete=self._load_delete)), (M.CANCEL, self.leave))
        if self.mode == FormMode.DISCARD:
            return (() if self._uncertain else ((M.SAVE, self._save),)) + ((M.DISCARD, self._discard), (M.CANCEL, self._cancel_confirmation))
        if self.mode == FormMode.DELETE:
            return ((M.DELETE, lambda: self._save(True)), (M.CANCEL, self._cancel_confirmation))
        return ()

    def _focus(self, field):
        if self.busy:
            return
        self.field = field
        self._form.reveal_focus()
        self.notice = None
        self.changed()

    def _cancel_picker(self):
        self._picker = self._picker_rect = None
        self.changed()

    def _select_alarm(self, index):
        self._focus(F.ALARMS)
        self.draft.select_alarm(index)
        self.changed()

    def _remove_alarm(self, index):
        self._focus(F.ALARMS)
        self.draft.remove_alarm(index)
        self.changed()

    def _recurrence_notice(self):
        event = self.draft.original
        return self.locale().text(M.CALENDAR_CUSTOM_REPEAT) if event is not None and not event.repeat.editable else ""

    def _activate(self, field):
        if self.busy:
            return
        self._focus(field)
        if field == F.TARGET:
            self._choices = self.edits.destinations
            if self._choices:
                self.mode, self._index = FormMode.CHOOSE, 0
        elif field == F.ALL_DAY:
            self.draft.all_day = not self.draft.all_day
        elif field in (F.REPEAT, F.REPEAT_END, F.ALARM_UNIT):
            enum, attribute = {F.REPEAT: (RepeatUnit, "unit"), F.REPEAT_END: (RepeatEnd, "repeat_end"),
                               F.ALARM_UNIT: (ReminderUnit, "alarm_unit")}[field]
            def apply(value):
                setattr(self.draft, attribute, value)
                self._cancel_picker()
            self._picker = ChoicePicker(tuple(enum), getattr(self.draft, attribute),
                lambda value: value.translated(self.locale()), apply, self._cancel_picker, self.changed)
        elif field == F.ALARM_ADD:
            try:
                self.draft.add_alarm()
            except ValueError:
                self.notice = M.CALENDAR_INVALID_VALUES
        elif field.temporal_type is not None:
            value = self.draft.temporal(field)
            def apply(value):
                self.draft.choose(field, value)
                self._cancel_picker()
            if field.temporal_type is date:
                self._picker = DatePicker(value, self.config().week_start, lambda: self.clock().now().date(), apply,
                    self._cancel_picker, self.changed, self._day_role)
            else:
                self._picker = TimePicker(value, apply, self._cancel_picker, self.changed)
        elif field == F.SAVE:
            self._save()
        elif field == F.CANCEL:
            self.leave()
        elif field == F.DELETE:
            self.mode, self._index, self._offset = FormMode.DELETE, 1, 0

    def key(self, key, data):
        if self.busy:
            if key == "escape" and not self._writing:
                self.leave()
            return True
        if self._picker:
            return self._picker.key(key, data)
        if self.mode == FormMode.CHOOSE:
            if key == "escape":
                if self.draft is None:
                    self.back()
                else:
                    self.mode = FormMode.EDIT
            elif key in ("up", "down", "tab", "s-tab"):
                self._index = (self._index + (-1 if key in ("up", "s-tab") else 1)) % max(1, len(self._choices))
            elif key == "enter" or data == " ":
                if self._choices:
                    self._choose(self._index)
            return True
        actions = self._actions()
        if actions:
            if self._message.key(key):
                pass
            elif key == "escape":
                self.leave() if self.mode in (FormMode.UNCERTAIN, FormMode.FAILED, FormMode.ACTIONS) else self._cancel_confirmation()
            elif key in ("tab", "right", "s-tab", "left"):
                self._index = (self._index + (-1 if key in ("s-tab", "left") else 1)) % len(actions)
            elif key == "enter" or data == " ":
                actions[self._index % len(actions)][1]()
            return True
        if key == "escape":
            self.leave()
        elif key == "f2":
            self._save()
        elif key in ("tab", "s-tab"):
            fields = self.draft.fields()
            self._focus(fields[(fields.index(self.field) + (-1 if key == "s-tab" else 1)) % len(fields)])
        elif self.field == F.ALARMS and key in ("up", "down", "delete", "backspace"):
            if key in ("delete", "backspace"):
                self.draft.remove_alarm()
            else:
                self.draft.select_alarm(self.draft.alarm_index + (-1 if key == "up" else 1))
        elif self.draft.edit(self.field, key, data):
            pass
        elif key == "enter" or data == " ":
            self._activate(self.field)
        return True

    def status_text(self):
        locale = self.locale()
        notice = self.notice.message(locale) if isinstance(self.notice, WriteProblem) else locale.text(self.notice) if self.notice else ""
        if self.busy or self.mode == FormMode.LOAD:
            return notice or locale.text(M.CALENDAR_LOADING)
        if self._picker:
            return self._picker.status_text(locale)
        if self._actions():
            return self._message.status_text(locale)
        hint = locale.text(M.MENU_HINT if self.mode == FormMode.CHOOSE else M.CALENDAR_REMOTE_HINT)
        return hint + (" · " + notice if notice else "")

    def scroll_at(self, x, y, amount):
        if self._picker is not None:
            if self._picker_rect is not None and self._picker_rect.contains(x, y):
                self._picker.scroll_at(x - self._picker_rect.left, y - self._picker_rect.top, amount)
            return True
        if self.mode == FormMode.EDIT:
            self._form.scroll(amount)
        elif self._actions() or self.mode == FormMode.LOAD:
            self._message.scroll(amount)
        else:
            self._offset = max(0, self._offset + amount)
        self.changed()
        return True

    def draw(self, columns, height, theme):
        if columns <= 0 or height <= 0:
            return []
        locale, bg = self.locale(), ThemeRole.EDITOR_BG
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **{"background": bg, **kw})
        fill = lambda items=(): fitted(items, columns, theme, background=bg)
        rows = []
        selected_choice_row = None
        self._picker_rect = None

        def text(value, role=ThemeRole.FOREGROUND):
            for line in str(value).splitlines() or [""]:
                current = ""
                for cluster in clusters(line):
                    if current and width(current + cluster) > columns:
                        rows.append(fill([p(current, role)]))
                        current = ""
                    current += cluster
                rows.append(fill([p(current, role)]))

        if self.mode == FormMode.CHOOSE:
            text(locale.text(M.CALENDAR_CHOOSE), ThemeRole.TITLE)
            rows.append(fill())
            for index, source in enumerate(self._choices):
                selected = index == self._index
                if selected:
                    selected_choice_row = len(rows)
                label = locale.text(M.CALENDAR_LOCAL) if source is None else source.label
                role, background = (ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if selected else (ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG)
                label = fit(" " + label, columns)
                rows.append(fill([p(label + " " * max(0, columns - width(label)), role, background=background,
                                    action=lambda index=index: self._choose(index))]))
            text("", ThemeRole.MUTED)
        elif self.mode == FormMode.LOAD:
            return self._message.draw(TextRow.plain(locale.text(M.CALENDAR_LOADING), ThemeRole.MUTED),
                columns, height, theme, heading=None, background=bg)
        elif self._actions():
            message = M.CALENDAR_ACTION_PENDING if self.mode == FormMode.ACTIONS else M.CALENDAR_REVIEW if self.mode == FormMode.REVIEW else M.CALENDAR_CONFLICT if self.mode == FormMode.CONFLICT else M.CALENDAR_WRITE_UNCERTAIN if self.mode == FormMode.UNCERTAIN else (M.CALENDAR_DELETE_SERIES if self.draft.original.is_series else M.CALENDAR_DELETE_REMOTE) if self.mode == FormMode.DELETE else M.SAVE_CHANGES if self.mode == FormMode.DISCARD else M.CALENDAR_WRITE_FAILED
            summary = self.notice.message(locale) if self.mode == FormMode.FAILED and isinstance(self.notice, WriteProblem) else locale.text(message)
            details = []
            def detail(value, role=ThemeRole.FOREGROUND):
                details.append(TextRow.plain(str(value), role))
            if self.draft is not None:
                detail(self.draft.source.label, ThemeRole.TITLE)
                if self.draft.original is not None and self.draft.original.is_series:
                    detail(locale.text(M.CALENDAR_SERIES), ThemeRole.MUTED)
            if self.mode in (FormMode.CONFLICT, FormMode.REVIEW) and self.latest is not None:
                try:
                    ours, latest = self.draft.values(), EventValues.from_event(self.latest)
                    for field in ours.changes(latest):
                        detail("─" * columns, ThemeRole.SEPARATOR)
                        detail(field.label(locale), ThemeRole.TITLE)
                        detail(locale.text(M.CALENDAR_MY_EDIT) + ": " + str(ours.value(field)))
                        detail(locale.text(M.CALENDAR_LATEST) + ": " + str(latest.value(field)), ThemeRole.MUTED)
                except (ValueError, OverflowError):
                    detail(locale.text(M.CALENDAR_INVALID_VALUES), ThemeRole.WARNING)
            elif self.mode == FormMode.DELETE:
                detail(self.draft.original.title)
            actions = tuple(PopupAction(locale.text(label), action) for label, action in self._actions())
            role = ThemeRole.ERROR if self.mode == FormMode.FAILED else ThemeRole.WARNING
            return self._message.draw(TextRow.plain(summary, role), columns, height, theme,
                details=details, actions=actions, selected=self._index, heading=None, background=bg)
        else:
            body = self._form.draw(self.draft, self.day, self.field, True, columns, height, theme, locale,
                focus=self._focus, activate=self._activate, open_picker=self._activate, changed=self.changed,
                target_label=self.draft.source.label,
                notice=self._recurrence_notice(),
                on_alarm_select=self._select_alarm, on_alarm_remove=self._remove_alarm)
            if self._picker:
                anchor = (self._form.field_columns.get(self.field, 0), self._form.field_rows.get(self.field, 0))
                body, self._picker_rect = PickerOverlay.draw(body, columns, height, self._picker,
                    anchor, theme, locale, self._cancel_picker)
            return body
        room = height
        if selected_choice_row is not None:
            self._offset = min(self._offset, selected_choice_row) if selected_choice_row < self._offset else max(self._offset, selected_choice_row - room + 1)
        self._offset = max(0, min(self._offset, len(rows) - room))
        return (rows[self._offset:self._offset + room] + [fill()] * room)[:room]
