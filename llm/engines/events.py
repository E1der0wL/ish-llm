"""Engine observations and their validation; persistence and state transitions belong to services."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Optional, Union

from llm.core.models import ResourceKind, StepKind, StepErrorCode
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.core.results import CompletionResult, EngineOutput, EngineDelta
from llm.core.interactions import InteractionRequest
from llm.core.checkpoint_records import CheckpointWrite
from llm.core.step_event_metadata import StepEventMetadata, StepEventPhase


class EngineEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    OUTPUT = "output"
    COMPLETION = "completion"
    STEP_STARTED = "step_started"
    STEP_UPDATED = "step_updated"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_INTERRUPTED = "step_interrupted"
    STEP_CANCELLED = "step_cancelled"
    CHECKPOINT = "checkpoint"
    PAUSED = "paused"
    INTERACTION_CHANGED = "interaction_changed"
    STEERING = "steering"
    STEERING_CHANGED = "steering_changed"


@dataclass(frozen=True, slots=True)
# Engine에서 서비스로 전달하는 관찰 이벤트. 사용자 이벤트는 문자열 이름을 사용한다.
class EngineEvent:
    NO_CHECKPOINT: ClassVar[None] = None
    NO_INTERACTION: ClassVar[None] = None
    NO_OUTPUT: ClassVar[None] = None
    NO_DIAGNOSTIC: ClassVar[None] = None
    NO_PROGRESS: ClassVar[None] = None
    NO_FAILURE_CODE: ClassVar[None] = None
    NO_STEP: ClassVar[None] = None
    INVALID_CHECKPOINT: ClassVar[str] = "A Step completion can attach one checkpoint record"
    INVALID_DIAGNOSTIC: ClassVar[str] = "diagnostic must be Diagnostic"
    INVALID_PROGRESS: ClassVar[str] = "progress must be OperationProgress"
    INVALID_FAILURE_CODE: ClassVar[str] = "failure_code requires a nonempty code on STEP_FAILED"
    DEFAULT_FAILURE: ClassVar[str] = "Step failed"
    PHASES: ClassVar[dict] = {
        EngineEventType.STEP_STARTED: StepEventPhase.STARTED.value,
        EngineEventType.STEP_UPDATED: StepEventPhase.RUNNING.value,
        EngineEventType.STEP_COMPLETED: StepEventPhase.COMPLETED.value,
        EngineEventType.STEP_FAILED: StepEventPhase.FAILED.value,
        EngineEventType.STEP_INTERRUPTED: StepEventPhase.INTERRUPTED.value,
        EngineEventType.STEP_CANCELLED: StepEventPhase.CANCELLED.value,
    }

    type: Union[EngineEventType, str]
    delta: Optional[EngineDelta] = None
    step_id: Optional[str] = NO_STEP
    kind: str = StepKind.LLM.value
    name: str = ""
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    completion: Optional[CompletionResult] = None
    output: Optional[EngineOutput] = NO_OUTPUT
    interaction: Optional[InteractionRequest] = NO_INTERACTION
    diagnostic: Optional[Diagnostic] = NO_DIAGNOSTIC
    progress: Optional[OperationProgress] = NO_PROGRESS
    # 실행 중 fail-fast 합성용. Diagnostic의 임의 관찰 code와 달리 명시적 CodedError만 전달한다.
    # StepEventRecorder는 이 런타임 필드를 저장하지 않는다.
    failure_code: Optional[str] = NO_FAILURE_CODE
    # A completed Step and its resume receipt commit in one service transaction.
    # Only record operations are attachable; initialization remains a separate boundary.
    checkpoint: Optional["EngineEvent"] = NO_CHECKPOINT

    @property
    def has_checkpoint(self):
        return self.checkpoint is not self.NO_CHECKPOINT

    @property
    def checkpoint_receipt(self):
        return self.checkpoint or self

    def __post_init__(self):
        if self.has_checkpoint and (self.type != EngineEventType.STEP_COMPLETED
                or not isinstance(self.checkpoint, EngineEvent)
                or self.checkpoint.type != EngineEventType.CHECKPOINT
                or not CheckpointWrite(self.checkpoint.metadata).is_record
                or self.checkpoint.has_checkpoint):
            raise ValueError(self.INVALID_CHECKPOINT)
        # Step 상태 표시를 공통 계약으로 제공한다. 실제 상태 전이는 서비스가 수행한다.
        if self.diagnostic is not self.NO_DIAGNOSTIC and not isinstance(self.diagnostic, Diagnostic):
            raise TypeError(self.INVALID_DIAGNOSTIC)
        if self.progress is not self.NO_PROGRESS and not isinstance(self.progress, OperationProgress):
            raise TypeError(self.INVALID_PROGRESS)
        if self.failure_code is not self.NO_FAILURE_CODE and (self.type != EngineEventType.STEP_FAILED
                or not isinstance(self.failure_code, str) or not self.failure_code.strip()):
            raise ValueError(self.INVALID_FAILURE_CODE)
        if self.step_id is not self.NO_STEP and self.type in self.PHASES:
            source = ResourceRef(ResourceKind.STEP, self.step_id, step_id=self.step_id)
            if self.progress is self.NO_PROGRESS:
                phase = StepEventMetadata.read_phase(self.metadata, self.PHASES[self.type])
                object.__setattr__(self, EngineEvent.progress.__name__, OperationProgress(source, phase))
            if self.type == EngineEventType.STEP_FAILED and self.diagnostic is self.NO_DIAGNOSTIC:
                code = StepEventMetadata.read_error_code(self.metadata, StepErrorCode.FAILED)
                object.__setattr__(self, EngineEvent.diagnostic.__name__, Diagnostic(
                    code, self.error or self.DEFAULT_FAILURE, source=source))
