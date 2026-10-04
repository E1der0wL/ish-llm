"""Project → Session → Run → Step의 영속 데이터와 상태를 정의한다. asyncio 객체나 서비스 핸들은 이 모델에 저장하지 않는다."""

from typing import Optional, TYPE_CHECKING
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from .paths import ProjectPaths, RunPaths, StepPaths, SessionPaths
from .policies import normalize_policies, policy_schema

if TYPE_CHECKING:
    from .results import EngineOutput


# Session 기반 경로·식별자를 사용하는 영속 도메인 형식. 이전 Task 형식은 자동 변환하지 않는다.
DOMAIN_STORAGE_VERSION = 1

# ---------------------------------------------------------------------------
# 공통 ID와 UTC 시각 생성
# ---------------------------------------------------------------------------
def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 영속 역할과 수명 주기 상태
# ---------------------------------------------------------------------------

class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DELETED = "deleted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    COMMITTED = "committed"
    STREAMING = "streaming"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"


def validate_run_transition(current: RunStatus, target: RunStatus, *, reason: str) -> None:
    """Run 상태 변경의 순수 검증. 저장·시각 생성·다른 도메인 변경은 하지 않는다.

    종료한 실행 시도는 다시 열지 않는다. resume는 서비스가 별도 Run을 생성한다.
    CANCELLED는 기존 저장 enum으로 유지하되 새 실행 경로를 만들지 않는다.
    대기 요청 취소는 Run 전이가 아니라 Message의 CANCELLED 전이다.
    """
    current, target = RunStatus(current), RunStatus(target)
    if reason == "start":
        allowed = current == RunStatus.PENDING and target == RunStatus.RUNNING
    elif reason == "finish":
        allowed = current == RunStatus.RUNNING and target in (
            RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.PAUSED)
    elif reason == "recovery":
        allowed = current in (RunStatus.PENDING, RunStatus.RUNNING) and target == RunStatus.INTERRUPTED
    else:
        raise ValueError(f"Unknown Run transition reason: {reason}")
    if not allowed:
        raise ValueError(f"Invalid Run transition ({reason}): {current.value} -> {target.value}")


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Project: Session/Engine에 전달할 확장 가능한 JSON 설정
# ---------------------------------------------------------------------------

# 고정 필드에 제한되지 않는 JSON 설정. 실행 시 Session 설정과 병합한다.
class ProjectConfig(dict):
    """Open JSON workspace settings with mapping access and attribute shortcuts.

    Unknown top-level keys round-trip unchanged. Reserved sections are validated
    when constructing or saving; nested mutation is allowed between saves.
    """

    def __init__(self, values: Optional[dict] = None, **settings) -> None:
        sections = {"policies": {}, "parameters": {}, "data": {}}
        if values is not None:
            sections.update(deepcopy(dict(values)))
        sections.update(deepcopy(settings))
        super().__init__(sections)
        self.validate()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value

    @staticmethod
    def validate_settings(value: dict) -> None:
        """Check JSON compatibility without restricting application field names."""
        if not isinstance(value, dict):
            raise TypeError("Settings must be a dictionary")
        def check(item):
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise TypeError("Settings keys must be strings")
                    check(nested)
            elif isinstance(item, list):
                for nested in item:
                    check(nested)
            elif item is not None and not isinstance(item, (str, bool, int, float)):
                raise TypeError("Settings must contain only JSON values")
        check(value)
        json.dumps(value, allow_nan=False)

    def validate(self) -> None:
        self.validate_settings(self)
        self["policies"] = normalize_policies(self.get("policies", {}))
        for section in ("parameters", "data"):
            if not isinstance(self.get(section), dict):
                raise TypeError(f"{section} must be a dictionary")
        self.validate_session(self, project=True)

    @staticmethod
    def policy_schema() -> dict:
        """UI용 정책 필드·허용 형식·설명. 실행 객체나 계산기 함수는 포함하지 않는다."""
        return policy_schema()

    def configure_policies(self, changes: dict) -> dict:
        """정책 일부를 병합·검증한 뒤 반영하고 독립 사본을 반환한다.

        검증 실패 시 기존 설정은 그대로 유지한다. 파일 저장과 잠금은
        ProjectManager가 담당하며 이 메서드는 메모리의 설정만 변경한다.
        """
        self.validate_settings(changes)
        candidate = deepcopy(self)
        candidate["policies"] = self.merge(candidate.get("policies", {}), changes)
        candidate.validate()
        self["policies"] = candidate.policies
        return deepcopy(self.policies)

    @classmethod
    def validate_session(cls, config: dict, *, project: bool = False) -> None:
        cls.validate_settings(config)
        if not project and "policies" in config:
            raise ValueError("Execution policies belong to ProjectConfig, not Session configuration")
        removed = {"completion", "engines", "component_configurations", "session_defaults", "default_engine"} & config.keys()
        if removed:
            raise ValueError(f"Unsupported configuration sections: {sorted(removed)}; use targeted parameters")
        for name in ("parameters", "data"):
            if name in config and not isinstance(config[name], dict):
                raise TypeError("Session configuration sections must be dictionaries")
        parameters = config.get("parameters", {})
        if not project and "components" in parameters:
            raise ValueError("Component parameters belong to ProjectConfig, not Session configuration")
        for category in ("engines", "components"):
            targets = parameters.get(category, {})
            if not isinstance(targets, dict) or any(not isinstance(options, dict) for options in targets.values()):
                raise TypeError(f"parameters.{category} must map target names to dictionaries")

    def to_dict(self) -> dict:
        self.validate()
        return deepcopy(dict(self))

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)

    @classmethod
    def deserialize(cls, value: str) -> "ProjectConfig":
        return cls.from_dict(json.loads(value))

    @staticmethod
    def merge(defaults: dict, overrides: dict) -> dict:
        """Recursively merge dictionaries; lists/scalars replace the default."""
        result = deepcopy(defaults)
        for key, value in overrides.items():
            result[key] = (ProjectConfig.merge(result[key], value)
                           if isinstance(result.get(key), dict) and isinstance(value, dict)
                           else deepcopy(value))
        return result

    def for_engine(self, name: str, session_config: Optional[dict] = None) -> dict:
        """Detached settings including arbitrary workspace and Session keys."""
        self.validate()
        session_config = session_config if session_config is not None else {}
        self.validate_session(session_config)
        result = self.merge(dict(self), session_config)
        result["engine"] = deepcopy(result.get("parameters", {}).get("engines", {}).get(name, {}))
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        cls.validate_settings(data)
        return cls(data)


