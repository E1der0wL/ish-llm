"""Session-local composer recall; editing a recalled request exits navigation."""

from llm.core.models import MessageStatus, MessageRole
from hub.model import SessionIdentity
from prompt_toolkit.document import Document


class ComposerHistory:
    UNSELECTED = None
    ABSENT = None
    IDLE = False
    CHANGING = True
    RECOVERED_LIMIT = 20
    EMPTY_TEXT = ""

    def __init__(self, view):
        self.view = view
        self._key = self.UNSELECTED
        self._items: tuple[str, ...] = ()
        self._index: int | None = self.UNSELECTED
        self._changing = self.IDLE
        self._recovered = {}
        view.composer.buffer.on_text_changed += self._edited

    @property
    def browsing(self):
        key = self.view._draft_key(self.view.selected)
        if self._key != key:
            self._index = self.UNSELECTED
        return self._index is not self.UNSELECTED

    @property
    def available(self):
        buffer = self.view.composer.buffer
        key = SessionIdentity.current(self.view).to_tuple()
        return (
            key not in self.view.deleting.pending
            and self.view.question.item is self.ABSENT
            and buffer.complete_state is self.ABSENT
            and (not buffer.text or self.browsing)
        )

    def _edited(self, _):
        if not self._changing:
            self._index = self.UNSELECTED

    def recover(self, key: str, text: str) -> None:
        items = self._recovered.setdefault(key, [])
        if not items or items[-1] != text:
            items.append(text)
        del items[: -self.RECOVERED_LIMIT]

    def discard(self, key: str, texts: set[str]) -> None:
        """Forget deleted requests without consuming a separately edited draft."""
        self._recovered[key] = [
            text for text in self._recovered.get(key, ()) if text not in texts
        ]
        if self._key == key:
            browsing = self.browsing
            self._items, self._index = (), self.UNSELECTED
            if (
                browsing
                and self.view._draft_key(self.view.selected) == key
                and self.view.composer.text in texts
            ):
                self.view.composer.text = self.EMPTY_TEXT
                self.view._drafts[key] = self.EMPTY_TEXT

    def move(self, direction: int):
        if not self.available:
            return
        if not self.browsing:
            self._key = self.view._draft_key(self.view.selected)
            self._items = tuple(
                message.text
                for message in self.view.sessions[self.view.selected].messages
                if message.role == MessageRole.USER
                and message.status != MessageStatus.CANCELLED
                and message.text.strip()
            )
            self._items += tuple(self._recovered.get(self._key, ()))
            self._index = len(self._items)
        self._index = max(0, min(len(self._items), self._index + direction))
        value = (
            self._items[self._index]
            if self._index < len(self._items)
            else self.EMPTY_TEXT
        )
        self._changing = self.CHANGING
        try:
            self.view.composer.buffer.cancel_completion()
            self.view.composer.buffer.set_document(Document(value, len(value)))
        finally:
            self._changing = self.IDLE
