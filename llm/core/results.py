"""Run에 저장된 모델 호출 관찰값과 조회용 실행 결과를 정의한다. Project/Session에 결과 파일을 중복 생성하지 않는다.

Persistable execution observations, separate from Project configuration."""

from dataclasses import asdict, dataclass, field, replace
from copy import deepcopy
from datetime import datetime
from typing import Any, Optional

from .contracts import Diagnostic, ResourceRef
from .models import ProjectConfig, Run, RunStatus, new_id, now


@dataclass(frozen=True, slots=True)
class EngineDelta:
    """한 출력의 텍스트 변경. sequence는 저장 서비스가 부여하는 Run 내 순서다.

    UI는 (run_id, output_id)를 키로 사용한다. step_id=None이면 Run 출력이며,
    visibility='internal'은 대화 본문에 포함하지 않는 작업 관찰값이다.
    """
    output_id: str = field(default_factory=new_id)
    text: str = ""
    step_id: Optional[str] = None
    visibility: str = "user"
    operation: str = "append"
    sequence: int = 0

    def __post_init__(self):
        _validate_output(self)
        if self.operation not in ("append", "replace"):
            raise ValueError("Output operation must be append or replace")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "EngineDelta":
        if not isinstance(value, dict) or "output_id" not in value:
            raise ValueError("Stored delta requires output_id")
        return cls(**deepcopy(value))


@dataclass(frozen=True, slots=True)
class EngineOutput:
    """Engine/Step의 공통 결과. 업무 데이터는 JSON 값이며 별도 도메인이 아니다.

    final은 해당 출력의 확정을 뜻한다. Run 성공 여부는 ExecutionResult.status로
    판단한다. 실패/중단된 실행의 부분 출력은 final=False로 재조회할 수 있다.
    """
    output_id: str = field(default_factory=new_id)
    text: str = ""
    data: Any = None
    step_id: Optional[str] = None
    visibility: str = "user"
    metadata: dict = field(default_factory=dict)
    sequence: int = 0
    final: bool = True

    def __post_init__(self):
        _validate_output(self)
        if type(self.final) is not bool or not isinstance(self.metadata, dict):
            raise ValueError("Invalid output final/metadata")
        # 엔진/SDK의 가변 객체를 UI와 저장소에 그대로 넘기지 않는다.
        copied = {"data": self.data, "metadata": self.metadata}
        ProjectConfig.validate_settings(copied)
        copied = deepcopy(copied)
        object.__setattr__(self, "data", copied["data"])
        object.__setattr__(self, "metadata", copied["metadata"])

    def apply(self, delta: EngineDelta) -> "EngineOutput":
        """UI/저장소가 공유하는 증분 적용 규칙. 중복·역순과 소유자 변경을 거부한다."""
        if self.final:
            raise ValueError("Cannot change a finalized output")
        if (self.output_id, self.step_id, self.visibility) != (delta.output_id, delta.step_id, delta.visibility):
            raise ValueError("Output identity mismatch")
        if (self.sequence or delta.sequence) and delta.sequence <= self.sequence:
            raise ValueError("Output sequence must increase")
        return replace(self, text=(self.text + delta.text if delta.operation == "append" else delta.text),
                       sequence=delta.sequence)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "EngineOutput":
        if not isinstance(value, dict) or "output_id" not in value:
            raise ValueError("Stored output requires output_id")
        return cls(**deepcopy(value))


def _validate_output(value) -> None:
    if not isinstance(value.output_id, str) or not value.output_id.strip():
        raise ValueError("Output requires a nonempty ID")
    if value.step_id is not None and (not isinstance(value.step_id, str) or not value.step_id.strip()):
        raise ValueError("Output Step ID must be nonempty")
    if not isinstance(value.text, str) or value.visibility not in ("user", "internal"):
        raise ValueError("Invalid output text/visibility")
    if type(value.sequence) is not int or value.sequence < 0:
        raise ValueError("Output sequence must be a nonnegative integer")


def duration_seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if start is None or end is None:
        return None
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


@dataclass(slots=True)
# 개별 모델 호출의 종료 이유, 시간과 사용량을 기록한다.
class CompletionResult:
    id: str = field(default_factory=new_id)
    step_id: Optional[str] = None
    model: Optional[str] = None
    response_id: Optional[str] = None
    finish_reason: Optional[str] = None
    status: RunStatus = RunStatus.RUNNING
    started_at: str = field(default_factory=now)
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    usage: dict = field(default_factory=dict)
    usage_complete: bool = False
    reserved_tokens: Optional[int] = None
    # Only text explicitly exposed by the provider, separate from the answer.
    reasoning_content: str = ""

    @classmethod
    def for_run(cls, data: dict, run: Run) -> "CompletionResult":
        result = cls(**{**deepcopy(data), "status": RunStatus(data["status"])})
        if (run.status not in (RunStatus.PENDING, RunStatus.RUNNING)
                and result.status in (RunStatus.PENDING, RunStatus.RUNNING)):
            result.status = run.status if run.status != RunStatus.COMPLETED else RunStatus.INTERRUPTED
            result.ended_at = run.ended_at
            result.duration_seconds = duration_seconds(result.started_at, result.ended_at)
        return result


@dataclass(slots=True)
# 실행 결과 조회 뷰. 사용량을 모르면 0 대신 None을 유지한다.
class ExecutionResult:
    """A query view of one Run, never a second persisted copy."""
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

    @classmethod
    def from_run(cls, project_id: str, run: Run) -> "ExecutionResult":
        completions = [CompletionResult.for_run(data, run)
                       for data in run.metadata.get("completions", [])]
        usage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [item.usage.get(key) for item in completions]
            usage[key] = (sum(values) if values and all(type(value) is int for value in values)
                          and all(item.usage_complete for item in completions) else None)
        return cls(project_id, run.session_id, run.id, run.engine, run.status,
                   run.started_at, run.ended_at, duration_seconds(run.started_at, run.ended_at),
                   completions=completions, usage=usage, error=run.error, error_code=run.error_code,
                   output=EngineOutput.from_dict(run.metadata["output"]) if "output" in run.metadata else None)

    @property
    def diagnostic(self) -> Optional[Diagnostic]:
        """저장된 오류의 UI 투영. 재시도 판단이나 원본 오류 필드를 변경하지 않는다."""
        if self.error is None and self.error_code is None:
            return None
        return Diagnostic(self.error_code or "execution_failed", self.error or "",
            source=ResourceRef("run", self.run_id, project_id=self.project_id,
                               session_id=self.session_id, run_id=self.run_id))

    @property
    def total_tokens(self) -> Optional[int]:
        return self.usage.get("total_tokens")

    @property
    def finish_reasons(self) -> list[Optional[str]]:
        return [completion.finish_reason for completion in self.completions]
