"""Project → Session → Run → Step의 영속 데이터와 상태를 정의한다. asyncio 객체나 서비스 핸들은 이 모델에 저장하지 않는다."""

from llm.core.json_data import JsonObject
from .configuration_targets import ParameterTargets, ResolvedEngineConfiguration

from typing import Any, Optional, TYPE_CHECKING
from collections.abc import Iterator
import json
from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from uuid import uuid4

from .paths import ProjectPaths, RunPaths, StepPaths, SessionPaths
from .policies import normalize_policies, describe_policies
from llm.providers.codes import ProviderErrorCode

if TYPE_CHECKING:
    from .results import EngineOutput


# Session 기반 경로·식별자를 사용하는 영속 도메인 형식. 이전 Task 형식은 자동 변환하지 않는다.
DOMAIN_STORAGE_VERSION = 1


class DomainRecord:
    """Shared current-format mechanics; each model owns its complete record rules."""

    UNSUPPORTED_VERSION = "Unsupported domain storage_version"
    REQUIRED = "{model} record requires all current fields"
    WRONG_FIELDS = "{model} record requires all current fields; missing={missing}, unexpected={unexpected}"
    INVALID_FIELD = "Invalid {model} {field}"
    TIMEZONE_REQUIRED = "{model} {field} requires a timezone"

    @staticmethod
    def fields(model):
        return tuple(item for item in fields(model) if item.name != Project.paths.__name__)

    @classmethod
    def serialize(cls, model):
        return {item.name: getattr(model, item.name) for item in cls.fields(model)}

    @classmethod
    def validate_version(cls, data):
        key = Project.storage_version.__name__
        if not isinstance(data, dict) or type(data.get(key)) is not int or data[key] != DOMAIN_STORAGE_VERSION:
            raise ValueError(cls.UNSUPPORTED_VERSION)


def validate_domain_version(data):
    """Shared Project/Session/Run/Step current storage version guard."""
    DomainRecord.validate_version(data)

# ---------------------------------------------------------------------------
# 공통 ID와 UTC 시각 생성
# ---------------------------------------------------------------------------
def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stored_fields(model, data):
    """The domain's serialized fields are mandatory even when creation has defaults."""
    declarations = DomainRecord.fields(model)
    if not isinstance(data, dict):
        raise ValueError(DomainRecord.REQUIRED.format(model=model.__name__))
    expected = {item.name for item in declarations}
    if data.keys() != expected:
        raise ValueError(DomainRecord.WRONG_FIELDS.format(model=model.__name__,
            missing=sorted(expected - data.keys()), unexpected=sorted(data.keys() - expected)))
    if Project.storage_version.__name__ in expected:
        validate_domain_version(data)
    for item in declarations:
        value = data[item.name]
        if item.type in (str, dict, bool) and not isinstance(value, item.type):
            raise ValueError(DomainRecord.INVALID_FIELD.format(model=model.__name__, field=item.name))
        if item.type == Optional[str] and value is not None and not isinstance(value, str):
            raise ValueError(DomainRecord.INVALID_FIELD.format(model=model.__name__, field=item.name))
    for name in (Run.created_at.__name__, Run.started_at.__name__, Run.ended_at.__name__):
        if name in data and data[name] is not None:
            if datetime.fromisoformat(data[name]).tzinfo is None:
                raise ValueError(DomainRecord.TIMEZONE_REQUIRED.format(model=model.__name__, field=name))


# ---------------------------------------------------------------------------
# 영속 역할과 수명 주기 상태
# ---------------------------------------------------------------------------

class ConversationStorageInput:
    ALLOW_INJECTED = True
    EXPLICIT = False
    INJECTED = None
    INVALID = "Conversation storage must be 'file' or 'memory'"
    DESCRIPTION = "null은 주입 저장소 전용. Session이 존재하면 변경할 수 없다."


