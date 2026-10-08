"""Session-local composer recall; editing a recalled request exits navigation."""

from prompt_toolkit.document import Document


class ComposerHistory:
    def __init__(self, view):
        self.view = view
        self._key = None
        self._items: tuple[str, ...] = ()
        self._index: int | None = None
        self._changing = False
        view.composer.buffer.on_text_changed += self._edited

    @property
    def browsing(self):
        key = self.view._draft_key(self.view.selected)
        if self._key != key:
            self._index = None
        return self._index is not None

    @property
    def available(self):
        buffer = self.view.composer.buffer
        return (self.view.question.item is None and buffer.complete_state is None
                and (not buffer.text or self.browsing))

    def _edited(self, _):
        if not self._changing:
            self._index = None

    def move(self, direction: int):
        if not self.available:
            return
        if not self.browsing:
            self._key = self.view._draft_key(self.view.selected)
            self._items = tuple(message.text for message in self.view.sessions[self.view.selected].messages
                                if message.role == "user" and message.text.strip())
            self._index = len(self._items)
        self._index = max(0, min(len(self._items), self._index + direction))
        value = self._items[self._index] if self._index < len(self._items) else ""
        self._changing = True
        try:
            self.view.composer.buffer.cancel_completion()
            self.view.composer.buffer.set_document(Document(value, len(value)))
        finally:
            self._changing = False
