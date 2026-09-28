"""실행 전 검토용 계획. 각 서비스가 원본/정책을 재검증한 뒤 실제 작업을 수행한다."""

from llm.compat import dataclass
from .contracts import Diagnostic, JsonValue, ResourceRef
from .interactions import InteractionView


@dataclass(frozen=True, slots=True)
class ResumePlan(JsonValue):
    source: ResourceRef
    engine: str
    can_resume: bool
    interactions: list[InteractionView]
    reused: list[str]
    retry_nodes: list[str]
    blockers: list[Diagnostic]


@dataclass(frozen=True, slots=True)
class RecoveryPlan(JsonValue):
    source: ResourceRef
    issues: list[Diagnostic]
    repair_tasks: list[str]
    version: str


@dataclass(frozen=True, slots=True)
class RetentionPlan(JsonValue):
    source: ResourceRef
    policy: dict
    candidates: list[dict]
    protected: list[dict]
    bytes_before: int
    bytes_after: int
    tokens_before: int
    tokens_after: int
    version: str
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryResult(JsonValue):
    """복구 적용 결과. 미해결 진단은 새로 검사한 remaining에 포함된다."""
    applied: list[str]
    journal: str
    remaining: RecoveryPlan
