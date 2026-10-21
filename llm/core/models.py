"""Project → Session → Run → Step의 영속 데이터와 상태를 정의한다. asyncio 객체나 서비스 핸들은 이 모델에 저장하지 않는다."""

from typing import Any, Optional, TYPE_CHECKING
from collections.abc import Iterator
import json
from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from .paths import ProjectPaths, RunPaths, StepPaths, SessionPaths
from .policies import normalize_policies, describe_policies

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


def _stored_fields(model, data):
    """The domain's serialized fields are mandatory even when creation has defaults."""
    declarations = [item for item in fields(model) if item.name != "paths"]
    if not isinstance(data, dict):
        raise ValueError(f"{model.__name__} record requires all current fields")
    expected = {item.name for item in declarations}
    if data.keys() != expected:
        raise ValueError(f"{model.__name__} record requires all current fields; "
                         f"missing={sorted(expected - data.keys())}, "
                         f"unexpected={sorted(data.keys() - expected)}")
    for item in declarations:
        value = data[item.name]
        if item.type in (str, dict, bool) and not isinstance(value, item.type):
            raise ValueError(f"Invalid {model.__name__} {item.name}")
        if item.type == Optional[str] and value is not None and not isinstance(value, str):
            raise ValueError(f"Invalid {model.__name__} {item.name}")
    for name in ("created_at", "started_at", "ended_at"):
        if name in data and data[name] is not None:
            if datetime.fromisoformat(data[name]).tzinfo is None:
                raise ValueError(f"{model.__name__} {name} requires a timezone")


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


def validate_run_transition(current: RunStatus, target: RunStatus, *, reason: str | None = None) -> None:
    """Run 상태 변경의 순수 검증. 저장·시각 생성·다른 도메인 변경은 하지 않는다.

    종료한 실행 시도는 다시 열지 않는다. resume는 서비스가 별도 Run을 생성한다.
    CANCELLED는 기존 저장 enum으로 유지하되 새 실행 경로를 만들지 않는다.
    대기 요청 취소는 Run 전이가 아니라 Message의 CANCELLED 전이다.
    """
    current, target = RunStatus(current), RunStatus(target)
    transitions = {
        "start": {(RunStatus.PENDING, RunStatus.RUNNING)},
        "finish": {(RunStatus.RUNNING, status) for status in (
            RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.PAUSED)},
        "recovery": {(status, RunStatus.INTERRUPTED) for status in (RunStatus.PENDING, RunStatus.RUNNING)},
    }
    if reason is not None and reason not in transitions:
        raise ValueError(f"Unknown Run transition reason: {reason}")
    edges = transitions.values() if reason is None else (transitions[reason],)
    allowed = any((current, target) in values for values in edges)
    if not allowed:
        raise ValueError(f"Invalid Run transition ({reason}): {current.value} -> {target.value}")


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"


def validate_step_transition(current: StepStatus, target: StepStatus) -> None:
    """The Step owner permits pre-start failure, interruption and cancellation."""
    current, target = StepStatus(current), StepStatus(target)
    allowed = (
        current == StepStatus.PENDING and target == StepStatus.RUNNING
        or current == StepStatus.RUNNING and target == StepStatus.COMPLETED
        or current in (StepStatus.PENDING, StepStatus.RUNNING)
        and target in (StepStatus.FAILED, StepStatus.INTERRUPTED, StepStatus.CANCELLED)
    )
    if not allowed:
        raise ValueError(f"Invalid Step transition: {current.value} -> {target.value}")


# ---------------------------------------------------------------------------
# Project: Session/Engine에 전달할 확장 가능한 JSON 설정
# ---------------------------------------------------------------------------