class ConversationStorage(StrEnum):
    """Project-owned storage selection; None preserves an injected factory."""

    FILE = "file"
    MEMORY = "memory"

    @classmethod
    def validate(cls, value, *, nullable=ConversationStorageInput.ALLOW_INJECTED):
        if value is ConversationStorageInput.INJECTED and nullable:
            return
        if not isinstance(value, str) or value not in tuple(cls):
            raise ValueError(ConversationStorageInput.INVALID)

    @classmethod
    def schema(cls):
        from .schema import SchemaKeyword, SchemaType
        return {SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL],
                SchemaKeyword.ENUM: [member.value for member in cls] + [ConversationStorageInput.INJECTED],
                SchemaKeyword.DESCRIPTION: ConversationStorageInput.DESCRIPTION}


class ResourceKind(StrEnum):
    """Built-in domain reference kinds; Component reference kinds remain open."""

    PROJECT = "project"
    SESSION = "session"
    RUN = "run"
    STEP = "step"
    MESSAGE = "message"


class StepKind(StrEnum):
    """Shared execution Step meanings; custom Engine kinds remain strings."""

    LLM = "llm"
    TOOL = "tool"
    SHELL = "shell"


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


class RunErrorCode(StrEnum):
    AGENT_REQUIRED = "agent_required"
    ENGINE_REQUIRED = "engine_required"
    ENGINE_NOT_REGISTERED = "engine_not_registered"
    COMPONENT_NOT_REGISTERED = "component_not_registered"
    CAPABILITY_FAILED = "capability_failed"
    ENGINE_FAILED = "engine_failed"
    PROVIDER_CAPACITY = ProviderErrorCode.CAPACITY.value
    INTERRUPTED = "interrupted"
    PROCESS_RESTART = "process_restart"
    RESUME_REJECTED = "resume_rejected"
    QUEUE_FULL = "queue_full"
    POLICY_UNAVAILABLE = "policy_unavailable"
    REQUEST_CANCELLED = "request_cancelled"
    STEERING_UNAVAILABLE = "steering_unavailable"
    RUN_TIMEOUT = "run_timeout"
    TOOL_DENIED = "tool_denied"
    TOOL_CONTRACT = "tool_contract"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    OPERATION_CONFLICT = "operation_conflict"
    OPERATION_UNCERTAIN = "operation_uncertain"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PROCESS_FAILED = "process_failed"


class RunTransition(StrEnum):
    START = "start"
    PENDING_CANCEL = "pending_cancel"
    FINISH = "finish"
    RECOVERY = "recovery"


class RunLifecycle:
    UNKNOWN_REASON = "Unknown Run transition reason: {reason}"
    INVALID = "Invalid Run transition ({reason}): {current} -> {target}"
    EDGES = MappingProxyType({
        RunTransition.START: frozenset(((RunStatus.PENDING, RunStatus.RUNNING),)),
        RunTransition.PENDING_CANCEL: frozenset(((RunStatus.PENDING, RunStatus.CANCELLED),)),
        RunTransition.FINISH: frozenset((RunStatus.RUNNING, status) for status in (
            RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.PAUSED)),
        RunTransition.RECOVERY: frozenset((status, RunStatus.INTERRUPTED) for status in (RunStatus.PENDING, RunStatus.RUNNING)),
    })


def validate_run_transition(current: RunStatus, target: RunStatus, *, reason: str | None = None) -> None:
    """Run 상태 변경의 순수 검증. 저장·시각 생성·다른 도메인 변경은 하지 않는다.

    종료한 실행 시도는 다시 열지 않는다. resume는 서비스가 별도 Run을 생성한다.
    미접수 대기 요청 취소는 Message의 CANCELLED 전이다.
    fan-out 후 미시작 Run은 pending_cancel로만 CANCELLED가 될 수 있다.
    """
    current, target = RunStatus(current), RunStatus(target)
    if reason is not None and reason not in RunLifecycle.EDGES:
        raise ValueError(RunLifecycle.UNKNOWN_REASON.format(reason=reason))
    edges = RunLifecycle.EDGES.values() if reason is None else (RunLifecycle.EDGES[reason],)
    allowed = any((current, target) in values for values in edges)
    if not allowed:
        raise ValueError(RunLifecycle.INVALID.format(reason=reason, current=current.value, target=target.value))


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StepErrorCode(StrEnum):
    FAILED = "step_failed"
    TIMEOUT = "step_timeout"


