"""실행 전 검토용 계획. 각 서비스가 원본/정책을 재검증한 뒤 실제 작업을 수행한다."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Optional
from .contracts import Diagnostic, JsonValue, ResourceRef
from .interactions import InteractionView


class PlanBlockerCode(StrEnum):
    """Shared lifecycle-plan reasons; a code is evidence, never authorization."""

    ACTIVE_RUN = "active_run"
    ACTIVE_WORKER = "active_worker"
    NOT_RESUMABLE = "not_resumable"
    RESUME_REQUIRED = "resume_required"
    INTERACTION_REQUIRED = "interaction_required"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    RESTART_UNPROVEN = "restart_unproven"
    RESTART_RESERVED = "restart_reserved"
    INPUT_UNAVAILABLE = "input_unavailable"


@dataclass(frozen=True, slots=True)
class CheckpointPlan(JsonValue):
    """Run 전체 체크포인트 또는 RAG Job의 정리 계획. 원문/실행 payload는 노출하지 않는다."""
    MINIMUM_BYTES: ClassVar[int] = 0
    INVALID: ClassVar[str] = "Invalid checkpoint plan"
    source: ResourceRef
    checkpoints: list[str]
    bytes: int
    abandoned: bool
    abandon_blockers: list[Diagnostic]
    delete_blockers: list[Diagnostic]
    revision: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.bytes < self.MINIMUM_BYTES or not self.revision or len(set(self.checkpoints)) != len(self.checkpoints):
            raise ValueError(self.INVALID)

    @property
    def can_abandon(self) -> bool:
        return not self.abandon_blockers

    @property
    def can_delete(self) -> bool:
        return not self.delete_blockers


@dataclass(frozen=True, slots=True)
class TurnDeletionPlan(JsonValue):
    """대화 삭제 영향의 조회 스냅샷. 실행 시 revision과 재개 참조를 다시 검증한다."""
    source: ResourceRef
    message_ids: list[str]
    blockers: list[Diagnostic]
    revision: str


class ResumeState(StrEnum):
    """재개 계획이 관찰한 복원 상태. 실행 권한·예산·효과 상태와 구분한다."""

    UNKNOWN = "unknown"
    DELETED = "deleted"
    UNSUPPORTED = "unsupported"
    CORRUPT = "corrupt"
    VALID = "valid"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    NOT_CREATED = "not_created"


@dataclass(frozen=True, slots=True)
class ResumePlan(JsonValue):
    source: ResourceRef
    engine: str
    can_resume: bool
    interactions: list[InteractionView]
    reused: list[str]
    retry_nodes: list[str]
    blockers: list[Diagnostic]
    state: ResumeState = ResumeState.UNKNOWN
    revision: Optional[str] = None
    can_restart: bool = False
    restart_blockers: list[Diagnostic] = field(default_factory=list)
    active: bool = False
    abandoned: bool = False


@dataclass(frozen=True, slots=True)
class JobResumePlan(JsonValue):
    """Component-owned resumption; no invented Run/Engine or approval fields."""
    source: ResourceRef
    state: str
    can_resume: bool
    checkpoints: list[str]
    blockers: list[Diagnostic]
    revision: str
    active: bool = False
    abandoned: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryPlan(JsonValue):
    source: ResourceRef
    issues: list[Diagnostic]
    repair_sessions: list[str]
    version: str


class RetentionUnit(StrEnum):
    SESSION = "session"
    RUN = "run"


@dataclass(frozen=True, slots=True)
class RetentionPlan(JsonValue):
    source: ResourceRef
    unit: RetentionUnit
    candidates: list[dict]
    protected: list[dict]
    bytes: int
    tokens: int
    version: str
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryResult(JsonValue):
    """복구 적용 결과. 미해결 진단은 새로 검사한 remaining에 포함된다."""
    applied: list[str]
    journal: str
    remaining: RecoveryPlan
