"""Session/Run의 UI 조회 스냅샷. 원본 도메인 기록을 추가로 저장하지 않는다."""

from typing import ClassVar, Optional, Union
from dataclasses import dataclass
from .contracts import JsonValue
from .models import Run, SessionStatus
from .results import EngineDelta, EngineOutput


@dataclass(frozen=True, slots=True)
class ActiveRunView(JsonValue):
    run_id: str
    agent_id: str
    engine: str
    started_at: str


@dataclass(frozen=True, slots=True)
class SessionRuntimeView(JsonValue):
    MINIMUM_UNFINISHED: ClassVar[int] = 0
    INVALID_COUNTS: ClassVar[str] = "Invalid runtime queue or unfinished work counts"

    session_id: str
    status: SessionStatus
    unfinished_work: int
    active_runs: list[ActiveRunView]
    queued_count: int
    queued_request_ids: list[str]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.unfinished_work < self.MINIMUM_UNFINISHED or self.queued_count < len(self.queued_request_ids):
            raise ValueError(self.INVALID_COUNTS)


@dataclass(frozen=True, slots=True)
class RunView(JsonValue):
    MINIMUM_CURSOR: ClassVar[int] = 0
    INVALID_CURSOR: ClassVar[str] = "Run output cursor must be nonnegative"

    run: Run
    outputs: list[EngineOutput]
    cursor: int
    events: list[Union[EngineDelta, EngineOutput]]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.cursor < self.MINIMUM_CURSOR:
            raise ValueError(self.INVALID_CURSOR)
