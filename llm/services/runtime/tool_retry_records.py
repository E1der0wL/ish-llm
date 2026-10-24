"""Tool retry checkpoint state owned by the runtime; no implicit retry policy."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields

from llm.core.checkpoints import CheckpointStatus
from llm.core.interactions import same_interaction_value
from llm.core.json_data import JsonObject
from llm.core.tool_codes import ToolErrorCode
from llm.errors import stable_error_code
from .tool_errors import ToolExecutionError, ToolInvocationError


class ToolAttemptFailure:
    CODE = "code"
    MESSAGE = "message"
    EFFECT = ToolExecutionError.EFFECT
    ARGUMENTS = "arguments"
    ATTEMPT = "attempt"
    INTERRUPTED = ToolErrorCode.INTERRUPTED.value
    INTERRUPTED_MESSAGE = "Previous attempt was interrupted"

    @classmethod
    def interrupted(cls, arguments):
        return {
            cls.CODE: cls.INTERRUPTED,
            cls.MESSAGE: cls.INTERRUPTED_MESSAGE,
            cls.EFFECT: ToolExecutionError.UNCERTAIN_EFFECT,
            cls.ARGUMENTS: deepcopy(arguments),
        }

    @classmethod
    def current(cls, error, uncertain, arguments):
        return {
            cls.CODE: stable_error_code(error) or ToolExecutionError.code,
            cls.MESSAGE: str(error),
            cls.EFFECT: ToolExecutionError.UNCERTAIN_EFFECT if uncertain else ToolExecutionError.NO_EFFECT,
            cls.ARGUMENTS: deepcopy(arguments),
        }

    @classmethod
    def attempted(cls, error, attempt, arguments):
        return {
            cls.ATTEMPT: attempt,
            cls.CODE: error.code,
            cls.MESSAGE: str(error),
            cls.EFFECT: error.effect,
            cls.ARGUMENTS: deepcopy(arguments),
        }

    @classmethod
    def with_attempt(cls, previous, attempt):
        return {**previous, cls.ATTEMPT: attempt}

    @classmethod
    def uncertain(cls, previous):
        return previous[cls.EFFECT] != ToolExecutionError.NO_EFFECT


@dataclass(slots=True)
class ToolRetryState:
    INITIAL_ATTEMPT = 0
    INITIAL_REPAIRS = 0
    CONFIRMED = False
    PENDING = True
    EXECUTING = False
    NO_ERROR = None
    ATTEMPT_INCREMENT = 1
    REPAIR_KEY = "{key}:repair:{repairs}"
    INVALID = "Tool retry checkpoint does not match its original invocation"

    original: dict
    arguments: dict
    attempt: int
    repairs: int
    failures: list
    uncertain: bool
    pending: bool
    last_error: dict | None

    @classmethod
    def initial(cls, arguments):
        return cls(
            deepcopy(arguments),
            deepcopy(arguments),
            cls.INITIAL_ATTEMPT,
            cls.INITIAL_REPAIRS,
            [],
            cls.CONFIRMED,
            cls.PENDING,
            cls.NO_ERROR,
        )

    @classmethod
    def read(cls, value, arguments):
        state = deepcopy(value)
        if (
            set(state) != {item.name for item in fields(cls)}
            or not same_interaction_value(state[cls.original.__name__], arguments)
            or type(state[cls.attempt.__name__]) is not int
            or state[cls.attempt.__name__] < cls.INITIAL_ATTEMPT
            or type(state[cls.repairs.__name__]) is not int
            or not cls.INITIAL_REPAIRS <= state[cls.repairs.__name__] <= state[cls.attempt.__name__]
            or type(state[cls.uncertain.__name__]) is not bool
            or type(state[cls.pending.__name__]) is not bool
            or not isinstance(state[cls.failures.__name__], list)
            or len(state[cls.failures.__name__]) != state[cls.attempt.__name__]
        ):
            raise ToolInvocationError(cls.INVALID)
        JsonObject.validate(state)
        result = cls(**state)
        if not result.pending:
            result.attempt += cls.ATTEMPT_INCREMENT
            previous = result.last_error or ToolAttemptFailure.interrupted(result.arguments)
            result.failures.append(ToolAttemptFailure.with_attempt(previous, result.attempt))
            result.uncertain = result.uncertain or ToolAttemptFailure.uncertain(previous)
            result.pending = cls.PENDING
        return result

    def to_dict(self):
        return asdict(self)

    def invocation_key(self, request_key):
        return (
            request_key if not self.repairs else self.REPAIR_KEY.format(key=request_key, repairs=self.repairs)
        )

    def repair_key(self, request_key):
        return self.REPAIR_KEY.format(key=request_key, repairs=self.repairs)

    def begin(self):
        self.pending = self.EXECUTING
        self.last_error = self.NO_ERROR

    def failed(self, error, uncertain, arguments):
        # Build the observation before updating either field, as in dict.update.
        failure = ToolAttemptFailure.current(error, uncertain, arguments)
        self.uncertain, self.last_error = uncertain, failure

    def selected(self, error, attempt, arguments, candidate, modified, uncertain):
        self.failures.append(ToolAttemptFailure.attempted(error, attempt, arguments))
        next_arguments = deepcopy(candidate)
        repairs = self.repairs + int(modified)
        self.attempt, self.arguments, self.repairs = attempt, next_arguments, repairs
        self.uncertain, self.pending = uncertain, self.PENDING


@dataclass(frozen=True, slots=True)
class ToolRetryCheckpoint:
    PREFIX = "@tool-retry:"
    KIND = "tool_retry"
    CONTAINER = True
    ABSENT = None
    INVALID_KEY = "Tool request_key must be nonempty and outside the reserved retry namespace"
    INVALID_RECORD = "Invalid Tool retry checkpoint"

    status: str
    node_type: str
    container: bool
    retry: dict

    @classmethod
    def key(cls, request_key):
        if request_key is not cls.ABSENT and (
            not isinstance(request_key, str) or not request_key.strip() or request_key.startswith(cls.PREFIX)
        ):
            raise ToolInvocationError(cls.INVALID_KEY)
        return cls.PREFIX + request_key if request_key is not cls.ABSENT else cls.ABSENT

    @classmethod
    def event_key(cls, request_key):
        return cls.PREFIX + request_key

    @classmethod
    def read(cls, saved, arguments):
        if not saved:
            return ToolRetryState.initial(arguments)
        if (
            saved.get(cls.node_type.__name__) != cls.KIND
            or saved.get(cls.status.__name__) != CheckpointStatus.COMPLETED.value
            or not isinstance(saved.get(cls.retry.__name__), dict)
        ):
            raise ToolInvocationError(cls.INVALID_RECORD)
        return ToolRetryState.read(saved[cls.retry.__name__], arguments)

    @classmethod
    def create(cls, state):
        return asdict(cls(CheckpointStatus.COMPLETED.value, cls.KIND, cls.CONTAINER, state.to_dict()))