class StepLifecycle:
    INVALID = "Invalid Step transition: {current} -> {target}"
    EDGES = frozenset({(StepStatus.PENDING, StepStatus.RUNNING), (StepStatus.RUNNING, StepStatus.COMPLETED)}
        | {(current, target) for current in (StepStatus.PENDING, StepStatus.RUNNING)
           for target in (StepStatus.FAILED, StepStatus.INTERRUPTED, StepStatus.CANCELLED)})


def validate_step_transition(current: StepStatus, target: StepStatus) -> None:
    """The Step owner permits pre-start failure, interruption and cancellation."""
    current, target = StepStatus(current), StepStatus(target)
    if (current, target) not in StepLifecycle.EDGES:
        raise ValueError(StepLifecycle.INVALID.format(current=current.value, target=target.value))


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

    POLICIES = "policies"
    PARAMETERS = "parameters"
    DATA = "data"
    SECTIONS = (POLICIES, PARAMETERS, DATA)
    SESSION_SECTIONS = (PARAMETERS, DATA)
    RETIRED_SECTIONS = ("completion", ParameterTargets.ENGINES, "component_configurations", "session_defaults", "default_engine")
    PYTHON_PRIVATE_PREFIX = "__"
    INVALID_SECTION = "{section} must be a dictionary"
    UNKNOWN_SECTIONS = "Unsupported configuration sections: {unknown}; use data for Application metadata"
    SESSION_POLICIES = "Execution policies belong to ProjectConfig, not Session configuration"
    RETIRED_CONFIGURATION = "Unsupported configuration sections: {removed}; use targeted parameters"
    INVALID_SESSION_SECTION = "Session configuration sections must be dictionaries"
    ENSURE_ASCII = False

    def __init__(self, values: dict | Iterator[tuple[str, Any]] | None = None, **config) -> None:
        sections = {key: {} for key in self.SECTIONS}
        if values is not None:
            if isinstance(values, Iterator):
                values = dict(values)
            self.validate_json(values)
            sections.update(deepcopy(dict(values)))
        sections.update(deepcopy(config))
        super().__init__(sections)
        self.validate()

    def __getattr__(self, name):
        if name.startswith(self.PYTHON_PRIVATE_PREFIX):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value

    @staticmethod
    def validate_json(value: dict) -> None:
        """Public configuration entry point for the shared JSON object validator."""
        JsonObject.validate(value)

    def validate(self) -> None:
        self.validate_json(self)
        self[self.POLICIES] = normalize_policies(self.get(self.POLICIES, {}))
        for section in self.SESSION_SECTIONS:
            if not isinstance(self.get(section), dict):
                raise TypeError(self.INVALID_SECTION.format(section=section))
        self.validate_session(self, project=ParameterTargets.PROJECT_SCOPE)

    @staticmethod
    def describe_policies() -> dict:
        """UI용 정책 필드·허용 형식·설명. 실행 객체나 계산기 함수는 포함하지 않는다."""
        return describe_policies()

    def bind_policy_snapshot(self, policies):
        """Bind admitted evidence to a detached execution configuration, without merging."""
        self[self.POLICIES] = deepcopy(policies)

    def configure_policies(self, changes: dict) -> dict:
        """정책 일부를 병합·검증한 뒤 반영하고 독립 사본을 반환한다.

        검증 실패 시 기존 설정은 그대로 유지한다. 파일 저장과 잠금은
        ProjectManager가 담당하며 이 메서드는 메모리의 설정만 변경한다.
        """
        self.validate_json(changes)
        candidate = deepcopy(self)
        candidate[self.POLICIES] = self.merge(candidate.get(self.POLICIES, {}), changes)
        candidate.validate()
        self[self.POLICIES] = candidate.policies
        return deepcopy(self.policies)

    @classmethod
    def validate_session(cls, config: dict, *, project: bool = ParameterTargets.SESSION_SCOPE) -> None:
        cls.validate_json(config)
        unknown = config.keys() - set(cls.SECTIONS if project else cls.SESSION_SECTIONS)
        if unknown:
            raise ValueError(cls.UNKNOWN_SECTIONS.format(unknown=sorted(unknown)))
        if not project and cls.POLICIES in config:
            raise ValueError(cls.SESSION_POLICIES)
        removed = set(cls.RETIRED_SECTIONS) & config.keys()
        if removed:
            raise ValueError(cls.RETIRED_CONFIGURATION.format(removed=sorted(removed)))
        for name in cls.SESSION_SECTIONS:
            if name in config and not isinstance(config[name], dict):
                raise TypeError(cls.INVALID_SESSION_SECTION)
        ParameterTargets.validate(config.get(cls.PARAMETERS, {}), project=project)

    @property
    def parameter_targets(self):
        return ParameterTargets.read(self[self.PARAMETERS])

    def set_component_parameters(self, name, value):
        candidate = self.parameter_targets.with_component(name, value)
        self[self.PARAMETERS] = candidate.to_dict()

    def set_engine_parameters(self, name, value):
        candidate = self.parameter_targets.with_engine(name, value)
        self[self.PARAMETERS] = candidate.to_dict()

    def remove_component_parameters(self, name):
        self[self.PARAMETERS] = self.parameter_targets.without_component(name).to_dict()

    def remove_engine_parameters(self, name):
        self[self.PARAMETERS] = self.parameter_targets.without_engine(name).to_dict()

    @classmethod
    def engine_parameter_path(cls, name):
        return cls.PARAMETERS, ParameterTargets.ENGINES, name

    @classmethod
    def without_engine_parameters(cls, value, *names):
        """Detached export redaction, preserving draft omission and unrelated fields."""
        result = deepcopy(value)
        engines = result.get(cls.PARAMETERS, {}).get(ParameterTargets.ENGINES, {})
        for name in names:
            engines.pop(name, None)
        return result

    def to_dict(self) -> dict:
        self.validate()
        return deepcopy(dict(self))

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=self.ENSURE_ASCII, allow_nan=JsonObject.ALLOW_NON_FINITE)

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
        return ResolvedEngineConfiguration.create(result, name).to_dict()

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        cls.validate_json(data)
        return cls(data)


