"""Model-only Tool result projections and transient summary outcomes."""

from .compaction_records import CompactionApplied
from .tool_values import MemoryToolDefinition


class ToolSummaryExecution:
    SCOPE = None
    NO_APPLIED = None
    NO_ERROR = None
    EMPTY_SUMMARY = ""
    STEP = "Tool result summary"
    RESUME_REQUIRED = "Explicit summary resume required"
    ENSURE_ASCII = False


class ToolSummaryIdentity:
    SCOPE = "scope"
    CALL_ID = "call_id"
    TEXT = "text"
    MODE = "mode"
    OPTIONS = "options"
    DESTINATION = "destination"

    @classmethod
    def create(cls, *, scope, call_id, text, mode, options, destination):
        return {cls.SCOPE: scope, cls.CALL_ID: call_id, cls.TEXT: text,
                cls.MODE: mode, cls.OPTIONS: options, cls.DESTINATION: destination}


class ToolSummaryOutcome:
    COMPLETE = "complete"
    TEXT = "text"
    NO_APPLIED = None

    def __init__(self):
        self._value = {}

    def __bool__(self):
        return bool(self._value)

    def record(self, summary):
        self._value.update({self.COMPLETE: summary.complete, self.TEXT: summary.text})

    @property
    def applied(self):
        return CompactionApplied.from_summary(self._value[self.TEXT]) if self._value.get(self.COMPLETE) else self.NO_APPLIED


class ToolSummaryObservation:
    CANCELLED = "cancelled"
    REQUEST_ID = "request_id"
    COMPLETED = "completed"
    OFFSET = "offset"
    BLOCKED = "blocked"
    CANCELLATION_OBSERVED = True

    @classmethod
    def cancelled(cls):
        return {cls.CANCELLED: cls.CANCELLATION_OBSERVED}

    @classmethod
    def progress(cls, identifier, summary):
        return {cls.REQUEST_ID: identifier, cls.COMPLETED: summary.complete,
                cls.OFFSET: summary.offset, cls.BLOCKED: summary.blocked}


class ToolSummaryView:
    REFERENCE = "reference"
    ID = "id"
    TOOL = "tool"
    REVISION = "revision"
    ORIGINAL_CHARS = "original_chars"
    SOURCE_CALL_ID = "source_call_id"
    NOTICE = "notice"
    REFERENCE_NOTICE = "Derived reference data; original Tool result is unchanged."

    @classmethod
    def create(cls, state, *, identifier, text, call_id):
        return {cls.REFERENCE: {cls.ID: identifier, cls.TOOL: MemoryToolDefinition.Name.READ_SOURCE,
                    cls.REVISION: state.request.reference_revision},
                cls.ORIGINAL_CHARS: len(text), cls.SOURCE_CALL_ID: call_id,
                cls.NOTICE: cls.REFERENCE_NOTICE, **state.applied_record}
