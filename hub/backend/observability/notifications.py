"""Bounded, deduplicated notifications shared by events and snapshot reconciliation."""

from llm.core.models import RunStatus
from collections import deque
from collections.abc import Mapping
from typing import Any

from ...model import SessionNotification, QuestionPresentation
from ..execution.engine_selection import HubEngineIdentity


class NotificationBuffer:
    """One backend loop owns identities, observed states and notification sequence."""

    ABSENT = None
    MAX_ITEMS = 64
    MAX_KEYS = 128
    FIRST_SEQUENCE = 0
    SEQUENCE_INCREMENT = 1
    QUESTION_IDENTITY = "question"

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, str]] = {}
        self._runs: dict[str, tuple[str, str] | None] = {}
        self._live_questions: set[str] = set()
        self._items: deque[SessionNotification] = deque(maxlen=self.MAX_ITEMS)
        self._keys: deque[tuple[str, str]] = deque(maxlen=self.MAX_KEYS)
        self._sequence = self.FIRST_SEQUENCE

    def _record(
        self,
        key: tuple[str, str],
        project_id: str,
        session_id: str,
        title: str,
        status: str,
    ) -> None:
        if key in self._keys:
            return
        self._keys.append(key)
        self._sequence += self.SEQUENCE_INCREMENT
        self._items.append(
            SessionNotification(self._sequence, project_id, session_id, title, status)
        )

    def _record_run(self, project_id: str, title: str, run: Any) -> None:
        state = (run.id, str(run.status))
        self._runs[run.session_id] = state
        self._record(state, project_id, run.session_id, title, str(run.status))

    def register_session(self, project_id: str, session_id: str, title: str) -> None:
        self._sessions[session_id] = (project_id, title)

    def forget_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, self.ABSENT)
        self._runs.pop(session_id, self.ABSENT)

    def record_run(self, run: Any) -> None:
        identity = self._sessions.get(run.session_id)
        already_paused = str(run.status) == RunStatus.PAUSED and self._runs.get(
            run.session_id
        ) == (run.id, RunStatus.PAUSED.value)
        if (
            identity is not self.ABSENT
            and not run.engine.startswith(HubEngineIdentity.INTERNAL_PREFIX)
            and not already_paused
        ):
            self._record_run(*identity, run)

    def record_question(
        self, project_id: str, session_id: str, title: str, item: dict
    ) -> None:
        from ..interactions.questions import waiting_status

        self._record(
            (self.QUESTION_IDENTITY, QuestionPresentation.observe(item).request.id),
            project_id,
            session_id,
            title,
            waiting_status((item,)),
        )

    def observe_questions(self, pending: Mapping[str, Any]) -> None:
        for identifier, item in pending.items():
            if identifier not in self._live_questions:
                _, title = self._sessions.get(
                    item.session_id, (item.project_id, item.session_id)
                )
                self.record_question(
                    item.project_id,
                    item.session_id,
                    title,
                    QuestionPresentation.request_only(item.request),
                )
        self._live_questions = set(pending)

    def observe_session(
        self,
        project_id: str,
        session_id: str,
        title: str,
        run: Any,
        questions: tuple = (),
    ) -> None:
        self.register_session(project_id, session_id, title)
        if run is not self.ABSENT:
            state = (run.id, str(run.status))
            if session_id in self._runs and self._runs[session_id] != state:
                self._record_run(project_id, title, run)
            else:
                self._runs[session_id] = state
        elif session_id not in self._runs:
            self._runs[session_id] = self.ABSENT
        for question in questions:
            self.record_question(project_id, session_id, title, question)

    def items(self) -> tuple[SessionNotification, ...]:
        return tuple(self._items)
