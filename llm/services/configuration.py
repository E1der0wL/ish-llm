"""백엔드가 실행/조회에 공유할 서비스 의존성을 한 곳에서 조립한다."""

from dataclasses import field
from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.services.runtime.output import OutputPolicy
from typing import Callable, Optional, Union
from llm.core.models import Session
from llm.compat import dataclass
from llm.services.history.context import ConversationContextBuilder
from llm.services.history.conversation import Conversation, conversation_store
from llm.services.runtime.events import EventHandlers
from llm.services.infrastructure.logging import DomainLogger
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunRepository
from llm.services.lifecycle.steps import StepManager, StepRepository
from llm.services.lifecycle.sessions import SessionManager, SessionRepository
from llm.services.runtime.policies import ProjectPolicyResolver
from llm.services.runtime.tools import ToolPolicy


@dataclass
class ServiceConfig:
    """서비스를 한 번 구성한다. conversations는 프로젝트 저장의 기본값 또는 주입 팩토리다."""
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
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    conversation_cache_size: int = 32
    provider_limits: ProviderLimits = field(default_factory=ProviderLimits)
    provider_calls: Optional[ProviderCalls] = None
    output_policy: OutputPolicy = field(default_factory=OutputPolicy)
    output_index_stride: int = 128
    backups: Optional[DirectoryBackups] = None
    observability_sink: Optional[Callable] = None

    def build(self, workspace, components):
        """RunManager와 Facade가 같은 저장소/문맥/로그 설정을 공유하도록 연결한다."""
        projects = self.project_repository if self.project_repository is not None else ProjectRepository(workspace / "projects")
        if projects.root.absolute() != (workspace / "projects").absolute():
            raise ValueError("Project repository must belong to the backend workspace")
        projects.ownership.logger = self.logger
        runs = self.run_repository if self.run_repository is not None else RunRepository(output_index_stride=self.output_index_stride)
        steps = StepManager(self.step_repository)
        builder = self.context_builder if self.context_builder is not None else ConversationContextBuilder()
        sessions = SessionManager(self.session_repository, conversations=self.conversations, context_builder=builder, runs=runs,
                            conversation_cache_size=self.conversation_cache_size)
        return ProjectManager(projects, sessions, components=components, backups=self.backups, backup_steps=steps.repository), runs, steps
