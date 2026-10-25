"""백엔드가 실행/조회에 공유할 서비스 의존성을 한 곳에서 조립한다."""

from dataclasses import dataclass, field
from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.providers.runtime import ProviderDiagnostics
from llm.services.runtime.output import OutputBuffer
from typing import Callable, Optional, Union, ClassVar
from llm.core.models import Session
from llm.services.history.context import ConversationContextBuilder
from llm.services.history.conversation import Conversation, ProjectConversations, conversation_store
from llm.services.infrastructure.journal import OutputJournal
from llm.services.runtime.events import EventHandlers
from llm.services.infrastructure.logging import DomainLogger
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunRepository
from llm.services.lifecycle.steps import StepManager, StepRepository
from llm.services.lifecycle.sessions import SessionManager, SessionRepository
from llm.services.runtime.policies import ProjectPolicyResolver
from llm.services.runtime.tools import ToolRuntime
from llm.core.paths import BackendPaths
from .host_observations import ServiceProjection, ServiceIdentity, ToolRuntimeObservation, HostServiceSnapshot


@dataclass
class BackendServices:
    """서비스를 한 번 구성한다. conversations는 프로젝트 저장의 기본값 또는 주입 팩토리다."""

    UNSPECIFIED: ClassVar[None] = None
    INVALID_DIAGNOSTICS: ClassVar[str] = "provider_diagnostics must be ProviderDiagnostics"
    FOREIGN_REPOSITORY: ClassVar[str] = "Project repository must belong to the backend workspace"
    INVALID_TRANSFER_AUTHORIZER: ClassVar[str] = "transfer_authorize must be callable"

    project_repository: Optional[ProjectRepository] = None
    session_repository: Optional[SessionRepository] = None
    run_repository: Optional[RunRepository] = None
    step_repository: Optional[StepRepository] = None
    conversations: Union[str, Callable[[Session], Conversation]] = conversation_store
    context_builder: Optional[ConversationContextBuilder] = None
    token_counters: dict[str, Callable] = field(default_factory=dict)
    policy_resolver: Optional[ProjectPolicyResolver] = None
    event_handlers: EventHandlers = field(default_factory=EventHandlers)
    logger: DomainLogger = field(default_factory=DomainLogger)
    tool_runtime: ToolRuntime = field(default_factory=ToolRuntime, metadata={ServiceProjection.TOOL: 0})
    conversation_cache_size: int = field(
        default=ProjectConversations.DEFAULT_FILE_CACHE_SIZE, metadata={ServiceProjection.PRIMITIVE: 0}
    )
    provider_limits: ProviderLimits = field(
        default_factory=ProviderLimits, metadata={ServiceProjection.STRUCTURE: 0}
    )
    provider_calls: Optional[ProviderCalls] = None
    provider_diagnostics: Optional[ProviderDiagnostics] = field(
        default=None, metadata={ServiceProjection.STRUCTURE: 2, ServiceProjection.NONNULL: 0}
    )
    output_buffer: OutputBuffer = field(
        default_factory=OutputBuffer, metadata={ServiceProjection.STRUCTURE: 1}
    )
    output_index_stride: int = field(
        default=OutputJournal.DEFAULT_INDEX_STRIDE, metadata={ServiceProjection.PRIMITIVE: 1}
    )
    backups: Optional[DirectoryBackups] = None
    observability_sink: Optional[Callable] = None
    transfer_authorize: Optional[Callable] = None

    @classmethod
    def observed_sink(cls, services):
        """Read an optional host observation sink; this never grants authority."""
        try:
            return services.observability_sink
        except AttributeError:
            return cls.UNSPECIFIED

    def describe(self):
        """Host 자원의 읽기 전용 투영. callable/client/인증값을 직렬화하지 않는다."""
        from dataclasses import fields, asdict

        declarations = fields(self)
        values = {
            item.name: ServiceIdentity.observe(getattr(self, item.name)).to_dict() for item in declarations
        }
        for item in ServiceProjection.selected(declarations, ServiceProjection.STRUCTURE):
            value = getattr(self, item.name)
            values[item.name] = asdict(value) if value is not self.UNSPECIFIED else self.UNSPECIFIED
        for item in ServiceProjection.selected(declarations, ServiceProjection.NONNULL):
            if getattr(self, item.name) is not self.UNSPECIFIED:
                values[item.name] = {
                    key: value for key, value in values[item.name].items() if value is not self.UNSPECIFIED
                }
        for item in ServiceProjection.selected(declarations, ServiceProjection.PRIMITIVE):
            values[item.name] = getattr(self, item.name)
        for item in ServiceProjection.selected(declarations, ServiceProjection.TOOL):
            runtime = getattr(self, item.name) or ToolRuntime()
            values[item.name] = ToolRuntimeObservation.describe(runtime)
        return HostServiceSnapshot(values).to_dict()

    def build(self, workspace, components, *, engines=UNSPECIFIED):
        """RunManager와 Facade가 같은 저장소/문맥/로그 설정을 공유하도록 연결한다."""
        if self.provider_diagnostics is not self.UNSPECIFIED and not isinstance(
            self.provider_diagnostics, ProviderDiagnostics
        ):
            raise TypeError(self.INVALID_DIAGNOSTICS)
        projects = (
            self.project_repository
            if self.project_repository is not self.UNSPECIFIED
            else ProjectRepository(BackendPaths(workspace).projects)
        )
        if projects.root.absolute() != (BackendPaths(workspace).projects).absolute():
            raise ValueError(self.FOREIGN_REPOSITORY)
        projects.ownership.logger = self.logger
        runs = (
            self.run_repository
            if self.run_repository is not self.UNSPECIFIED
            else RunRepository(output_index_stride=self.output_index_stride)
        )
        steps = StepManager(self.step_repository)
        builder = (
            self.context_builder
            if self.context_builder is not self.UNSPECIFIED
            else ConversationContextBuilder()
        )
        sessions = SessionManager(
            self.session_repository,
            conversations=self.conversations,
            context_builder=builder,
            runs=runs,
            conversation_cache_size=self.conversation_cache_size,
        )
        sessions.transfers.authorize = self.transfer_authorize
        if self.transfer_authorize is not self.UNSPECIFIED and not callable(self.transfer_authorize):
            raise TypeError(self.INVALID_TRANSFER_AUTHORIZER)
        return (
            ProjectManager(
                projects,
                sessions,
                components=components,
                backups=self.backups,
                backup_steps=steps.repository,
                engines=engines,
            ),
            runs,
            steps,
        )
