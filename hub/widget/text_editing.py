"""GUI-style editing for writable Hub controls, independent of shell keymaps."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.selection import SelectionState


class TextEditing:
    def __init__(self, visible):
        self.bindings = KeyBindings()
        def editable():
            control = get_app().layout.current_control
            return (visible() and getattr(control, "hub_editable", False)
                    and not control.buffer.read_only())
        active = Condition(editable)
        selected = active & Condition(lambda: bool(get_app().current_buffer.selection_state))
        add = self.bindings.add
        add("c-a", filter=active, is_global=True, eager=True)(self.select_all)
        add("c-x", filter=active, is_global=True, eager=True)(self.cut)
        add("c-z", filter=active, is_global=True, eager=True,
            save_before=lambda e: False)(lambda e: self.undo(e))
        add("c-y", filter=active, is_global=True, eager=True,
            save_before=lambda e: False)(lambda e: self.undo(e, redo=True))
        for key in ("backspace", "delete"):
            add(key, filter=selected, is_global=True)(lambda e: e.current_buffer.cut_selection())
        add(Keys.Any, filter=selected, is_global=True)(self.replace)
        add(Keys.BracketedPaste, filter=active, is_global=True)(self.paste)
        for key in ("left", "right", "up", "down", "home", "end", "c-left", "c-right", "c-home", "c-end"):
            add(key, filter=selected, is_global=True)(self.collapse)
            add("s-" + key if not key.startswith("c-") else "c-s-" + key[2:],
                filter=active, is_global=True)(self.extend)
        # These otherwise depend on the host's Emacs/Vi editing mode.
        for key in ("home", "end", "c-left", "c-right", "c-home", "c-end"):
            add(key, filter=active & ~selected, is_global=True)(self.move)

    @staticmethod
    def select_all(event):
        buffer = event.current_buffer
        if buffer.text:
            buffer.selection_state = SelectionState(0)
            buffer.selection_state.enter_shift_mode()
            buffer.cursor_position = len(buffer.text)

    @staticmethod
    def cut(event):
        buffer = event.current_buffer
        if buffer.selection_state:
            # SystemClipboard updates its in-memory register immediately and
            # exports asynchronously, so editing never waits for the desktop.
            event.app.clipboard.set_data(buffer.cut_selection())

    @staticmethod
    def undo(event, redo=False):
        buffer = event.current_buffer
        buffer.exit_selection()
        buffer.redo() if redo else buffer.undo()

    @staticmethod
    def replace(event):
        if event.data and event.data.isprintable():
            event.current_buffer.cut_selection()
            event.current_buffer.insert_text(event.data)

    @staticmethod
    def paste(event):
        buffer = event.current_buffer
        text = event.data.replace("\r\n", "\n").replace("\r", "\n")
        from .clipboard import SystemClipboard
        from prompt_toolkit.clipboard import ClipboardData
        if isinstance(event.app.clipboard, SystemClipboard):
            event.app.clipboard.remember(ClipboardData(text))
        if buffer.selection_state:
            buffer.cut_selection()
        buffer.insert_text(text)

    @staticmethod
    def move(event, key=None):
        buffer = event.current_buffer
        key = key or event.key_sequence[-1].key
        document = buffer.document
        if key == "c-home":
            buffer.cursor_position = 0
        elif key == "c-end":
            buffer.cursor_position = len(buffer.text)
        elif key in ("home", "end"):
            buffer.cursor_position += (document.get_start_of_line_position() if key == "home"
                                       else document.get_end_of_line_position())
        elif key in ("c-left", "c-right"):
            buffer.cursor_position += (document.find_previous_word_beginning() or -buffer.cursor_position
                                       if key == "c-left" else document.find_next_word_ending() or len(document.text_after_cursor))
        else:
            getattr(buffer, "cursor_" + key)()

    def extend(self, event):
        buffer = event.current_buffer
        if not buffer.selection_state:
            buffer.start_selection()
        buffer.selection_state.enter_shift_mode()
        self.move(event, event.key_sequence[-1].key.replace("s-", ""))
        if buffer.cursor_position == buffer.selection_state.original_cursor_position:
            buffer.exit_selection()

    def collapse(self, event):
        buffer = event.current_buffer
        start, end = buffer.document.selection_range()
        buffer.exit_selection()
        key = event.key_sequence[-1].key
        if key in ("left", "right"):
            buffer.cursor_position = start if key == "left" else end
        else:
            self.move(event)
