"""Linux diagnostic Tool requests and observation-only response projections."""

from dataclasses import asdict, dataclass, field

from llm.core.configuration import UNSET
from llm.services.infrastructure.process_state import ProcessStat
from .arguments import BuiltinArguments


@dataclass(frozen=True, slots=True)
class SystemInspectArguments(BuiltinArguments):
    NAME = "system_inspect"
    DESCRIPTION = "Read Linux kernel, Python, memory, load and work-root disk information."


@dataclass(frozen=True, slots=True)
class ProcessListArguments(BuiltinArguments):
    NAME = "process_list"
    DESCRIPTION = "List visible Linux processes and parent/terminal identity. Optional query matches process names. Results are live observations, not an atomic snapshot."

    query: str = field(default="", metadata={BuiltinArguments.SCHEMA: {"type": "string"}})
    after_pid: int = field(default=0, metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})
    limit: int | None = field(default=None, metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})


@dataclass(frozen=True, slots=True)
class ProcessInspectArguments(BuiltinArguments):
    NAME = "process_inspect"
    DESCRIPTION = "Inspect a Linux PID's cwd, command, standard I/O and terminal/process groups. Optional file descriptors expose pipes/sockets. Unavailable fields are explicit; this cannot observe application UI focus or reconstruct past events."

    pid: int = field(metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 1}})
    expected_start_ticks: object = field(default=UNSET, metadata={BuiltinArguments.SCHEMA: {"type": "integer", "minimum": 0}})
    include_files: bool = field(default=False, metadata={BuiltinArguments.SCHEMA: {"type": "boolean"}})


@dataclass(frozen=True, slots=True)
class DiskObservation:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True, slots=True)
class SystemObservation:
    observed_at: str
    kernel: str
    python: str
    cpu_count: int | None
    load_average: list[float]
    memory_bytes: dict[str, int]
    disk: DiskObservation

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnavailableProcess:
    pid: int
    reason: str


@dataclass(frozen=True, slots=True)
class ProcessPage:
    processes: list[ProcessStat]
    unavailable: list[UnavailableProcess]
    next_pid: int | None = None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FileDescriptorTarget:
    fd: int
    target: str


@dataclass(frozen=True, slots=True)
class UnavailableFileDescriptor:
    fd: int
    unavailable: str


@dataclass(slots=True)
class ProcessDetails:
    """Failed or unrequested observations stay absent, not synthetic nulls."""

    UNOBSERVED = None

    cwd: str | None = UNOBSERVED
    executable: str | None = UNOBSERVED
    argv: list[str] | None = UNOBSERVED
    stdin: str | None = UNOBSERVED
    stdout: str | None = UNOBSERVED
    stderr: str | None = UNOBSERVED
    files: list[FileDescriptorTarget | UnavailableFileDescriptor] | None = UNOBSERVED

    def to_dict(self):
        return {name: value for name, value in asdict(self).items() if value is not self.UNOBSERVED}


@dataclass(slots=True)
class InspectionEvidence:
    observed_at: str
    unavailable: dict[str, str]


@dataclass(slots=True)
class ProcessInspection:
    stat: ProcessStat
    details: ProcessDetails = field(default_factory=ProcessDetails)
    unavailable: dict[str, str] = field(default_factory=dict)

    def observe(self, declaration, function):
        try:
            setattr(self.details, declaration.__name__, function())
        except OSError as error:
            self.unavailable[declaration.__name__] = type(error).__name__

    def to_dict(self, *, observed_at):
        # The kernel fields and optional details are distinct declarations;
        # the existing public response is their flat observation projection.
        return {**self.stat.to_dict(), **self.details.to_dict(),
                **asdict(InspectionEvidence(observed_at, self.unavailable))}
