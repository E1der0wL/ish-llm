"""A consumed draft stays recoverable until durable admission acknowledges it."""

from hub.locales.keys import MessageKey

from dataclasses import dataclass


@dataclass(slots=True)
class SubmittedDraft:
    EMPTY_TEXT = ""
    ABSENT = None
    RECOVERY_SEPARATOR = " · "
    project_id: str
    session_id: str
    text: str

    def is_current(self, view):
        return (view.project_id, view.sessions[view.selected].id) == (
            self.project_id,
            self.session_id,
        )

    def consume(self, view) -> None:
        if view._drafts.get(self.session_id) == self.text:
            view._drafts[self.session_id] = self.EMPTY_TEXT
        if self.is_current(view):
            if view.question.item is self.ABSENT and view.composer.text == self.text:
                view.composer.buffer.cancel_completion()
                view.composer.text = self.EMPTY_TEXT
                view._drafts[self.session_id] = self.EMPTY_TEXT

    def restore(self, view) -> None:
        current = self.is_current(view)
        if (
            current
            and view.question.item is self.ABSENT
            and view.composer.text == self.text
        ):
            return
        if current and view.question.item is self.ABSENT and not view.composer.text:
            view.composer.text = self.text
            view._drafts[self.session_id] = self.text
        elif not view._drafts.get(self.session_id) and (
            not current or view.question.item is not self.ABSENT
        ):
            view._drafts[self.session_id] = self.text
        else:
            view.input_history.recover(self.session_id, self.text)
            if current:
                view.notice += self.RECOVERY_SEPARATOR + view.t(
                    MessageKey.DRAFT_RECOVERED
                )
