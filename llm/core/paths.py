"""도메인마다 소유하는 주요 경로를 정의한다. Component 내부 경로는 해당 Component에서 관리한다."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    AGENTS: ClassVar[str] = "agents"
    SESSIONS: ClassVar[str] = "sessions"
    STATE: ClassVar[str] = "state"
    LOGS: ClassVar[str] = "logs"
    CACHE: ClassVar[str] = "cache"
    CORE_DIRECTORIES: ClassVar[frozenset[str]] = frozenset((AGENTS, SESSIONS, STATE, LOGS, CACHE))

    root: Path

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
    root: Path

    @property
    def conversation(self) -> Path:
        return self.root / "conversation.jsonl"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def attachments(self) -> Path:
        return self.root / "attachments"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path

    @property
    def steps(self) -> Path:
        return self.root / "steps"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


@dataclass(frozen=True, slots=True)
class StepPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"
