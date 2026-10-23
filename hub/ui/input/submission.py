"""A consumed draft stays recoverable until durable admission acknowledges it."""

from hub.locales.keys import MessageKey

from dataclasses import dataclass


@dataclass(slots=True)
class SubmittedDraft:
    project_id: str
    session_id: str
    text: str

    def consume(self, view) -> None:
        if view._drafts.get(self.session_id) == self.text:
            view._drafts[self.session_id] = ""
        if (view.project_id, view.sessions[view.selected].id) == (self.project_id, self.session_id):
            if view.question.item is None and view.composer.text == self.text:
                view.composer.buffer.cancel_completion()
                view.composer.text = ""
                view._drafts[self.session_id] = ""

    def restore(self, view) -> None:
        current = (view.project_id, view.sessions[view.selected].id) == (self.project_id, self.session_id)
        if current and view.question.item is None and view.composer.text == self.text:
            return
        if current and view.question.item is None and not view.composer.text:
            view.composer.text = self.text
            view._drafts[self.session_id] = self.text
        elif not view._drafts.get(self.session_id) and (not current or view.question.item is not None):
            view._drafts[self.session_id] = self.text
        else:
            view.input_history.recover(self.session_id, self.text)
            if current:
                view.notice += " · " + view.t(MessageKey.DRAFT_RECOVERED)
