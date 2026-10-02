"""Session/Run의 UI 조회 스냅샷. 원본 도메인 기록을 추가로 저장하지 않는다."""

from typing import Optional, Union
from dataclasses import dataclass
from .contracts import JsonValue
from .models import Run, SessionStatus
from .results import EngineDelta, EngineOutput


@dataclass(frozen=True, slots=True)
class SessionRuntimeView(JsonValue):
    session_id: str
    status: SessionStatus
    unfinished_work: int
    active_run_id: Optional[str]
    engine: Optional[str]
    started_at: Optional[str]
    queued_count: int
    queued_request_ids: list[str]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.unfinished_work < 0 or self.queued_count < len(self.queued_request_ids):
            raise ValueError("Invalid runtime queue or unfinished work counts")


@dataclass(frozen=True, slots=True)
class RunView(JsonValue):
    run: Run
    outputs: list[EngineOutput]
    cursor: int
    events: list[Union[EngineDelta, EngineOutput]]

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.cursor < 0:
            raise ValueError("Run output cursor must be nonnegative")
