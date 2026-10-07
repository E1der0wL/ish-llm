"""백엔드가 실행/조회에 공유할 서비스 의존성을 한 곳에서 조립한다."""

from dataclasses import dataclass, field
from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.services.runtime.output import OutputBuffer
from typing import Callable, Optional, Union
from llm.core.models import Session
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
from llm.services.runtime.tools import ToolRuntime


@dataclass
class BackendServices:
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
    tool_runtime: ToolRuntime = field(default_factory=ToolRuntime)
    conversation_cache_size: int = 32
    provider_limits: ProviderLimits = field(default_factory=ProviderLimits)
    provider_calls: Optional[ProviderCalls] = None
    output_buffer: OutputBuffer = field(default_factory=OutputBuffer)
    output_index_stride: int = 128
    backups: Optional[DirectoryBackups] = None
    observability_sink: Optional[Callable] = None

    def describe(self):
        """Host 자원의 읽기 전용 투영. callable/client/인증값을 직렬화하지 않는다."""
        from dataclasses import fields, asdict
        def identity(value):
            return {"configured": value is not None,
                    "implementation": type(value).__module__ + "." + type(value).__qualname__ if value is not None else None}
        values = {item.name: identity(getattr(self, item.name)) for item in fields(self)}
        for key in ("provider_limits", "output_buffer"):
            value = getattr(self, key)
            values[key] = asdict(value) if value is not None else None
        for key in ("conversation_cache_size", "output_index_stride"):
            values[key] = getattr(self, key)
        runtime = self.tool_runtime or ToolRuntime()
        values["tool_runtime"] = {name: identity(getattr(runtime, name)) for name in
            ("authorize", "runner", "operation_key", "operation_probe", "classify")}
        values["tool_runtime"].update(revision=runtime.revision,
                                     retry_safe_tools=list(runtime.retry_safe_tools))
        return {"values": values, "x-owner": "host", "x-scope": "infrastructure"}

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
