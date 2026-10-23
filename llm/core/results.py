"""Run에 저장된 모델 호출 관찰값과 조회용 실행 결과를 정의한다. Project/Session에 결과 파일을 중복 생성하지 않는다.

Persistable execution observations, separate from Project configuration."""

from llm.core.contracts import DiagnosticCode

from llm.core.models import ResourceKind

from dataclasses import asdict, dataclass, field, fields, replace
from copy import deepcopy
from datetime import datetime
from enum import StrEnum
import math
from typing import Any, ClassVar, Optional

from .contracts import Diagnostic, ResourceRef
from .models import StepStatus, Run, RunStatus, Step, new_id, now
from llm.core.json_data import JsonObject
from .model_protocol import StopReason, copy_model
from .usage import TokenUsage, UsageReceipt
from .execution_observations import ExecutionObservations


class OutputVisibility(StrEnum):
    USER = "user"
    INTERNAL = "internal"


class OutputOperation(StrEnum):
    APPEND = "append"
    REPLACE = "replace"


class OutputContract:
    EMPTY_TEXT = ""
    NO_DATA = None
    INITIAL_SEQUENCE = 0
    PARTIAL = False
    FINAL = True
    INVALID_ID = "Output requires a nonempty ID"
    INVALID_STEP = "Output Step ID must be nonempty"
    INVALID_CONTENT = "Invalid output text/visibility"
    INVALID_SEQUENCE = "Output sequence must be a nonnegative integer"


class ExecutionDuration:
    MINIMUM_SECONDS = 0.0


@dataclass(frozen=True, slots=True)
class EngineDelta:
    """한 출력의 텍스트 변경. sequence는 저장 서비스가 부여하는 Run 내 순서다.

    UI는 (run_id, output_id)를 키로 사용한다. step_id=None이면 Run 출력이며,
    visibility='internal'은 대화 본문에 포함하지 않는 작업 관찰값이다.
    """
    INVALID_OPERATION: ClassVar[str] = "Output operation must be append or replace"
    INVALID_FIELDS: ClassVar[str] = "Stored delta requires all current fields, including output_id"
    output_id: str = field(default_factory=new_id)
    text: str = OutputContract.EMPTY_TEXT
    step_id: Optional[str] = None
    visibility: str = OutputVisibility.USER.value
    operation: str = OutputOperation.APPEND.value
    sequence: int = OutputContract.INITIAL_SEQUENCE

    def __post_init__(self):
        _validate_output(self)
        if self.operation not in tuple(OutputOperation):
            raise ValueError(self.INVALID_OPERATION)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "EngineDelta":
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        return cls(**deepcopy(value))


@dataclass(frozen=True, slots=True)
class EngineOutput:
    """Engine/Step의 공통 결과. 업무 데이터는 JSON 값이며 별도 도메인이 아니다.

    final은 해당 출력의 확정을 뜻한다. Run 성공 여부는 ExecutionResult.status로
    판단한다. 실패/중단된 실행의 부분 출력은 final=False로 재조회할 수 있다.
    """
    INVALID_FINAL: ClassVar[str] = "Invalid output final/metadata"
    FINALIZED: ClassVar[str] = "Cannot change a finalized output"
    IDENTITY_MISMATCH: ClassVar[str] = "Output identity mismatch"
    SEQUENCE_ORDER: ClassVar[str] = "Output sequence must increase"
    INVALID_FIELDS: ClassVar[str] = "Stored output requires all current fields, including output_id"
    output_id: str = field(default_factory=new_id)
    text: str = OutputContract.EMPTY_TEXT
    data: Any = OutputContract.NO_DATA
    step_id: Optional[str] = None
    visibility: str = OutputVisibility.USER.value
    metadata: dict = field(default_factory=dict)
    sequence: int = OutputContract.INITIAL_SEQUENCE
    final: bool = OutputContract.FINAL

    def __post_init__(self):
        _validate_output(self)
        if type(self.final) is not bool or not isinstance(self.metadata, dict):
            raise ValueError(self.INVALID_FINAL)
        # 엔진/SDK의 가변 객체를 UI와 저장소에 그대로 넘기지 않는다.
        data, metadata = type(self).data.__name__, type(self).metadata.__name__
        copied = {data: self.data, metadata: self.metadata}
        JsonObject.validate(copied)
        copied = deepcopy(copied)
        object.__setattr__(self, data, copied[data])
        object.__setattr__(self, metadata, copied[metadata])

    @property
    def has_data(self):
        return self.data is not OutputContract.NO_DATA

    def apply(self, delta: EngineDelta) -> "EngineOutput":
        """UI/저장소가 공유하는 증분 적용 규칙. 중복·역순과 소유자 변경을 거부한다."""
        if self.final:
            raise ValueError(self.FINALIZED)
        if (self.output_id, self.step_id, self.visibility) != (delta.output_id, delta.step_id, delta.visibility):
            raise ValueError(self.IDENTITY_MISMATCH)
        if (self.sequence or delta.sequence) and delta.sequence <= self.sequence:
            raise ValueError(self.SEQUENCE_ORDER)
        return replace(self, text=(self.text + delta.text if delta.operation == OutputOperation.APPEND else delta.text),
                       sequence=delta.sequence)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "EngineOutput":
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        return cls(**deepcopy(value))


