"""GUI-style editing for writable Hub controls, independent of shell keymaps."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.selection import SelectionState
from .input_contract import EditableControl
from ..ui.input.keys import KeyName


class TextEditing:
    GLOBAL = True
    EAGER = True
    RECORD_UNDO = False
    UNDO = False
    REDO = True
    BUFFER_START = 0
    NO_KEY = None
    NEWLINE = "\n"
    CRLF = "\r\n"
    CR = "\r"
    CURSOR_METHOD_PREFIX = "cursor_"
    DELETION_KEYS = (KeyName.BACKSPACE, KeyName.DELETE)
    HORIZONTAL_KEYS = (KeyName.LEFT, KeyName.RIGHT)
    LINE_KEYS = (KeyName.HOME, KeyName.END)
    WORD_KEYS = (KeyName.CONTROL_LEFT, KeyName.CONTROL_RIGHT)
    EXPLICIT_MOVEMENT = (
        *LINE_KEYS,
        *WORD_KEYS,
        KeyName.CONTROL_HOME,
        KeyName.CONTROL_END,
    )
    SELECTION_MOVEMENT = (
        *HORIZONTAL_KEYS,
        KeyName.UP,
        KeyName.DOWN,
        *EXPLICIT_MOVEMENT,
    )

    def __init__(self, visible):
        self.bindings = KeyBindings()

        def editable():
            control = get_app().layout.current_control
            return visible() and EditableControl.writable(control)

        active = Condition(editable)
        selected = active & Condition(
            lambda: bool(get_app().current_buffer.selection_state)
        )
        add = self.bindings.add
        add(KeyName.CONTROL_A, filter=active, is_global=self.GLOBAL, eager=self.EAGER)(
            self.select_all
        )
        add(KeyName.CONTROL_X, filter=active, is_global=self.GLOBAL, eager=self.EAGER)(
            self.cut
        )
        add(
            KeyName.CONTROL_Z,
            filter=active,
            is_global=self.GLOBAL,
            eager=self.EAGER,
            save_before=lambda e: self.RECORD_UNDO,
        )(lambda e: self.undo(e))
        add(
            KeyName.CONTROL_Y,
            filter=active,
            is_global=self.GLOBAL,
            eager=self.EAGER,
            save_before=lambda e: self.RECORD_UNDO,
        )(lambda e: self.undo(e, redo=self.REDO))
        for key in self.DELETION_KEYS:
            add(key, filter=selected, is_global=self.GLOBAL)(
                lambda e: e.current_buffer.cut_selection()
            )
        add(Keys.Any, filter=selected, is_global=self.GLOBAL)(self.replace)
        add(Keys.BracketedPaste, filter=active, is_global=self.GLOBAL)(self.paste)
        for key in self.SELECTION_MOVEMENT:
            add(key, filter=selected, is_global=self.GLOBAL)(self.collapse)
            add(KeyName.shifted(key), filter=active, is_global=self.GLOBAL)(self.extend)
        # These otherwise depend on the host's Emacs/Vi editing mode.
        for key in self.EXPLICIT_MOVEMENT:
            add(key, filter=active & ~selected, is_global=self.GLOBAL)(self.move)

    @staticmethod
    def select_all(event):
        buffer = event.current_buffer
        if buffer.text:
            buffer.selection_state = SelectionState(TextEditing.BUFFER_START)
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
    def undo(event, redo=UNDO):
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
        text = event.data.replace(TextEditing.CRLF, TextEditing.NEWLINE).replace(
            TextEditing.CR, TextEditing.NEWLINE
        )
        from .clipboard import SystemClipboard
        from prompt_toolkit.clipboard import ClipboardData

        if isinstance(event.app.clipboard, SystemClipboard):
            event.app.clipboard.remember(ClipboardData(text))
        if buffer.selection_state:
            buffer.cut_selection()
        buffer.insert_text(text)

    @staticmethod
    def move(event, key=NO_KEY):
        buffer = event.current_buffer
        key = key or event.key_sequence[-1].key
        document = buffer.document
        if key == KeyName.CONTROL_HOME:
            buffer.cursor_position = TextEditing.BUFFER_START
        elif key == KeyName.CONTROL_END:
            buffer.cursor_position = len(buffer.text)
        elif key in TextEditing.LINE_KEYS:
            buffer.cursor_position += (
                document.get_start_of_line_position()
                if key == KeyName.HOME
                else document.get_end_of_line_position()
            )
        elif key in TextEditing.WORD_KEYS:
            buffer.cursor_position += (
                document.find_previous_word_beginning() or -buffer.cursor_position
                if key == KeyName.CONTROL_LEFT
                else document.find_next_word_ending() or len(document.text_after_cursor)
            )
        else:
            getattr(buffer, TextEditing.CURSOR_METHOD_PREFIX + key)()

    def extend(self, event):
        buffer = event.current_buffer
        if not buffer.selection_state:
            buffer.start_selection()
        buffer.selection_state.enter_shift_mode()
        self.move(
            event, KeyName.unshifted(event.key_sequence[-1].key)
        )
        if buffer.cursor_position == buffer.selection_state.original_cursor_position:
            buffer.exit_selection()

    def collapse(self, event):
        buffer = event.current_buffer
        start, end = buffer.document.selection_range()
        buffer.exit_selection()
        key = event.key_sequence[-1].key
        if key in self.HORIZONTAL_KEYS:
            buffer.cursor_position = start if key == KeyName.LEFT else end
        else:
            self.move(event)