@dataclass(slots=True)
# 프로젝트의 영속 데이터. 직접 실행하거나 런타임 객체를 저장하지 않는다.
class Project:
    ACTIVE = False
    DELETED = True
    STORED_CONFIG = 'Stored Project configuration requires policies, parameters and data'
    INVALID_DELETED = 'Project deleted must be a boolean'
    INVALID_COMPONENTS = 'Invalid Project Component identities'
    UNINITIALIZED_COMPONENTS = 'Selected Components require initialized storage'
    INVALID_TITLE_TIME = 'Invalid Project title or creation time'
    INVALID_TIMEZONE = 'Project creation time requires a timezone'
    INVALID_COMPONENT_SEQUENCE = 'Project Component identities must be a sequence'

    id: str
    title: str
    paths: ProjectPaths
    config: ProjectConfig = field(default_factory=ProjectConfig)
    created_at: str = field(default_factory=now)
    deleted: bool = ACTIVE
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
        ProjectConfig.validate_session(data[cls.config.__name__], project=ParameterTargets.PROJECT_SCOPE)
        if not isinstance(data[cls.config.__name__], dict) or data[cls.config.__name__].keys() != set(ProjectConfig.SECTIONS):
            raise ValueError(Project.STORED_CONFIG)
        if type(data[cls.deleted.__name__]) is not bool:
            raise ValueError(Project.INVALID_DELETED)
        for key in (cls.components.__name__, cls.initialized_components.__name__):
            values = data[key]
            if (not isinstance(values, list) or any(not isinstance(name, str) or not name for name in values)
                    or len(set(values)) != len(values)):
                raise ValueError(Project.INVALID_COMPONENTS)
        if not set(data[cls.components.__name__]) <= set(data[cls.initialized_components.__name__]):
            raise ValueError(Project.UNINITIALIZED_COMPONENTS)
        if not isinstance(data[cls.title.__name__], str) or not isinstance(data[cls.created_at.__name__], str):
            raise ValueError(Project.INVALID_TITLE_TIME)
        created = datetime.fromisoformat(data[cls.created_at.__name__])
        if created.tzinfo is None:
            raise ValueError(Project.INVALID_TIMEZONE)
        return cls(**{**data, cls.paths.__name__: paths, cls.components.__name__: tuple(data[cls.components.__name__]),
                      cls.initialized_components.__name__: tuple(data[cls.initialized_components.__name__]),
                      cls.config.__name__: ProjectConfig.from_dict(data[cls.config.__name__])})

    def to_record(self):
        value = DomainRecord.serialize(self)
        value[type(self).config.__name__] = self.config.to_dict()
        for name in (type(self).components.__name__, type(self).initialized_components.__name__):
            if not isinstance(value[name], (tuple, list)):
                raise ValueError(self.INVALID_COMPONENT_SEQUENCE)
            value[name] = list(value[name])
        type(self).from_record(value, self.paths)
        return value

    def __post_init__(self) -> None:
        self.validate_conversation_storage()

    def validate_conversation_storage(self) -> None:
        """저장 방식은 설정 데이터와 구분되는 프로젝트 수명 주기 속성이다."""
        ConversationStorage.validate(self.conversation_storage)