def _validate_output(value) -> None:
    if not isinstance(value.output_id, str) or not value.output_id.strip():
        raise ValueError(OutputContract.INVALID_ID)
    if value.step_id is not None and (not isinstance(value.step_id, str) or not value.step_id.strip()):
        raise ValueError(OutputContract.INVALID_STEP)
    if not isinstance(value.text, str) or value.visibility not in tuple(OutputVisibility):
        raise ValueError(OutputContract.INVALID_CONTENT)
    if type(value.sequence) is not int or value.sequence < OutputContract.INITIAL_SEQUENCE:
        raise ValueError(OutputContract.INVALID_SEQUENCE)


def duration_seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if start is None or end is None:
        return None
    return max(ExecutionDuration.MINIMUM_SECONDS, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


@dataclass(frozen=True, slots=True)
class ResumeOutput:
    """Explicit-resume observation, not a completion or effect receipt.

    Services supply the stored output and its owning Step without interpreting
    Engine-private metadata. The consuming Engine selects applicable references.
    This snapshot is transient; it creates no additional persisted authority.
    """
    INVALID_OUTPUT: ClassVar[str] = "Resume output requires an unfinished source output"
    MISSING_STEP: ClassVar[str] = "Resume output requires its owning Step"
    INVALID_STEP: ClassVar[str] = "Resume output Step ownership/status mismatch"
    run_id: str
    output: EngineOutput
    step: Optional[Step] = None

    def __post_init__(self):
        if not self.run_id or not isinstance(self.output, EngineOutput) or self.output.final:
            raise ValueError(self.INVALID_OUTPUT)
        if self.step is None:
            if self.output.step_id is not None:
                raise ValueError(self.MISSING_STEP)
        elif (self.step.id != self.output.step_id or self.step.run_id != self.run_id
              or self.step.status == StepStatus.COMPLETED):
            raise ValueError(self.INVALID_STEP)
        object.__setattr__(self, type(self).output.__name__, deepcopy(self.output))
        object.__setattr__(self, type(self).step.__name__, deepcopy(self.step))


@dataclass(slots=True)
# 개별 모델 호출의 종료 이유, 시간과 사용량을 기록한다.
class CompletionResult:
    RESERVATION_FIELDS = ("id", "step_id", "started_at", "reserved_tokens")
    INVALID_TOKEN_COUNT = "Completion usage must contain nonnegative token counts"
    INVALID_FIELDS = "Completion record requires all current fields"
    MISSING_ID = "Completion record requires an ID"
    INVALID_TEXT = "Invalid Completion {field}"
    INVALID_TIME = "Completion timestamps require a timezone"
    INVALID_USAGE = "Invalid Completion usage"
    INVALID_RESERVATION = "Invalid Completion token reservation"
    INVALID_DURATION = "Invalid Completion duration"
    INVALID_REASONING = "Invalid Completion reasoning content"
    INVALID_STATE = "Completion state requires its original end evidence"
    CHANGED_RESERVATION = "Completion reservation identity changed"
    IMMUTABLE = "Completed model call evidence is immutable"
    LOST_USAGE = "Completion update cannot erase final usage"
    UNKNOWN_OBSERVATION = None
    UNCHANGED_OBSERVATION = False
    CHANGED_OBSERVATION = True

    id: str = field(default_factory=new_id)
    step_id: Optional[str] = None
    model: Optional[str] = None
    response_id: Optional[str] = None
    finish_reason: Optional[str] = None
    termination: Optional[StopReason] = None
    status: RunStatus = RunStatus.RUNNING
    started_at: str = field(default_factory=now)
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    usage: dict = field(default_factory=dict)
    usage_complete: bool = False
    reserved_tokens: Optional[int] = None
    # Only text explicitly exposed by the provider, separate from the answer.
    reasoning_content: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def observe_update(self, update):
        """Merge normalized partial observations without completing usage evidence."""
        changed = self.UNCHANGED_OBSERVATION
        for key in (CompletionResult.response_id.__name__, CompletionResult.model.__name__,
                    CompletionResult.finish_reason.__name__, CompletionResult.termination.__name__):
            value = getattr(update, key)
            if value is not self.UNKNOWN_OBSERVATION and value != getattr(self, key):
                setattr(self, key, value)
                changed = self.CHANGED_OBSERVATION
        if update.usage:
            usage = {**self.usage, **deepcopy(update.usage)}
            if usage != self.usage:
                self.usage = usage
                changed = self.CHANGED_OBSERVATION
        if update.reasoning:
            self.reasoning_content += update.reasoning
            changed = self.CHANGED_OBSERVATION
        return changed

    def observe_drained_usage(self):
        """Only a drained transport reaches this boundary, including a rejected final response."""
        self.usage_complete = bool(self.usage)

    def observe_response(self, answer):
        """Merge only present final values; unknown usage remains unknown."""
        if answer.usage is not self.UNKNOWN_OBSERVATION:
            self.usage = {**self.usage, **copy_model(answer.usage)}
        for target, value in ((CompletionResult.response_id.__name__, answer.response_id),
                              (CompletionResult.model.__name__, answer.model),
                              (CompletionResult.finish_reason.__name__, answer.raw_finish_reason),
                              (CompletionResult.termination.__name__, answer.stop)):
            if value is not self.UNKNOWN_OBSERVATION:
                setattr(self, target, value)
        self.observe_drained_usage()

    def usage_record(self) -> dict:
        """Detached quota projection; unknown usage and its reservation stay explicit."""
        return UsageReceipt.from_result(self).to_dict()

    @classmethod
    def from_record(cls, data):
        if not isinstance(data, dict) or data.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        if not isinstance(data[cls.id.__name__], str) or not data[cls.id.__name__]:
            raise ValueError(cls.MISSING_ID)
        for key in (cls.step_id.__name__, cls.model.__name__, cls.response_id.__name__, cls.finish_reason.__name__):
            if data[key] is not None and not isinstance(data[key], str):
                raise ValueError(cls.INVALID_TEXT.format(field=key))
        for key in (cls.started_at.__name__, cls.ended_at.__name__):
            if key == cls.ended_at.__name__ and data[key] is None:
                continue
            if not isinstance(data[key], str) or datetime.fromisoformat(data[key]).utcoffset() is None:
                raise ValueError(cls.INVALID_TIME)
        if type(data[cls.usage_complete.__name__]) is not bool or not isinstance(data[cls.usage.__name__], dict):
            raise ValueError(cls.INVALID_USAGE)
        TokenUsage(data[cls.usage.__name__]).validate(complete=data[cls.usage_complete.__name__], invalid_count=cls.INVALID_TOKEN_COUNT)
        reservation = data[cls.reserved_tokens.__name__]
        if reservation is not None and not TokenUsage.valid_count(reservation):
            raise ValueError(cls.INVALID_RESERVATION)
        duration = data[cls.duration_seconds.__name__]
        if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < ExecutionDuration.MINIMUM_SECONDS):
            raise ValueError(cls.INVALID_DURATION)
        if not isinstance(data[cls.reasoning_content.__name__], str):
            raise ValueError(cls.INVALID_REASONING)
        termination = None if data[cls.termination.__name__] is None else StopReason(data[cls.termination.__name__])
        status = RunStatus(data[cls.status.__name__])
        if (status not in Run.ACTIVE_STATES) != (data[cls.ended_at.__name__] is not None):
            raise ValueError(cls.INVALID_STATE)
        return cls(**{**deepcopy(data), cls.status.__name__: status, cls.termination.__name__: termination})

    @classmethod
    def for_run(cls, data: dict, run: Run) -> "CompletionResult":
        result = cls.from_record(data)
        if (run.status not in Run.ACTIVE_STATES
                and result.status in Run.ACTIVE_STATES):
            result.status = run.status if run.status != RunStatus.COMPLETED else RunStatus.INTERRUPTED
            result.ended_at = run.ended_at
            result.duration_seconds = duration_seconds(result.started_at, result.ended_at)
        return result

    @classmethod
    def validate_update(cls, previous: dict, current: dict) -> None:
        before, after = cls.from_record(previous), cls.from_record(current)
        if any(getattr(before, name) != getattr(after, name) for name in cls.RESERVATION_FIELDS):
            raise ValueError(cls.CHANGED_RESERVATION)
        if before.status not in Run.ACTIVE_STATES and before != after:
            raise ValueError(cls.IMMUTABLE)
        if before.usage_complete and (not after.usage_complete or before.usage != after.usage):
            raise ValueError(cls.LOST_USAGE)


