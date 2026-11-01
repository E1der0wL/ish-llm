"""Process Tool input fields, effective limits, and response projections."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
import math

from llm.core.configuration import UNSET
from llm.core.schema import SchemaKeyword, object_schema
from jsonschema import Draft202012Validator
from llm.services.infrastructure.process_environment import ProcessEnvironment
from .arguments import BuiltinArguments


class ProcessStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class ProcessText:
    ENCODING = "utf-8"
    ERRORS = "replace"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessLimits(BuiltinArguments):
    NO_LIMIT = None
    MINIMUM_TIMEOUT = 0
    MINIMUM_OUTPUT = 1
    INVALID_TIMEOUT = "Process timeout must be positive and finite"
    INVALID_OUTPUT = "max_output_bytes must be a positive integer"

    timeout_seconds: object = field(
        default=UNSET,
        metadata={
            BuiltinArguments.SCHEMA: {
                "type": "number",
                "exclusiveMinimum": MINIMUM_TIMEOUT,
            }
        },
    )
    max_output_bytes: object = field(
        default=UNSET,
        metadata={
            BuiltinArguments.SCHEMA: {"type": "integer", "minimum": MINIMUM_OUTPUT}
        },
    )

    @property
    def timeout(self):
        return self.NO_LIMIT if self.timeout_seconds is UNSET else self.timeout_seconds

    @property
    def output_limit(self):
        return (
            self.NO_LIMIT if self.max_output_bytes is UNSET else self.max_output_bytes
        )

    def validate_limits(self):
        seconds, maximum = self.timeout, self.output_limit
        if seconds is not None and (
            type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or seconds <= self.MINIMUM_TIMEOUT
        ):
            raise ValueError(self.INVALID_TIMEOUT)
        if maximum is not None and (
            type(maximum) is not int or maximum < self.MINIMUM_OUTPUT
        ):
            raise ValueError(self.INVALID_OUTPUT)

    def supplied(self):
        return {
            item.name: deepcopy(getattr(self, item.name))
            for item in fields(self)
            if getattr(self, item.name) is not UNSET
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessInvocation(ProcessLimits):
    """Internal argv request; only explicitly provided overrides are serialized."""

    argv: list[str] = field(
        metadata={
            BuiltinArguments.SCHEMA: {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            }
        }
    )
    cwd: object = field(
        default=UNSET,
        metadata={BuiltinArguments.SCHEMA: {"type": "string", "minLength": 1}},
    )
    env: object = field(
        default=UNSET, metadata={BuiltinArguments.SCHEMA: ProcessEnvironment.schema()}
    )

    @classmethod
    def accepts_environment(cls, parameters):
        return cls.env.__name__ in parameters.get(SchemaKeyword.PROPERTIES, {})

    @classmethod
    def with_environment(cls, arguments, environment):
        """Host-selected environment; an explicit invocation value still wins."""
        return {cls.env.__name__: dict(environment), **arguments}


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessStartArguments(ProcessInvocation):
    NAME = "process_start"
    DESCRIPTION = "Start an argv command; returns an ID for polling/cancellation."
    WORKING_ROOT = "."
    INVALID_STDIN = "stdin must be boolean"

    stdin: bool = field(
        default=False,
        metadata={
            BuiltinArguments.SCHEMA: {
                "type": "boolean",
                "description": "Explicitly open an input pipe for process_write. This is not a PTY.",
            }
        },
    )

    @property
    def directory(self):
        return self.WORKING_ROOT if self.cwd is UNSET else self.cwd

    def validate_input(self):
        if type(self.stdin) is not bool:
            raise ValueError(self.INVALID_STDIN)

    def environment(self):
        return (
            {}
            if self.env is UNSET
            else {ProcessInvocation.env.__name__: ProcessEnvironment.copy(self.env)}
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessReferenceArguments(BuiltinArguments):
    STATUS_NAME = "process_status"
    CANCEL_NAME = "process_cancel"
    DESCRIPTION = "Inspect or cancel a process started by this Tool collection."

    process_id: str = field(metadata={BuiltinArguments.SCHEMA: {"type": "string"}})

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessOutputArguments(ProcessReferenceArguments):
    NAME = "process_output"
    DESCRIPTION = "Read retained process output incrementally. Offsets count decoded Unicode characters independently in stdout/stderr. Use next offsets to avoid duplicate text. truncated means configured capture discarded output; stream ordering is not a global event timeline."
    MIN_OFFSET = 0
    MIN_COUNT = 1

    stdout_offset: int = field(
        default=MIN_OFFSET,
        metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": MIN_OFFSET}},
    )
    stderr_offset: int = field(
        default=MIN_OFFSET,
        metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": MIN_OFFSET}},
    )
    max_chars: object = field(
        default=UNSET,
        metadata={
            BuiltinArguments.SCHEMA: {
                "type": "integer",
                "minimum": MIN_COUNT,
                "description": "Maximum characters per stream in this page.",
            }
        },
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessWriteArguments(ProcessReferenceArguments):
    NAME = "process_write"
    DESCRIPTION = "Send exact text to a process started here with stdin=true, or close its input. No automatic newline. Do not replay uncertain writes."
    INVALID_WRITE = "Provide text or explicitly close stdin"

    text: str = field(
        default="", metadata={BuiltinArguments.SCHEMA: {"type": "string"}}
    )
    close: bool = field(
        default=False, metadata={BuiltinArguments.SCHEMA: {"type": "boolean"}}
    )

    def validate_input(self):
        if (
            not isinstance(self.text, str)
            or type(self.close) is not bool
            or not self.text
            and not self.close
        ):
            raise ValueError(self.INVALID_WRITE)


@dataclass(frozen=True, slots=True, kw_only=True)
class ShellArguments(ProcessLimits):
    NAME = "shell_execute"
    DESCRIPTION = "Execute a host shell command and wait for its result."

    command: str = field(
        metadata={BuiltinArguments.SCHEMA: {"type": "string", "minLength": 1}}
    )
    cwd: object = field(
        default=UNSET,
        metadata={BuiltinArguments.SCHEMA: {"type": "string", "minLength": 1}},
    )
    env: object = field(
        default=UNSET, metadata={BuiltinArguments.SCHEMA: ProcessEnvironment.schema()}
    )

    @classmethod
    def forward(cls, arguments, prefix):
        """Project an admitted shell call without dropping explicit opaque overrides."""
        return {
            ProcessInvocation.argv.__name__: [*prefix, arguments[cls.command.__name__]],
            **{
                key: value
                for key, value in arguments.items()
                if key != cls.command.__name__
            },
        }

    def invocation(self, prefix):
        return ProcessInvocation(
            argv=[*prefix, self.command],
            cwd=self.cwd,
            env=self.env,
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckArguments(ProcessLimits):
    NAMES = ("check_run", "test_run")
    DESCRIPTION = "Run a developer-configured validation command."

    name: str = field(metadata={BuiltinArguments.SCHEMA: {}})

    @classmethod
    def choices_schema(cls, commands):
        value = cls.schema()
        value[SchemaKeyword.PROPERTIES][cls.name.__name__] = {
            SchemaKeyword.ENUM: list(commands)
        }
        return value

    def invocation(self, commands):
        return ProcessInvocation(
            argv=commands[self.name],
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=self.max_output_bytes,
        )


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    process_id: str = field(metadata={"type": "string"})
    status: str = field(metadata={"type": "string"})
    returncode: int | None = field(metadata={"type": ["integer", "null"]})
    pid: int | None = field(metadata={"type": ["integer", "null"]})
    argv: list[str] = field(metadata={"type": "array", "items": {"type": "string"}})
    cwd: str = field(metadata={"type": "string"})
    started_at: str = field(metadata={"type": "string"})
    ended_at: str | None = field(metadata={"type": ["string", "null"]})
    stdin_open: bool = field(metadata={"type": "boolean"})
    stdout: str = field(metadata={"type": "string"})
    stderr: str = field(metadata={"type": "string"})
    truncated: bool = field(metadata={"type": "boolean"})

    @classmethod
    def schema(cls):
        properties = {item.name: deepcopy(dict(item.metadata)) for item in fields(cls)}
        return object_schema(properties, required=list(properties))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def identifier(cls, value):
        return value[cls.process_id.__name__]


@dataclass(frozen=True, slots=True)
class OutputDestination:
    component: str = field(metadata={"type": "string", "minLength": 1})
    identifier: str = field(metadata={"type": "string", "minLength": 1})

    @classmethod
    def schema(cls):
        properties = {item.name: deepcopy(dict(item.metadata)) for item in fields(cls)}
        return object_schema(properties, required=list(properties))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutputReference(OutputDestination):
    path: str = field(metadata={"type": "string", "minLength": 1})
    revision: str = field(metadata={"type": "string", "pattern": "^[a-f0-9]{64}$"})


@dataclass(frozen=True, slots=True)
class StoredOutput(ProcessSnapshot):
    """Retained output wire contract; read() validates publication, not live drafts."""

    output_sources: dict = field(metadata=object_schema(
        {name: OutputReference.schema() for name in ("stdout", "stderr")}, required=["stdout", "stderr"]))
    stdout_chars: int = field(metadata={"type": "integer", "minimum": 0})
    stderr_chars: int = field(metadata={"type": "integer", "minimum": 0})
    preview_chars: int = field(metadata={"type": "integer", "minimum": 0})

    @classmethod
    def from_snapshot(cls, snapshot, *, sources, stdout_chars, stderr_chars, preview_chars):
        return cls(**snapshot.to_dict(), output_sources=deepcopy(sources),
                   stdout_chars=stdout_chars, stderr_chars=stderr_chars, preview_chars=preview_chars)

    @classmethod
    def read(cls, value, *, identifier):
        Draft202012Validator(cls.schema()).validate(value)
        result = cls(**deepcopy(value))
        if result.process_id != identifier:
            raise ValueError("Output record identity mismatch")
        return result

    def with_sources(self, sources):
        from dataclasses import replace
        return replace(self, output_sources=deepcopy(sources))

    def reference(self, name):
        return OutputReference(**self.output_sources[name])

    def total(self, name):
        return {"stdout": self.stdout_chars, "stderr": self.stderr_chars}[name]


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    process_id: str
    status: str
    returncode: int | None
    truncated: bool
    stdout: str
    next_stdout_offset: int
    stdout_remaining: int
    stderr: str
    next_stderr_offset: int
    stderr_remaining: int

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProcessWritten:
    process_id: str
    bytes_written: int
    stdin_closed: bool

    def to_dict(self):
        return asdict(self)