# ---------------------------------------------------------------------------
# Session: long-lived session state (runtime asyncio objects live in services)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# 대화 세션의 영속 데이터. 큐와 실행 태스크는 SessionRuntime에 둔다.
class Session:
    DELETED_ERROR = "Session is deleted"
    INVALID_AGENTS = 'Session agent_ids must be a list'
    NONCANONICAL_AGENTS = 'Session agent_ids must be unique and canonical'
    INVALID_EXECUTION_STATE = 'Session execution state requires its exact active Run declarations'
    RETAIN = "retain"
    id: str
    project_id: str
    title: str
    paths: SessionPaths
    status: SessionStatus = SessionStatus.IDLE
    active_run_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now)
    metadata: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    agent_ids: list[str] = field(default_factory=list)
    storage_version: int = DOMAIN_STORAGE_VERSION

    @property
    def retained(self):
        return self.metadata.get(self.RETAIN)

    @staticmethod
    def validate_agents(values):
        from .agents import AgentDefinition
        if not isinstance(values, list):
            raise ValueError(Session.INVALID_AGENTS)
        for identifier in values:
            AgentDefinition.identifier(identifier)
        if values != sorted(set(values)):
            raise ValueError(Session.NONCANONICAL_AGENTS)

    @classmethod
    def from_record(cls, data, paths):
        _stored_fields(cls, data)
        ProjectConfig.validate_session(data[cls.config.__name__])
        cls.validate_agents(data[cls.agent_ids.__name__])
        status = SessionStatus(data[cls.status.__name__])
        active = data[cls.active_run_ids.__name__]
        if (not isinstance(active, list) or any(not isinstance(value, str) or not value.strip() for value in active)
                or active != sorted(set(active)) or ((status == SessionStatus.RUNNING) != bool(active))):
            raise ValueError(Session.INVALID_EXECUTION_STATE)
        return cls(**{**data, cls.status.__name__: status, cls.paths.__name__: paths})


# ---------------------------------------------------------------------------
# Message: JSONL 이벤트로 복원하는 Session 대화 상태
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# Session 대화의 메시지와 상태. Run ID로 실행과 연결된다.
class Message:
    CONVERSATION_DELETED = "conversation_deleted"
    DELETION_MARKER = True
    UNBOUND_RUN = None
    PENDING_STATES = (MessageStatus.QUEUED, MessageStatus.STREAMING)

    id: str
    role: MessageRole
    content: str
    status: MessageStatus
    run_id: Optional[str] = UNBOUND_RUN
    created_at: str = field(default_factory=now)
    metadata: dict = field(default_factory=dict)

    @property
    def conversation_deleted(self) -> bool:
        return bool(self.metadata.get(self.CONVERSATION_DELETED))

    def detach_run(self):
        """Clear the source Run link on an already detached conversation copy."""
        self.run_id = self.UNBOUND_RUN

    def validate_changes(self, **changes):
        """Validate a proposed mutation with the complete stored Message contract."""
        self.from_record({**DomainRecord.serialize(self), **changes})

    @classmethod
    def deletion_metadata(cls) -> dict:
        return {cls.CONVERSATION_DELETED: cls.DELETION_MARKER}

    @classmethod
    def from_record(cls, data):
        _stored_fields(cls, data)
        return cls(**{**data, cls.role.__name__: MessageRole(data[cls.role.__name__]), cls.status.__name__: MessageStatus(data[cls.status.__name__])})