class CompletionRecords:
    """The required Run receipt container, its identities and quota projection."""

    KEY = "completions"
    MISSING_CONTAINER = "Run requires a stored completions container"
    DUPLICATE_ID = "Duplicate Run completion record"
    LOST_RECEIPT = "Run update cannot remove stored completion receipts"

    @classmethod
    def results(cls, metadata) -> list[CompletionResult]:
        if not isinstance(metadata, dict) or not isinstance(metadata.get(cls.KEY), list):
            raise ValueError(cls.MISSING_CONTAINER)
        results = [CompletionResult.from_record(entry) for entry in metadata[cls.KEY]]
        if len({result.id for result in results}) != len(results):
            raise ValueError(cls.DUPLICATE_ID)
        return results

    @classmethod
    def read(cls, metadata) -> list[dict]:
        cls.results(metadata)
        return metadata[cls.KEY]

    @classmethod
    def initialize(cls, metadata) -> None:
        """Called only while creating a new Run, never by a Reader or updater."""
        metadata.setdefault(cls.KEY, [])
        cls.results(metadata)

    @classmethod
    def usage(cls, metadata) -> list[dict]:
        return [result.usage_record() for result in cls.results(metadata)]

    @classmethod
    def finalize(cls, run: Run) -> None:
        entries = cls.read(run.metadata)
        if run.status not in Run.ACTIVE_STATES:
            run.metadata[cls.KEY] = [asdict(CompletionResult.for_run(entry, run)) for entry in entries]

    @classmethod
    def contains(cls, metadata, identifier: str) -> bool:
        return any(result.id == identifier for result in cls.results(metadata))

    @classmethod
    def validate_update(cls, previous_metadata, current_metadata):
        previous = {entry.id: entry for entry in cls.results(previous_metadata)}
        current = {entry.id: entry for entry in cls.results(current_metadata)}
        if not previous.keys() <= current.keys():
            raise ValueError(cls.LOST_RECEIPT)
        for identifier, entry in previous.items():
            CompletionResult.validate_update(entry.to_dict(), current[identifier].to_dict())

    @classmethod
    def upsert(cls, metadata, result: CompletionResult) -> None:
        previous_results = cls.results(metadata)
        entries = metadata[cls.KEY]
        value = asdict(result)
        CompletionResult.from_record(value)
        for index, previous in enumerate(previous_results):
            if previous.id == result.id:
                CompletionResult.validate_update(previous.to_dict(), value)
                entries[index] = value
                return
        entries.append(value)