@dataclass(slots=True)
# 프로젝트의 영속 데이터. 직접 실행하거나 런타임 객체를 저장하지 않는다.
class Project:
    id: str
    title: str
    paths: ProjectPaths
    config: ProjectConfig = field(default_factory=ProjectConfig)
    created_at: str = field(default_factory=now)
    deleted: bool = False
    # Component identities only. Each component owns its configuration and paths.
    components: tuple[str, ...] = ()
    # None은 사용자 주입 대화 팩토리의 저장 방식을 따른다.
    conversation_storage: Optional[str] = None
    storage_version: int = DOMAIN_STORAGE_VERSION

    def __post_init__(self) -> None:
        self.validate_conversation_storage()

    def validate_conversation_storage(self) -> None:
        """저장 방식은 설정 데이터와 구분되는 프로젝트 수명 주기 속성이다."""
        if self.conversation_storage not in (None, "file", "memory"):
            raise ValueError("Project conversation_storage must be 'file' or 'memory'")


# ---------------------------------------------------------------------------
# Session: long-lived session state (runtime asyncio objects live in services)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# 대화 세션의 영속 데이터. 큐와 실행 태스크는 SessionRuntime에 둔다.
class Session:
    id: str
    project_id: str
    title: str
    paths: SessionPaths
    status: SessionStatus = SessionStatus.IDLE
    current_run_id: Optional[str] = None
    created_at: str = field(default_factory=now)
    metadata: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    storage_version: int = DOMAIN_STORAGE_VERSION


# ---------------------------------------------------------------------------
# Message: JSONL 이벤트로 복원하는 Session 대화 상태
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# Session 대화의 메시지와 상태. Run ID로 실행과 연결된다.
class Message:
    id: str
    role: MessageRole
    content: str
    status: MessageStatus
    run_id: Optional[str] = None
    created_at: str = field(default_factory=now)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Run: 확정된 요청 하나에 대한 Engine 실행
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# 하나의 사용자 요청에 대응하는 실행 기록.
class Run:
    id: str
    session_id: str
    input_message_id: str
    assistant_message_id: str
    engine: str
    paths: RunPaths
    status: RunStatus = RunStatus.PENDING
    created_at: str = field(default_factory=now)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    error_code: Optional[str] = None
    storage_version: int = DOMAIN_STORAGE_VERSION


# ---------------------------------------------------------------------------
# Step: observable execution unit inside a Run
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# Run 내부의 관찰 가능한 작업 기록.
class Step:
    id: str
    run_id: str
    kind: str
    name: str
    paths: StepPaths
    status: StepStatus = StepStatus.PENDING
    created_at: str = field(default_factory=now)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    storage_version: int = DOMAIN_STORAGE_VERSION

    @property
    def progress(self):
        from .contracts import OperationProgress
        value = self.metadata.get("progress")
        return OperationProgress.from_dict(value) if value is not None else None

    @property
    def diagnostic(self):
        from .contracts import Diagnostic, ResourceRef
        value = self.metadata.get("diagnostic")
        if value is not None:
            return Diagnostic.from_dict(value)
        return Diagnostic("step_failed", self.error,
            source=ResourceRef("step", self.id, step_id=self.id, run_id=self.run_id)) if self.error else None

    @property
    def output(self) -> Optional["EngineOutput"]:
        """Step에 귀속된 공통 결과를 독립된 조회 스냅샷으로 반환한다."""
        from .results import EngineOutput
        value = self.metadata.get("output")
        return EngineOutput.from_dict(value) if value is not None else None