# ---------------------------------------------------------------------------
# Run: 확정된 요청 하나에 대한 Engine 실행
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RunIndex:
    """Ordering/cache projection; selecting a row still invokes the full Run Reader."""

    id: str
    session_id: str
    status: RunStatus
    started_at: Optional[str]
    ended_at: Optional[str]


@dataclass(slots=True)
# 하나의 사용자 요청에 대응하는 실행 기록.
class Run:
    INVALID_BINDING = 'Run Agent identity must match its original binding'
    INVALID_EXECUTION_STATE = 'Run state requires its original start and end evidence'
    ACTIVE_STATES = frozenset((RunStatus.PENDING, RunStatus.RUNNING))
    STARTED_STATES = frozenset((RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED))

    id: str
    session_id: str
    input_message_id: str
    assistant_message_id: str
    engine: str
    paths: RunPaths
    agent_id: str = field(kw_only=True)
    status: RunStatus = RunStatus.PENDING
    created_at: str = field(default_factory=now)
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    error_code: Optional[str] = None
    storage_version: int = DOMAIN_STORAGE_VERSION

    @classmethod
    def index_record(cls, data):
        """Read-only ordering projection; selected rows still use the complete Reader."""
        return RunIndex(id=data[cls.id.__name__], session_id=data[cls.session_id.__name__],
                               status=RunStatus(data[cls.status.__name__]),
                               started_at=data[cls.started_at.__name__], ended_at=data[cls.ended_at.__name__])

    @classmethod
    def from_record(cls, data, paths):
        _stored_fields(cls, data)
        from llm.core.agents import AgentBinding
        binding = AgentBinding.from_metadata(data[cls.metadata.__name__])
        if data[cls.agent_id.__name__] != binding.agent_id or data[cls.engine.__name__] != binding.engine:
            raise ValueError(Run.INVALID_BINDING)
        status = RunStatus(data[cls.status.__name__])
        if ((status in cls.STARTED_STATES and data[cls.started_at.__name__] is None)
                or (status == RunStatus.PENDING and data[cls.started_at.__name__] is not None)
                or ((status not in cls.ACTIVE_STATES) != (data[cls.ended_at.__name__] is not None))):
            raise ValueError(Run.INVALID_EXECUTION_STATE)
        return cls(**{**data, cls.status.__name__: status, cls.paths.__name__: paths})


# ---------------------------------------------------------------------------
# Step: observable execution unit inside a Run
# ---------------------------------------------------------------------------

@dataclass(slots=True)
# Run 내부의 관찰 가능한 작업 기록.
class Step:
    INVALID_EXECUTION_STATE = 'Step state requires its original start and end evidence'
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
        status = StepStatus(data[cls.status.__name__])
        if ((status in cls.STARTED_STATES and data[cls.started_at.__name__] is None)
                or (status == StepStatus.PENDING and data[cls.started_at.__name__] is not None)
                or ((status not in cls.ACTIVE_STATES) != (data[cls.ended_at.__name__] is not None))):
            raise ValueError(Step.INVALID_EXECUTION_STATE)
        return cls(**{**data, cls.status.__name__: status, cls.paths.__name__: paths})

    @property
    def progress(self):
        from .execution_observations import ExecutionObservations
        return ExecutionObservations.progress(self.metadata)

    @property
    def diagnostic(self):
        from .contracts import Diagnostic, ResourceRef
        from .execution_observations import ExecutionObservations
        value = ExecutionObservations.diagnostic(self.metadata)
        if value is not None:
            return value
        return Diagnostic(StepErrorCode.FAILED, self.error,
            source=ResourceRef(ResourceKind.STEP, self.id, step_id=self.id, run_id=self.run_id)) if self.error else None

    @property
    def output(self) -> Optional["EngineOutput"]:
        """Step에 귀속된 공통 결과를 독립된 조회 스냅샷으로 반환한다."""
        from .execution_observations import ExecutionObservations
        return ExecutionObservations.step_output(self.metadata)