# 정책/대상 인자는 닫힌 공용 외형이며 Application 확장은 data가 소유한다.
class ProjectConfig(dict):
    """Workspace configuration with mapping access and attribute shortcuts.

    Application fields belong to data. Implementation fields are validated
    when constructing or saving; nested mutation is allowed between saves.
    Pair iterators support Python's dict reconstruction protocol (including
    dataclasses.asdict). JSON/API inputs must use from_dict, which requires
    an object before construction and never consumes an iterator.
    """

    def __init__(self, values: dict | Iterator[tuple[str, Any]] | None = None, **config) -> None:
        sections = {"policies": {}, "parameters": {}, "data": {}}
        if values is not None:
            if isinstance(values, Iterator):
                values = dict(values)
            self.validate_json(values)
            sections.update(deepcopy(dict(values)))
        sections.update(deepcopy(config))
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
    def validate_json(value: dict) -> None:
        """Check JSON compatibility without restricting application field names."""
        if not isinstance(value, dict):
            raise TypeError("JSON data must be a dictionary")
        def check(item):
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise TypeError("JSON keys must be strings")
                    check(nested)
            elif isinstance(item, list):
                for nested in item:
                    check(nested)
            elif item is not None and not isinstance(item, (str, bool, int, float)):
                raise TypeError("JSON data must contain only JSON values")
        check(value)
        json.dumps(value, allow_nan=False)

    def validate(self) -> None:
        self.validate_json(self)
        self["policies"] = normalize_policies(self.get("policies", {}))
        for section in ("parameters", "data"):
            if not isinstance(self.get(section), dict):
                raise TypeError(f"{section} must be a dictionary")
        self.validate_session(self, project=True)

    @staticmethod
    def describe_policies() -> dict:
        """UI용 정책 필드·허용 형식·설명. 실행 객체나 계산기 함수는 포함하지 않는다."""
        return describe_policies()

    def configure_policies(self, changes: dict) -> dict:
        """정책 일부를 병합·검증한 뒤 반영하고 독립 사본을 반환한다.

        검증 실패 시 기존 설정은 그대로 유지한다. 파일 저장과 잠금은
        ProjectManager가 담당하며 이 메서드는 메모리의 설정만 변경한다.
        """
        self.validate_json(changes)
        candidate = deepcopy(self)
        candidate["policies"] = self.merge(candidate.get("policies", {}), changes)
        candidate.validate()
        self["policies"] = candidate.policies
        return deepcopy(self.policies)

    @classmethod
    def validate_session(cls, config: dict, *, project: bool = False) -> None:
        cls.validate_json(config)
        unknown = config.keys() - ({"parameters", "data", "policies"} if project else {"parameters", "data"})
        if unknown:
            raise ValueError(f"Unsupported configuration sections: {sorted(unknown)}; use data for Application metadata")
        if not project and "policies" in config:
            raise ValueError("Execution policies belong to ProjectConfig, not Session configuration")
        removed = {"completion", "engines", "component_configurations", "session_defaults", "default_engine"} & config.keys()
        if removed:
            raise ValueError(f"Unsupported configuration sections: {sorted(removed)}; use targeted parameters")
        for name in ("parameters", "data"):
            if name in config and not isinstance(config[name], dict):
                raise TypeError("Session configuration sections must be dictionaries")
        parameters = config.get("parameters", {})
        unknown = parameters.keys() - {"engines", "components"}
        if unknown:
            raise ValueError(f"Unsupported parameter targets: {sorted(unknown)}")
        if not project and "components" in parameters:
            raise ValueError("Component parameters belong to ProjectConfig, not Session configuration")
        for category in ("engines", "components"):
            targets = parameters.get(category, {})
            if not isinstance(targets, dict) or any(not isinstance(options, dict) for options in targets.values()):
                raise TypeError(f"parameters.{category} must map target names to dictionaries")
            from .schema import validate_parameters
            for name, options in targets.items():
                validate_parameters(options, scope=f"parameters.{category}.{name}")

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

    def resolve_engine_config(self, name: str, session_config: Optional[dict] = None) -> dict:
        """Detached configuration including arbitrary workspace and Session keys."""
        self.validate()
        session_config = session_config if session_config is not None else {}
        self.validate_session(session_config)
        result = self.merge(dict(self), session_config)
        result["engine"] = deepcopy(result.get("parameters", {}).get("engines", {}).get(name, {}))
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        cls.validate_json(data)
        return cls(data)