@dataclass(slots=True)
# 실행 결과 조회 뷰. 사용량을 모르면 0 대신 None을 유지한다.
class ExecutionResult:
    """A query view of one Run, never a second persisted copy."""
    EMPTY_ERROR = ""
    project_id: str
    session_id: str
    run_id: str
    engine: str
    status: RunStatus
    started_at: Optional[str]
    ended_at: Optional[str]
    duration_seconds: Optional[float]
    completions: list[CompletionResult] = field(default_factory=list)
    # None means unknown/partial, never an invented zero for missing usage.
    usage: dict = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    output: Optional[EngineOutput] = None

    diagnostic_details: dict = field(default_factory=dict)

    @classmethod
    def from_run(cls, project_id: str, run: Run) -> "ExecutionResult":
        completions = [CompletionResult.for_run(data, run)
                       for data in CompletionRecords.read(run.metadata)]
        usage = TokenUsage.aggregate(completions).to_dict()
        return cls(project_id, run.session_id, run.id, run.engine, run.status,
                   run.started_at, run.ended_at, duration_seconds(run.started_at, run.ended_at),
                   completions=completions, usage=usage, error=run.error, error_code=run.error_code,
                   output=ExecutionObservations.run_output(run.metadata),
                   diagnostic_details=ExecutionObservations.diagnostic_record(run.metadata))

    @property
    def diagnostic(self) -> Optional[Diagnostic]:
        """저장된 오류의 UI 투영. 재시도 판단이나 원본 오류 필드를 변경하지 않는다."""
        if self.error is None and self.error_code is None:
            return None
        if self.diagnostic_details:
            return Diagnostic.from_dict(self.diagnostic_details)
        return Diagnostic(self.error_code or DiagnosticCode.EXECUTION_FAILED, self.error or self.EMPTY_ERROR,
            source=ResourceRef(ResourceKind.RUN, self.run_id, project_id=self.project_id,
                               session_id=self.session_id, run_id=self.run_id))

    @property
    def total_tokens(self) -> Optional[int]:
        return TokenUsage(self.usage).total_tokens

    @property
    def finish_reasons(self) -> list[Optional[str]]:
        return [completion.finish_reason for completion in self.completions]
