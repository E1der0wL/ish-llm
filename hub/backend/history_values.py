"""Existing request-history presentation records, shared by their Writers and readers."""

from llm.core.models import Run, RunStatus, Message, MessageRole, MessageStatus
from llm.core.contracts import ResourceRef


class HistoryCommand:
    TURNS = "turns"
    EDIT = "edit_turn"
    RETRY = "retry_turn"
    DELETE = "delete_turn"
    DELETE_MANY = "delete_turns"
    DELETE_PLAN = "delete_turns_plan"


class HistoryRun:
    AGENT_ID = Run.agent_id.__name__
    RUN_ID = ResourceRef.run_id.__name__
    STATUS = Run.status.__name__
    ENGINE = Run.engine.__name__
    ELAPSED = "elapsed"
    ERROR = Run.error.__name__
    EMPTY = ""
    ABSENT = None

    def __init__(self, value):
        self._value = value

    @classmethod
    def from_run(cls, identifier, run, elapsed):
        return {
            cls.AGENT_ID: identifier,
            cls.RUN_ID: run.id,
            cls.STATUS: str(run.status),
            cls.ENGINE: run.engine,
            cls.ELAPSED: elapsed,
            cls.ERROR: str(run.error or cls.EMPTY),
        }

    @classmethod
    def queued(cls, identifier, source, admission):
        return {
            cls.AGENT_ID: identifier,
            cls.RUN_ID: cls.EMPTY,
            cls.STATUS: str(source.status),
            cls.ENGINE: admission.binding(identifier).engine,
            cls.ELAPSED: cls.ABSENT,
            cls.ERROR: cls.EMPTY,
        }

    @property
    def agent_id(self):
        return self._value[self.AGENT_ID]

    @property
    def run_id(self):
        return self._value[self.RUN_ID]

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def engine(self):
        return self._value[self.ENGINE]

    @property
    def elapsed(self):
        return self._value[self.ELAPSED]

    @property
    def error(self):
        return self._value[self.ERROR]


class HistoryRow:
    ID = "id"
    REQUEST_ID = "request_id"
    TEXT = "text"
    TIME = "time"
    STATUS = HistoryRun.STATUS
    AGENT_ID = HistoryRun.AGENT_ID
    RUNS = "runs"
    ENGINE = HistoryRun.ENGINE
    ELAPSED = HistoryRun.ELAPSED
    RESPONSE = "response"
    ERROR = HistoryRun.ERROR
    NAMES = ", "
    PARAGRAPH = "\n\n"
    LINE = "\n"
    ABSENT = None
    DISPLAY_STATUS_PRIORITY = (
        RunStatus.RUNNING,
        RunStatus.PENDING,
        MessageStatus.QUEUED,
        RunStatus.PAUSED,
        RunStatus.FAILED,
        RunStatus.INTERRUPTED,
        RunStatus.CANCELLED,
        RunStatus.COMPLETED,
    )

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, turn, request, attempts, recipients, status_priority):
        records = [message for group in turn.active for message in group]
        assistants = [
            message for message in records if message.role == MessageRole.ASSISTANT
        ]
        fallback = assistants[-1].status if assistants else request.status
        statuses = {HistoryRun(item).status for item in recipients}
        status = next(
            (value for value in status_priority if value in statuses),
            fallback,
        )
        elapsed = [
            HistoryRun(item).elapsed
            for item in recipients
            if HistoryRun(item).elapsed is not cls.ABSENT
        ]
        return {
            cls.ID: turn.id,
            cls.REQUEST_ID: request.id,
            cls.TEXT: turn.active_request.content,
            cls.TIME: turn.initial.created_at,
            cls.STATUS: str(status),
            cls.AGENT_ID: cls.NAMES.join(attempts),
            cls.RUNS: recipients,
            cls.ENGINE: cls.NAMES.join(
                dict.fromkeys(HistoryRun(item).engine for item in recipients)
            ),
            cls.ELAPSED: max(elapsed) if elapsed else cls.ABSENT,
            cls.RESPONSE: cls.PARAGRAPH.join(message.content for message in assistants),
            cls.ERROR: cls.LINE.join(
                HistoryRun(item).error for item in recipients if HistoryRun(item).error
            ),
        }

    def request_id(self, default=ABSENT):
        return self._value.get(self.REQUEST_ID, default)

    @property
    def id(self):
        return self._value[self.ID]

    @property
    def text(self):
        return self._value[self.TEXT]

    @property
    def time(self):
        return self._value[self.TIME]

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def agent_id(self):
        return self._value[self.AGENT_ID]

    @property
    def runs(self):
        return self._value[self.RUNS]

    @property
    def engine(self):
        return self._value[self.ENGINE]

    @property
    def elapsed(self):
        return self._value[self.ELAPSED]

    @property
    def response(self):
        return self._value[self.RESPONSE]

    @property
    def error(self):
        return self._value[self.ERROR]
