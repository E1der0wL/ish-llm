"""도메인마다 소유하는 주요 경로를 정의한다. Component 내부 경로는 해당 Component에서 관리한다."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class BackendPaths:
    """Backend namespace; locking remains owned by the selected Project repository."""

    PROJECTS: ClassVar[str] = "projects"
    root: Path

    @property
    def projects(self) -> Path:
        return self.root / self.PROJECTS


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Permanent lock and transaction namespace of the existing repository root."""

    LOCK: ClassVar[str] = ".ish.lock"
    TRANSACTIONS: ClassVar[str] = ".transactions"
    RESERVED: ClassVar[frozenset[str]] = frozenset((LOCK, TRANSACTIONS))
    root: Path

    @property
    def lock(self) -> Path:
        return self.root / self.LOCK

    @property
    def transactions(self) -> Path:
        return self.root / self.TRANSACTIONS

    @classmethod
    def reserved(cls, relative: Path) -> bool:
        return not relative.parts or relative.parts[0] in cls.RESERVED


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    RECORD: ClassVar[str] = 'project.json'
    AGENTS: ClassVar[str] = "agents"
    SESSIONS: ClassVar[str] = "sessions"
    STATE: ClassVar[str] = "state"
    LOGS: ClassVar[str] = "logs"
    CACHE: ClassVar[str] = "cache"
    CORE_DIRECTORIES: ClassVar[frozenset[str]] = frozenset((AGENTS, SESSIONS, STATE, LOGS, CACHE))

    root: Path

    @property
    def record(self) -> Path:
        return self.root / ProjectPaths.RECORD

    @property
    def agents(self) -> Path:
        return self.root / ProjectPaths.AGENTS

    @property
    def sessions(self) -> Path:
        return self.root / ProjectPaths.SESSIONS

    @property
    def state(self) -> Path:
        return self.root / ProjectPaths.STATE

    @property
    def logs(self) -> Path:
        return self.root / ProjectPaths.LOGS

    @property
    def cache(self) -> Path:
        return self.root / ProjectPaths.CACHE


@dataclass(frozen=True, slots=True)
class SessionPaths:
    RECORD: ClassVar[str] = 'session.json'
    CONVERSATION: ClassVar[str] = 'conversation.jsonl'
    RUNS: ClassVar[str] = 'runs'
    ATTACHMENTS: ClassVar[str] = 'attachments'
    STATE: ClassVar[str] = 'state'
    LOGS: ClassVar[str] = 'logs'
    root: Path

    @property
    def record(self) -> Path:
        return self.root / SessionPaths.RECORD

    @property
    def conversation(self) -> Path:
        return self.root / SessionPaths.CONVERSATION

    @property
    def runs(self) -> Path:
        return self.root / SessionPaths.RUNS

    @property
    def attachments(self) -> Path:
        return self.root / SessionPaths.ATTACHMENTS

    @property
    def state(self) -> Path:
        return self.root / SessionPaths.STATE

    @property
    def logs(self) -> Path:
        return self.root / SessionPaths.LOGS


@dataclass(frozen=True, slots=True)
class RunPaths:
    RECORD: ClassVar[str] = 'run.json'
    OUTPUTS: ClassVar[str] = 'outputs.jsonl'
    STEPS: ClassVar[str] = 'steps'
    STATE: ClassVar[str] = 'state'
    LOGS: ClassVar[str] = 'logs'
    root: Path

    @property
    def record(self) -> Path:
        return self.root / RunPaths.RECORD

    @property
    def outputs(self) -> Path:
        return self.output_path(self.state)

    @staticmethod
    def output_path(state: Path) -> Path:
        """Resolve the journal from the existing structural state-path contract."""
        return state / RunPaths.OUTPUTS

    @property
    def steps(self) -> Path:
        return self.root / RunPaths.STEPS

    @property
    def state(self) -> Path:
        return self.root / RunPaths.STATE

    @property
    def logs(self) -> Path:
        return self.root / RunPaths.LOGS


@dataclass(frozen=True, slots=True)
class StepPaths:
    RECORD: ClassVar[str] = 'step.json'
    STATE: ClassVar[str] = 'state'
    LOGS: ClassVar[str] = 'logs'
    ARTIFACTS: ClassVar[str] = 'artifacts'
    root: Path

    @property
    def record(self) -> Path:
        return self.root / StepPaths.RECORD

    @property
    def state(self) -> Path:
        return self.root / StepPaths.STATE

    @property
    def logs(self) -> Path:
        return self.root / StepPaths.LOGS

    @property
    def artifacts(self) -> Path:
        return self.root / StepPaths.ARTIFACTS
