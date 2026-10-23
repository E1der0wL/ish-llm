"""Run-local Engine snapshots and instruction channels; never persistent state or new authority."""

from dataclasses import dataclass, field, replace
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, ClassVar, Optional

from llm.core.models import Message, Project, Run, Session
from llm.core.configuration import UNSET
from llm.core.results import OutputVisibility, ResumeOutput
from llm.core.steering import SteeringMode
from llm.core.steering_channel import SteeringChannel
from llm.components.tools import ToolRegistry
from .events import EngineEvent


@dataclass(slots=True)
class SteeringInbox:
    """저장 ACK 뒤 서비스가 채우는 호출별 전달함. 영속 상태의 소유자가 아니다."""

    INVALID_CHILD: ClassVar[str] = "Child instruction channel requires a unique execution and stable scope"

    messages: tuple[Message, ...] = ()
    history: dict[str, Message] = field(default_factory=dict)
    target_id: str = SteeringChannel.ROOT
    scope: Optional[str] = None
    node_id: Optional[str] = None
    node_path: Optional[str] = None
    engine: str = ""
    mode: SteeringMode = SteeringMode.UNSUPPORTED
    channels: dict[str, "SteeringInbox"] = field(default_factory=dict, repr=False)
    acknowledged: Optional[str] = None
    opened: bool = False
    closed: bool = False

    def child(self, *, target_id: str, scope: str, engine: str, mode: SteeringMode,
              node_id: str, node_path: Optional[str]) -> "SteeringInbox":
        if not scope or target_id in self.channels:
            raise ValueError(self.INVALID_CHILD)
        child = SteeringInbox(history=dict(self.history), target_id=target_id, scope=scope,
                              engine=engine, mode=SteeringMode(mode), node_id=node_id, node_path=node_path,
                              channels=self.channels)
        self.channels[target_id] = child
        return child


@dataclass(frozen=True, slots=True)
# Run별 읽기 스냅샷과 임시 state/capability를 전달한다.
class EngineContext:
    NO_CHECKPOINT: ClassVar[None] = None
    NO_STEERING: ClassVar[None] = None
    NO_CHECKPOINT_SCOPE: ClassVar[None] = None
    NO_RESOLVER: ClassVar[None] = None
    NO_EXPANSION: ClassVar[None] = None
    CURRENT_ENGINE: ClassVar[None] = None
    NO_EXECUTOR: ClassVar[None] = None
    NO_METADATA: ClassVar[None] = None
    INVALID_CAPABILITIES: ClassVar[str] = "Capability requirements must be a tuple of names"
    MISSING_CAPABILITIES: ClassVar[str] = "Required capabilities unavailable: {missing}"
    INVALID_RESOLUTION: ClassVar[str] = "Capability resolver must return exactly the requested capabilities"
    INVALID_TOOL_KEY: ClassVar[str] = "Tool checkpoint_key must be a nonempty string"

    project: Project
    session: Session
    run: Run
    messages: tuple[Message, ...]
    # Per-Run snapshot of Project capabilities, never part of persisted models.
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    # Preparation outputs and handles for this Run only; never persisted.
    state: dict[str, Any] = field(default_factory=dict)
    # Engine이 선언한 capability만 Run별로 구성한다. 런타임 값은 저장하지 않는다.
    capabilities: dict[str, Any] = field(default_factory=dict)
    # 명시적 재개에서만 제공하는 저장 스냅샷. 런타임 핸들은 포함하지 않는다.
    checkpoint: Optional[dict] = NO_CHECKPOINT
    # 부모가 이벤트 저장을 중계하는 자식 실행 범위. Step 출력 소유권과 구분한다.
    checkpoint_scope: Optional[str] = NO_CHECKPOINT_SCOPE
    # 명시적 재개에서만 조회한 부분 출력. 완료/승인 증명이 아닌 scope별 참고 자료다.
    resume_outputs: tuple[ResumeOutput, ...] = ()
    # 백엔드가 주입하는 실행 정책. Run 전체(중첩 Agent/Graph 포함)가 공유한다.
    tool_scope: Any = None
    pending_work: Any = None
    # 호스트 계산기 레지스트리만 공유한다. 입력 정책은 사용하는 Engine이 별도 사본에 연결한다.
    token_counters: Mapping[str, Callable] = field(default_factory=dict)
    completion_policy: Any = None
    # 중첩 엔진의 최종 결과는 이 Step에 귀속한다. None은 최상위 Run이다.
    output_step_id: Optional[str] = None
    output_visibility: str = OutputVisibility.USER
    steering: Optional[SteeringInbox] = NO_STEERING
    # Service-owned, Run-local capability lookup; not persisted and never expands Tool authority.
    capability_resolver: Optional[Callable] = NO_RESOLVER

    def read_tool_binding(self, value):
        """Nested snapshots contain Engine payload; service usage stays on the outer envelope."""
        from llm.core.tool_selection_records import ToolBindingRecord
        return (ToolBindingRecord.from_checkpoint(value) if self.checkpoint_scope is self.NO_CHECKPOINT_SCOPE
                else ToolBindingRecord.read(value))

    def with_capabilities(self, names, *, expansion=NO_EXPANSION):
        """Resolve explicitly selected implementation requirements through the owning service."""
        if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
            raise ValueError(self.INVALID_CAPABILITIES)
        missing = tuple(dict.fromkeys(name for name in names if name not in self.capabilities))
        if not missing:
            return self
        if self.capability_resolver is self.NO_RESOLVER:
            raise ValueError(self.MISSING_CAPABILITIES.format(missing=missing))
        resolved = self.capability_resolver(missing, expansion=expansion)
        if not isinstance(resolved, dict) or set(resolved) != set(missing):
            raise ValueError(self.INVALID_RESOLUTION)
        return replace(self, capabilities={**self.capabilities, **resolved})

    def resolve_config(self, engine: Optional[str] = CURRENT_ENGINE) -> dict:
        return self.project.config.resolve_engine_config(self.run.engine if engine is self.CURRENT_ENGINE else engine, self.session.config)

    def execute_tool(self, tool, arguments, *, checkpoint_key: str, result: dict,
                     executor=NO_EXECUTOR, metadata=NO_METADATA, decision=UNSET) -> AsyncIterator[EngineEvent]:
        """기존 invocation key로 Tool을 연결한다. 승인 증명/새 ID를 생성하지 않는다.

        decision 생략 시 waiting Tool approval의 Interaction binding으로만 응답을
        해석한다. Workflow confirmation은 Tool 권한을 부여하지 않는다.
        executor 주입으로 명시된 실행 제한을 유지한다. 소비자는 aclosing을 사용한다.
        """
        from llm.services.runtime.tools import ToolExecutor
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise ValueError(self.INVALID_TOOL_KEY)
        executor = executor if executor is not self.NO_EXECUTOR else ToolExecutor()
        return executor.execute(tool, arguments, result=result, context=self, metadata=metadata,
                                decision=decision, request_key=checkpoint_key)