@dataclass(slots=True)
# 프로젝트의 영속 데이터. 직접 실행하거나 런타임 객체를 저장하지 않는다.
class Project:
    COMPONENT_FIELDS = ("components", "initialized_components")

    id: str
    title: str
    paths: ProjectPaths
    config: ProjectConfig = field(default_factory=ProjectConfig)
    created_at: str = field(default_factory=now)
    deleted: bool = False
    # Component identities only. Each component owns its configuration and paths.
    components: tuple[str, ...] = ()
    # Managed storage lifetime, retained when a Component is disabled.
    initialized_components: tuple[str, ...] = ()
    # None은 사용자 주입 대화 팩토리의 저장 방식을 따른다.
    conversation_storage: Optional[str] = None
    storage_version: int = DOMAIN_STORAGE_VERSION

    @classmethod
    def from_record(cls, data: dict, paths: ProjectPaths) -> "Project":
        """Read the current record without applying creation defaults."""
        _stored_fields(cls, data)
        ProjectConfig.validate_session(data["config"], project=True)
        if not isinstance(data["config"], dict) or data["config"].keys() != {"policies", "parameters", "data"}:
            raise ValueError("Stored Project configuration requires policies, parameters and data")
        if type(data["deleted"]) is not bool:
            raise ValueError("Project deleted must be a boolean")
        for key in cls.COMPONENT_FIELDS:
            values = data[key]
            if (not isinstance(values, list) or any(not isinstance(name, str) or not name for name in values)
                    or len(set(values)) != len(values)):
                raise ValueError("Invalid Project Component identities")
        if not set(data["components"]) <= set(data["initialized_components"]):
            raise ValueError("Selected Components require initialized storage")
        if not isinstance(data["title"], str) or not isinstance(data["created_at"], str):
            raise ValueError("Invalid Project title or creation time")
        created = datetime.fromisoformat(data["created_at"])
        if created.tzinfo is None:
            raise ValueError("Project creation time requires a timezone")
        return cls(**{**data, "paths": paths, "components": tuple(data["components"]),
                      "initialized_components": tuple(data["initialized_components"]),
                      "config": ProjectConfig.from_dict(data["config"])})

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

    @classmethod
    def from_record(cls, data, paths):
        _stored_fields(cls, data)
        ProjectConfig.validate_session(data["config"])
        status = SessionStatus(data["status"])
        current = data["current_run_id"]
        if ((status == SessionStatus.RUNNING) != (current is not None)
                or (current is not None and not current.strip())):
            raise ValueError("Session execution state requires its current Run declaration")
        return cls(**{**data, "status": status, "paths": paths})


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

    @classmethod
    def from_record(cls, data):
        _stored_fields(cls, data)
        return cls(**{**data, "role": MessageRole(data["role"]), "status": MessageStatus(data["status"])})


# ---------------------------------------------------------------------------
# Run: 확정된 요청 하나에 대한 Engine 실행
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# 하나의 사용자 요청에 대응하는 실행 기록.
class Run:
    ACTIVE_STATES = frozenset((RunStatus.PENDING, RunStatus.RUNNING))
    STARTED_STATES = frozenset((RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED))

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

    @classmethod
    def from_record(cls, data, paths):
        _stored_fields(cls, data)
        status = RunStatus(data["status"])
        if ((status in cls.STARTED_STATES and data["started_at"] is None)
                or (status == RunStatus.PENDING and data["started_at"] is not None)
                or ((status not in cls.ACTIVE_STATES) != (data["ended_at"] is not None))):
            raise ValueError("Run state requires its original start and end evidence")
        return cls(**{**data, "status": status, "paths": paths})


# ---------------------------------------------------------------------------
# Step: observable execution unit inside a Run
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# Run 내부의 관찰 가능한 작업 기록.
class Step:
    ACTIVE_STATES = frozenset((StepStatus.PENDING, StepStatus.RUNNING))
    STARTED_STATES = frozenset((StepStatus.RUNNING, StepStatus.COMPLETED))

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

    @classmethod
    def from_record(cls, data, paths):
        _stored_fields(cls, data)
        status = StepStatus(data["status"])
        if ((status in cls.STARTED_STATES and data["started_at"] is None)
                or (status == StepStatus.PENDING and data["started_at"] is not None)
                or ((status not in cls.ACTIVE_STATES) != (data["ended_at"] is not None))):
            raise ValueError("Step state requires its original start and end evidence")
        return cls(**{**data, "status": status, "paths": paths})

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
