"""Shared popup text editing and dirty state; feature owners validate values."""
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document


class EditBuffer(Buffer):
    def __init__(self, text="", *, multiline=False):
        super().__init__(document=Document(text, len(text)), multiline=multiline)
        self._initial_text = text

    @property
    def dirty(self):
        return self.text != self._initial_text

    def handle(self, key, data):
        if key in ("<any>", "any", "<bracketed-paste>") or key == "enter" and self.multiline():
            text = "\n" if key == "enter" else data.replace("\r\n", "\n").replace("\r", "\n")
            if not self.multiline():
                text = text.replace("\n", " ")
            text = "".join(char for char in text if char == "\n" or char >= " " and char != "\x7f")
            self.save_to_undo_stack()
            self.insert_text(text)
        elif key in ("backspace", "c-h", "delete", "c-u", "c-k"):
            self.save_to_undo_stack()
            if key in ("backspace", "c-h", "c-u"):
                self.delete_before_cursor(self.cursor_position if key == "c-u" else 1)
            else:
                self.delete(len(self.text) - self.cursor_position if key == "c-k" else 1)
        elif key in ("left", "right", "up", "down"):
            getattr(self, "cursor_" + key)()
        elif key in ("home", "c-a", "end", "c-e"):
            self.cursor_position = 0 if key in ("home", "c-a") else len(self.text)
        elif key in ("c-z", "c-y"):
            self.undo() if key == "c-z" else self.redo()
