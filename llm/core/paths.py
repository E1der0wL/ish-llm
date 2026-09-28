"""도메인마다 소유하는 주요 경로를 정의한다. Component 내부 경로는 해당 Component에서 관리한다."""

from llm.compat import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def sessions(self) -> Path:
        return self.root / "sessions"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"


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
