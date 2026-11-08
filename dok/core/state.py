"""Runtime state transitions for visibility and one prompt submission."""

from dataclasses import dataclass
from enum import StrEnum


class SubmissionPhase(StrEnum):
    READY = "ready"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"


class DockMode(StrEnum):
    HIDDEN = "hidden"
    BASIC = "basic"
    MAXIMIZED = "maximized"


@dataclass(slots=True)
class DockState:
    mode: DockMode = DockMode.HIDDEN
    _last_mode: DockMode = DockMode.MAXIMIZED
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

    @property
    def requested_visible(self):
        return self.mode != DockMode.HIDDEN

    def set_mode(self, mode):
        mode = DockMode(mode)
        if not self.closed:
            self.mode = mode
            if mode != DockMode.HIDDEN:
                self._last_mode = mode

    def toggle_size(self):
        self.set_mode(DockMode.BASIC if self.mode == DockMode.MAXIMIZED else DockMode.MAXIMIZED)

    def show(self, visible: bool = True) -> None:
        self.set_mode(self._last_mode if visible else DockMode.HIDDEN)

    def can_render(self, *, running: bool, done: bool) -> bool:
        return self.requested_visible and not self.closed and running and not done

    def begin_submission(self) -> bool:
        if self.closed or self.phase != SubmissionPhase.READY:
            return False
        self.phase = SubmissionPhase.SUBMITTING
        self.diagnostic = ""
        return True

    def can_submit(self, token: object) -> bool:
        return not self.closed and (token is not self._prompt_token or self.phase == SubmissionPhase.READY)

    def finish_submission(self, *, accepted: bool, error: str = "") -> None:
        self.phase = SubmissionPhase.SUBMITTED if accepted else SubmissionPhase.READY
        self.diagnostic = error

    def report(self, message: object) -> None:
        self.diagnostic = str(message)

    def close(self) -> None:
        self.set_mode(DockMode.HIDDEN)
        self.closed = True
