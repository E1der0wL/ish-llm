"""Runtime state transitions for visibility and one prompt submission."""

from dataclasses import dataclass
from enum import StrEnum


class SubmissionPhase(StrEnum):
    READY = "ready"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"


@dataclass(slots=True)
class DockState:
    requested_visible: bool = False
    closed: bool = False
    diagnostic: str = ""
    phase: SubmissionPhase = SubmissionPhase.READY
    _prompt_token: object = None

    def enter_prompt(self, token: object) -> None:
        if token is not self._prompt_token:
            self._prompt_token = token
            self.phase = SubmissionPhase.READY

    def toggle(self) -> None:
        self.show(not self.requested_visible)

    def show(self, visible: bool = True) -> None:
        if not self.closed:
            self.requested_visible = bool(visible)

    def can_render(self, *, running: bool, done: bool) -> bool:
        return self.requested_visible and not self.closed and running and not done

    def begin_submission(self) -> bool:
        if self.closed or self.phase != SubmissionPhase.READY:
            return False
        self.phase = SubmissionPhase.SUBMITTING
        self.diagnostic = ""
        return True

    def finish_submission(self, *, accepted: bool, error: str = "") -> None:
        self.phase = SubmissionPhase.SUBMITTED if accepted else SubmissionPhase.READY
        self.diagnostic = error

    def report(self, message: object) -> None:
        self.diagnostic = str(message)

    def close(self) -> None:
        self.closed = True
        self.requested_visible = False
