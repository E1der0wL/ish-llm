"""Centered completion management with a local draft and explicit atomic save."""
from dataclasses import replace
from enum import StrEnum
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from ...core.theme import ThemeRole as R
from ...locales import Locale, Message as M
from ...popups.models import PopupAction, PopupContent
from ...rendering.message_form import MessageForm
from ...rendering.presentation import TextRow
from ...rendering.interactive import InteractivePopup, part, button, fitted, centered
from ...rendering.text import fit, width
from .models import Catalog, Registration
from .runtime import Phase
from .management_editor import EditBuffer, ItemDraft, ItemField


class ManagementAction(StrEnum):
    ADD = ""
    EDIT = ""
    LOCK = ""
    UNLOCK = ""
    DELETE = ""


class CompletionManager(InteractivePopup):
    def __init__(self, runtime, host, invalidate, dismiss, *, locale=Locale):
        self.runtime, self.host = runtime, host
        self._invalidate, self._dismiss, self._locale = invalidate, dismiss, locale
        self.expected = self.draft = None
        self.prefix_index = self.item_index = 0
        self.area = "prefixes"
        self.editor = self.prefix_editor = None
        self.field = ItemField.NAME
        self.dropdown = None
        self.menu_index = 0
        self.confirm = None
        self.confirm_yes = False
        self._message = MessageForm()
        self.error = ""
        self.saving = self.disposed = False
        self._left_width = 0
        self._body_height = 1

    def _sync(self):
        if self.draft is None and not self.runtime.busy:
            if self.runtime.catalog_ready:
                self.expected = self.draft = self.runtime.catalog
            elif self.runtime.error:
                self.error = self.runtime.error

    @property
    def dirty(self):
        return self.draft is not None and self.draft.registrations != self.expected.registrations

    @property
    def editor_dirty(self):
        editor = self.editor if self.editor is not None else self.prefix_editor
        return editor is not None and editor.dirty

    @property
    def busy(self):
        return self.saving or self.runtime.busy or self.runtime.phase == Phase.EXECUTING

    def registrations(self):
        return self.draft.ordered_registrations() if self.draft is not None else ()

    def registration(self):
        values = self.registrations()
        self.prefix_index = min(self.prefix_index, max(0, len(values) - 1))
        return values[self.prefix_index] if values else None

    def items(self):
        registration = self.registration()
        if registration is None:
            return ()
        return tuple(sorted(registration.items, key=lambda item: Catalog.name_order(item.name)))

    def selected_item(self):
        values = self.items()
        self.item_index = min(self.item_index, max(0, len(values) - 1))
        return values[self.item_index] if values else None

    def content(self):
        self._sync()
        return PopupContent.plain(self._locale().text(M.COMPLETION_MANAGER), "")

    def status_text(self):
        if self.confirm:
            return self._message.status_text(self._locale())
        message = (M.COMPLETION_EDIT_HINT
                   if self.editor or self.prefix_editor else M.COMPLETION_PREFIX_HINT if self.area == "prefixes"
                   else M.COMPLETION_LIST_HINT)
        return self._locale().text(message)

    def request_close(self):
        if self.saving:
            return False
        if self.dirty or self.editor_dirty:
            self.confirm = (M.COMPLETION_DISCARD, "", self._discard)
            self.confirm_yes = False
            self._invalidate()
            return False
        return True

    def _discard(self):
        self.draft = self.expected
        self.editor = self.prefix_editor = None
        self._dismiss()

    def _put(self, registration):
        if self.busy or self.draft is None:
            return
        self.draft = self.draft.put(registration)
        self.error = ""

    def _select_prefix(self, index):
        if self.editor or self.prefix_editor or self.saving:
            return
        self.prefix_index, self.item_index, self.area = index, 0, "prefixes"
        self._invalidate()

    def _select_item(self, index):
        self.item_index, self.area = index, "items"
        self._invalidate()

    def _edit_prefix(self, new=False):
        if self.busy or self.draft is None:
            return
        registration = None if new else self.registration()
        if not new and registration is None:
            return
        self.prefix_editor = EditBuffer(self.host.label(registration.prefix) if registration else "")
        self._old_prefix = registration
        self.error = ""

    def _edit_item(self, new=False):
        if self.busy or self.draft is None:
            return
        item = None if new else self.selected_item()
        if self.registration() is None or not new and item is None:
            return
        self.editor, self.field = ItemDraft(item), ItemField.NAME
        self.error = ""

    def _apply(self):
        if self.busy or self.draft is None:
            return
        try:
            if self.prefix_editor is not None:
                prefix = self.host.prefix(self.prefix_editor.text)
                registration = replace(self._old_prefix, prefix=prefix) if self._old_prefix else Registration(prefix)
                self.draft = self.draft.edit_registration(self._old_prefix.prefix if self._old_prefix else None, registration)
                self.prefix_index = self.registrations().index(registration)
                self.prefix_editor = None
            elif self.editor is not None:
                candidate = self.editor.build()
                old = self.editor.original.name if self.editor.original else None
                self._put(self.registration().edit_item(old, candidate))
                self.item_index = self.items().index(candidate)
                self.editor = None
            self.error = ""
        except (ValueError, TypeError) as exc:
            self.error = str(exc)
        self._invalidate()

    def _save(self):
        if self.busy or self.draft is None or not self.dirty:
            return
        def completed(error):
            if self.disposed:
                return
            self.saving, self.error = False, error
            if not error:
                self.expected = self.draft = self.runtime.catalog
            self._invalidate()
        self.saving = self.runtime.save_changes(self.expected, self.draft, completed)
        self._invalidate()

    def _delete(self):
        if self.busy or self.draft is None:
            return
        registration = self.registration()
        if registration is None:
            return
        if self.area == "prefixes":
            name = self.host.label(registration.prefix)
            action = lambda: setattr(self, "draft", self.draft.remove_registration(registration.prefix))
        else:
            item = self.selected_item()
            if item is None:
                return
            name = item.name
            action = lambda: self._put(registration.remove_item(name))
        self.confirm, self.confirm_yes = (M.COMPLETION_DELETE, name, action), False

    def _lock(self):
        if self.busy or self.draft is None:
            return
        registration = self.registration()
        if registration is None:
            return
        if self.area == "prefixes":
            self._put(replace(registration, locked=not registration.locked))
        elif (item := self.selected_item()) is not None:
            self._put(registration.edit_item(item.name, replace(item, locked=not item.locked)))

    def _choose(self, index):
        value, _ = self.editor.choices(self.dropdown)[index]
        self.editor.choose(self.dropdown, value)
        self.dropdown = None
        self._invalidate()

    def _focus(self, field):
        self.field = field
        self.dropdown = None
        choices = self.editor.choices(field)
        if choices:
            self.dropdown = field
            self.menu_index = next(i for i, (value, _) in enumerate(choices) if value == self.editor.value(field))
        self._invalidate()

    def key(self, key, data):
        self._sync()
        if self.saving:
            return True
        if self.confirm:
            if self._message.key(key):
                pass
            elif key == "escape":
                self.confirm = None
            elif key in ("left", "right", "tab", "s-tab"):
                self.confirm_yes = not self.confirm_yes
            elif key == "enter":
                _, _, action = self.confirm
                self.confirm = None
                if self.confirm_yes:
                    action()
            return True
        if self.dropdown is not None:
            choices = self.editor.choices(self.dropdown)
            if key == "escape":
                self.dropdown = None
            elif key in ("up", "down"):
                self.menu_index = (self.menu_index + (1 if key == "down" else -1)) % len(choices)
            elif key == "enter":
                self._choose(self.menu_index)
            return True
        if self.editor or self.prefix_editor:
            if key == "escape":
                self.editor = self.prefix_editor = None
                self.error = ""
            elif key == "f2" or key == "enter" and self.prefix_editor is not None:
                self._apply()
            elif self.prefix_editor is not None:
                self.prefix_editor.handle(key, data)
            elif key in ("tab", "s-tab"):
                fields = self.editor.fields()
                self.field = fields[(fields.index(self.field) + (1 if key == "tab" else -1)) % len(fields)]
            elif self.editor.buffer(self.field) is not None:
                self.editor.buffer(self.field).handle(key, data)
            elif key == "enter" or data == " ":
                self._focus(self.field)
            return True
        if key == "escape":
            self.area = "prefixes"
            return True
        if self.busy or self.draft is None:
            return True
        command = data.lower() if key in ("any", "<any>") else key
        if key in ("tab", "s-tab"):
            self.area = "items" if self.area == "prefixes" else "prefixes"
        elif key in ("up", "down", "scroll-up", "scroll-down"):
            delta = 1 if key in ("down", "scroll-down") else -1
            if self.area == "prefixes":
                self.prefix_index = max(0, min(len(self.registrations()) - 1, self.prefix_index + delta))
                self.item_index = 0
            else:
                self.item_index = max(0, min(len(self.items()) - 1, self.item_index + delta))
        elif key == "enter":
            if self.area == "prefixes":
                self.area = "items"
            else:
                self._edit_item()
        elif key == "f2":
            self._save()
        elif command == "a":
            self._edit_prefix(True) if self.area == "prefixes" else self._edit_item(True)
        elif command == "r" and self.area == "prefixes":
            self._edit_prefix()
        elif command in ("d", "delete"):
            self._delete()
        elif command == "l":
            self._lock()
        return True

    def scroll_at(self, x, y, amount):
        if self.confirm:
            self._message.scroll(amount)
            self._invalidate()
            return True
        if self.editor or self.prefix_editor:
            return False
        self.area = "prefixes" if x < self._left_width else "items"
        for _ in range(abs(amount)):
            self.key("down" if amount > 0 else "up", "")
        return True

    def dispose(self):
        self.disposed = True

    def _input(self, buffer, cells, theme, active=True):
        text = buffer.text
        cursor = buffer.cursor_position
        if active:
            text = text[:cursor] + "▏" + text[cursor:]
        text = text.replace("\n", " ↵ ")
        while active and "▏" in text and width(text[:text.index("▏") + 1]) > cells:
            text = text[1:]
        return fitted([part(theme, fit(text, cells, marker=""), background=R.INPUT_BG)], cells, theme, background=R.INPUT_BG)

    def _editor_rows(self, cells, theme):
        locale, rows = self._locale(), []
        self._active_row = 0
        if self.prefix_editor is not None:
            rows = [fitted([part(theme, locale.text(M.COMPLETION_PREFIX), R.TITLE)], cells, theme),
                    self._input(self.prefix_editor, cells, theme), fitted([], cells, theme)]
        else:
            for field in self.editor.fields():
                active = self.field == field
                if active:
                    self._active_row = len(rows)
                rows.append(fitted([part(theme, field.label(locale), R.TITLE if active else R.MUTED)], cells, theme))
                buffer = self.editor.buffer(field)
                if buffer is not None:
                    line = self._input(buffer, cells, theme, active)
                    # Clicking the whole text surface selects its editable field.
                    from ...rendering.interactive import click
                    rows.append([(style, text, click(lambda field=field: self._focus(field))) for style, text in line])
                else:
                    choices = self.editor.choices(field)
                    label = next(locale.text(message) for value, message in choices if value == self.editor.value(field))
                    rows.append(fitted([button(theme, label + " ", selected=active,
                                               action=lambda field=field: self._focus(field))], cells, theme))
                if self.dropdown == field:
                    self._active_row = len(rows) + self.menu_index
                    for index, (_, message) in enumerate(self.editor.choices(field)):
                        text = fit(locale.text(message), cells)
                        text += " " * max(0, cells - width(text))
                        role, bg = (R.SELECTED_FG, R.SELECTED_BG) if index == self.menu_index else (R.MENU_TEXT, R.BUTTON_BG)
                        rows.append([part(theme, text, role, background=bg, action=lambda i=index: self._choose(i))])
                rows.append(fitted([], cells, theme))
        return rows

    def _editor_footer(self, cells, rows, theme):
        """Reserve the bottom actions independently of the scrolling fields."""
        blank = fitted([], cells, theme)
        label = ("* " if self.editor_dirty else "") + self._locale().text(M.APPLY)
        actions = centered([button(theme, label, role=R.SAVE, action=self._apply),
                            part(theme, "  "), button(theme, self._locale().text(M.CANCEL),
                            action=lambda: self.key("escape", ""))], cells, theme)
        if rows < 3:
            return [actions, blank][:max(0, rows)]
        return [blank, actions, blank]

    def _draw_editor(self, columns, rows, theme):
        footer = self._editor_footer(columns, rows, theme)
        content_rows = rows - len(footer)
        error_rows = int(bool(self.error) and content_rows > 0)
        viewport_rows = content_rows - error_rows
        body = self._editor_rows(columns, theme)
        # Only fields scroll. The action row remains second from the bottom,
        # regardless of field count, dropdown size, errors or viewport height.
        start = max(0, self._active_row - max(0, viewport_rows - 2))
        visible = body[start:start + viewport_rows]
        visible += [fitted([], columns, theme) for _ in range(viewport_rows - len(visible))]
        if error_rows:
            visible.append(fitted([part(theme, self.error, R.ERROR)], columns, theme))
        return visible + footer

    def _mouse(self, handler):
        def handle(event):
            result = handler(event)
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self._invalidate()
            return result
        return handle

    def draw_body(self, columns, rows, theme):
        # Keyboard events are invalidated by PopupSurface; mouse callbacks also
        # need a redraw when they enter an editor or change a local draft.
        return [[(item[0], item[1], self._mouse(item[2])) if len(item) == 3 else item
                 for item in row] for row in self._draw_body(columns, rows, theme)]

    def _heading(self, area, label, cells, theme):
        selected = self.registration() if area == "prefixes" else self.selected_item()
        locked = selected is not None and selected.locked
        edit = self._edit_prefix if area == "prefixes" else self._edit_item
        actions = ((ManagementAction.ADD, lambda: edit(True)), (ManagementAction.EDIT, edit),
                   (ManagementAction.UNLOCK if locked else ManagementAction.LOCK, self._lock),
                   (ManagementAction.DELETE, self._delete))
        def invoke(action):
            self.area = area
            action()
        controls = []
        for icon, action in actions:
            if controls:
                controls.append(part(theme, " "))
            # Secondary actions use the surrounding popup surface rather than
            # the filled background used by primary buttons.
            controls.append(part(theme, " " + icon + " ",
                R.TITLE if locked and icon == ManagementAction.UNLOCK else R.MUTED,
                action=lambda action=action: invoke(action)))
        controls_width = sum(width(item[1]) for item in controls)
        title = fit(self._locale().text(label), max(0, cells - controls_width - 1))
        gap = max(0, cells - width(title) - controls_width)
        return fitted([part(theme, title, R.TITLE, bold=True), part(theme, " " * gap), *controls], cells, theme)

    def _draw_body(self, columns, rows, theme):
        self._sync()
        locale = self._locale()
        fill = lambda parts: fitted(parts, columns, theme)
        if self.confirm:
            message, name, _ = self.confirm
            def apply():
                self.confirm_yes = True
                self.key("enter", "")
            actions = (PopupAction(locale.text(M.APPLY), apply),
                       PopupAction(locale.text(M.CANCEL), lambda: self.key("escape", "")))
            return self._message.draw(TextRow.plain(locale.text(message, name=name), R.WARNING),
                columns, rows, theme, actions=actions, selected=0 if self.confirm_yes else 1, heading=None)
        if self.editor or self.prefix_editor:
            return self._draw_editor(columns, rows, theme)
        left = min(28, max(1, columns // 3))
        right = max(0, columns - left - 1)
        notice = self.error or (locale.text(M.COMPLETION_BUSY) if self.busy else "")
        self._left_width, self._body_height = left, max(0, rows - 4 - bool(notice))
        self.selected_item()
        left_rows = [self._heading("prefixes", M.COMPLETION_PREFIX, left, theme)]
        right_rows = [self._heading("items", M.COMPLETION_ENTRIES, right, theme)]
        start = max(0, self.prefix_index - self._body_height + 1)
        for index, value in list(enumerate(self.registrations()))[start:start + self._body_height]:
            label = (ManagementAction.LOCK + " " if value.locked else "  ") + self.host.label(value.prefix)
            bg = R.BUTTON_BG if index == self.prefix_index else R.BACKGROUND
            left_rows.append(fitted([part(theme, fit(label, left), R.TITLE if index == self.prefix_index and self.area == "prefixes" else R.FOREGROUND,
                                         background=bg, action=lambda i=index: self._select_prefix(i))], left, theme, background=bg))
        start = max(0, self.item_index - self._body_height + 1)
        for index, item in list(enumerate(self.items()))[start:start + self._body_height]:
            state = ManagementAction.LOCK + " " if item.locked else "  "
            name_width = min(24, max(8, right // 3))
            name = fit(state + item.name, name_width)
            label = name + " " * max(1, name_width - width(name) + 2) + item.help
            bg = R.BUTTON_BG if index == self.item_index else R.BACKGROUND
            right_rows.append(fitted([part(theme, fit(label, right), R.TITLE if index == self.item_index and self.area == "items" else R.FOREGROUND,
                                          background=bg, action=lambda i=index: self._select_item(i))], right, theme, background=bg))
        if not self.items():
            right_rows.append(fitted([part(theme, locale.text(M.COMPLETION_EMPTY), R.MUTED)], right, theme))
        body = []
        for index in range(self._body_height + 1):
            body.append(fill([*(left_rows[index] if index < len(left_rows) else fitted([], left, theme)),
                              part(theme, "│", R.SEPARATOR),
                              *(right_rows[index] if index < len(right_rows) else fitted([], right, theme))]))
        if notice:
            body.append(fill([part(theme, notice, R.ERROR if self.error else R.MUTED)]))
        body.append(fill([]))
        label = ("* " if self.dirty else "") + locale.text(M.SAVE)
        body.append(centered([button(theme, label, role=R.SAVE, action=self._save), part(theme, "  "),
                              button(theme, locale.text(M.CLOSE), action=self._dismiss)], columns, theme))
        body.append(fill([]))
        return body[:rows]
