"""Prefix editor with direct mode switching; the host owns its close shortcut."""
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from ...core.theme import ThemeRole
from ...locales import Locale, Message as M
from ...popups.models import PopupContent
from ...rendering.interactive import InteractivePopup, PopupChrome, click, fitted, part
from ...rendering.text import width, fit
from .models import MergeMode
from .runtime import Request


class CompletionPage(InteractivePopup):
    chrome = PopupChrome.BODY_ONLY

    def __init__(self, runtime, host, register, invalidate, *, command="", locale=Locale):
        self.runtime, self.host = runtime, host
        self._register, self._invalidate, self._locale = register, invalidate, locale
        self.command = command
        text = host.suggest_prefix(command)
        self.prefix = Buffer(document=Document(text, len(text)))
        self.mode = MergeMode.MERGE
        self.error = ""

    def content(self):
        return PopupContent.plain("", "")

    def status_text(self):
        return self._locale().text(M.COMPLETION_REGISTER_HINT)

    def _submit(self):
        if self.runtime.busy:
            return
        self.error = ""
        try:
            self._register(Request(self.host.prefix(self.prefix.text), self.command, self.mode))
        except (ValueError, OSError) as exc:
            self.error = str(exc)
        self._invalidate()

    def _edit(self, key, data):
        buffer = self.prefix
        if key in ("<any>", "any", "<bracketed-paste>"):
            text = data.replace("\n", " ").replace("\r", " ")
            if text and all(char >= " " for char in text):
                buffer.save_to_undo_stack()
                buffer.insert_text(text)
        elif key in ("backspace", "c-h", "delete", "c-u", "c-k"):
            buffer.save_to_undo_stack()
            if key in ("backspace", "c-h", "c-u"):
                buffer.delete_before_cursor(count=buffer.cursor_position if key == "c-u" else 1)
            else:
                buffer.delete(count=len(buffer.text) - buffer.cursor_position if key == "c-k" else 1)
        elif key in ("left", "right"):
            buffer.cursor_left() if key == "left" else buffer.cursor_right()
        elif key in ("home", "c-a", "end", "c-e"):
            buffer.cursor_position = 0 if key in ("home", "c-a") else len(buffer.text)
        elif key in ("c-z", "c-y"):
            buffer.undo() if key == "c-z" else buffer.redo()

    def key(self, key, data):
        if key == "escape":
            return False
        if key == "enter":
            self._submit()
        elif key in ("tab", "s-tab"):
            self.mode = MergeMode.REPLACE if self.mode == MergeMode.MERGE else MergeMode.MERGE
        else:
            self._edit(key, data)
            self.error = ""
        return True

    def _choose(self, mode):
        self.mode = mode
        self._invalidate()

    def draw_body(self, columns, rows, theme):
        locale = self._locale()
        label = locale.text(M.COMPLETION_PREFIX) + "  "
        available = max(1, columns - width(label))
        text, cursor = self.prefix.text, self.prefix.cursor_position
        text = text[:cursor] + "▏" + text[cursor:]
        while width(text[:cursor + 1]) > available and text:
            text, cursor = text[1:], cursor - 1
        text = fit(text, available, marker="")
        normal = fitted([part(theme, label, ThemeRole.ERROR if self.error else ThemeRole.MUTED),
            part(theme, text + " " * max(0, available - width(text)), background=ThemeRole.INPUT_BG)], columns, theme)
        choices = [part(theme, " " * width(label))]
        gap = min(2, max(0, available - 2))
        first_width = (available - gap) // 2
        for index, (mode, message) in enumerate(((MergeMode.MERGE, M.COMPLETION_MERGE), (MergeMode.REPLACE, M.COMPLETION_REPLACE))):
            if index:
                choices.append(part(theme, " " * gap))
            cells = first_width if index == 0 else available - gap - first_width
            selected = mode == self.mode
            text = fit(("● " if selected else "○ ") + locale.text(message), cells, marker="")
            left = max(0, (cells - width(text)) // 2)
            text = " " * left + text + " " * max(0, cells - left - width(text))
            choices.append((theme.button(selected=selected, bold=selected), text,
                            click(lambda mode=mode: self._choose(mode))))
        body = [normal, fitted(choices, columns, theme)]
        if rows >= 3:
            body.insert(1, fitted([], columns, theme))
        return body[:rows] + [fitted([], columns, theme) for _ in range(max(0, rows - len(body)))]
