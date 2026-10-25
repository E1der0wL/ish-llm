"""Agent 업무 정의를 등록 엔진에 연결한다. 모든 실행은 부모 Run 안에서 관찰된다."""

from llm.core.configuration_views import ConfigurationView

from llm.core.checkpoint_records import CheckpointWrite, CheckpointRecords, CheckpointPause
from llm.core.tool_event_result import ToolEventResult

from llm.core.continuations import ResumeRecord

from llm.core.results import OutputVisibility

import asyncio
import json
from contextlib import AsyncExitStack, aclosing, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace


from asyncio import timeout
from llm.core.agents import AgentBinding, AgentDefinition, AgentMode, AgentProfile
from llm.components.base import Component
from llm.components.tools import ToolRegistry
from llm.core.models import new_id
from llm.services.infrastructure.storage import revision_token
from llm.core.results import EngineOutput
from llm.services.runtime.tools import ToolExecutionScope
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType, required_capabilities, steering_mode, agent_mode
from llm.core.steering import SteeringMode
from llm.engines.registry import EngineRegistry
from .graph.engine import _GraphPause, GraphNodeContext
from .graph.checkpoints import EngineCheckpointScope
from .agent_resources import AgentResourceResolver
from .contracts import EngineHooks
from .agent_checkpoints import AgentCheckpoint
from .graph.selections import GraphHandlerSelection
from .agent_values import (AgentNodeDefinition, AgentExecutionBinding, AgentStepMetadata,
                           AgentInvocationIdentity, AgentInvocationBinding, AgentExecutionRevision,
                           AgentOutputValue, AgentDelegationArguments, AgentHandlerBinding)


from llm.core.agent_views import AgentDefinitions


class AgentExecution:
    """저장된 업무를 Graph 노드로 실행한다.

    engines의 각 엔진은 동기 for_agent(definition)으로 호출별 실행기를 반환한다.
    LoopEngine은 이 계약을 제공한다. 커스텀 엔진도 같은 이벤트/ToolExecutor 계약을
    지켜야 한다. GraphEngine은 부모 GraphNodeContext의 중첩 실행기를 사용한다.
    입력은 노드의 inputs, 출력은 {text, data?}이며 Agent schema도 이 계약에 적용한다.
    """

    required_capabilities = (AgentDefinition.CAPABILITY, ToolRegistry.CAPABILITY)
    # Graph 모드의 준비/매핑은 외부 효과를 수행하지 않고 자식 작업만 조율한다.
    resumable_container = True
    selection_binding = True
    DEFAULT_REVISION = "1"
    STEP_KIND = "agent"
    INLINE_WORKFLOW = False
    WRAPPED_WORKFLOW = True
    INPUT_ENSURE_ASCII = False
    INPUT_ALLOW_NAN = False
    INVALID_REVISION = "AgentNode revision must be nonempty text"
    UNSUPPORTED_INSTRUCTIONS = "Agent Prompt/Skill composition requires Engine.with_instructions"
    INVALID_CAPABILITIES = "additional_capabilities must return a tuple of names"
    MISSING_GRAPH_SCOPE = "Graph Agent requires a parent Graph execution scope"
    MISSING_STEERING_CHECKPOINT = "Steerable Agent engine requires a durable checkpoint contract"
    WRONG_CHECKPOINT_OWNER = "Agent engines cannot own Workflow checkpoint lifecycle"
    MISSING_PAUSE_SCOPE = "Agent engine requires a checkpoint scope to pause"
    WRONG_OUTPUT_STEP = "Agent engine output must belong to its Agent Step"
    MISSING_OUTPUT = "Agent engine must emit an EngineOutput"
    NO_ENGINE = None
    NO_BRIDGE = None

    @classmethod
    def step_agent_id(cls, step):
        """Read this execution Owner's top-level Step selection for deletion guards."""
        return AgentStepMetadata.selected_id(step.metadata)

    @staticmethod
    def agent_references(node):
        """The action owner interprets its stored selection without resolving an Engine."""
        return AgentNodeDefinition(node).references()

    @staticmethod
    def referenced_agents(record):
        """Read only execution-owned selections, never arbitrary application metadata."""
        from llm.core.checkpoints import CheckpointEnvelope, unwrap_record
        while CheckpointEnvelope.is_record(record):
            record, _ = unwrap_record(record)
        selections = (*AgentCheckpoint.bindings(record), *GraphHandlerSelection.bindings(record))
        return set().union(*(AgentExecutionBinding.references(value) for value in selections))

    @staticmethod
    def describe_config():
        return AgentNodeDefinition.describe_config()

    def __init__(self, *, engines, revision: str = DEFAULT_REVISION):
        if not isinstance(revision, str) or not revision:
            raise ValueError(self.INVALID_REVISION)
        self.revision = revision
        if isinstance(engines, EngineRegistry):
            self.engines = engines
        else:
            self.engines = EngineRegistry()
            for name, engine in engines.items():
                self.engines.register(name, engine)

    def _profile(self, definition, capabilities):
        identifier = AgentNodeDefinition(definition).agent_id
        profile = AgentDefinitions.selected(capabilities.get(AgentDefinition.CAPABILITY, ()), identifier)
        return AgentProfile(identifier, profile)

    def _engine(self, profile):
        return self.engines.resolve_agent(profile.to_dict())

    def _resources(self, profile, context):
        return AgentResourceResolver.select(profile, context)

    async def connect_resources(self, resources, context, tools, stack):
        await AgentResourceResolver.connect(resources, context, tools, stack)

    @staticmethod
    def compose_instructions(engine, resources, context):
        return AgentResourceResolver.compose(engine, resources, context)

    def additional_capabilities(self, definition, capabilities):
        profile = self._profile(definition, capabilities)
        return tuple(dict.fromkeys((*required_capabilities(self._engine(profile)),
                                    *AgentResourceResolver.capabilities(profile))))

    def graph_engine(self, definition, capabilities):
        """Graph 사전 탐색에 하위 실행 정의를 선언한다. 실행은 하지 않는다."""
        engine = self._engine(self._profile(definition, capabilities))
        return engine if agent_mode(engine) == AgentMode.WORKFLOW else self.NO_ENGINE

    def graph_context(self, definition, context):
        """Graph 환경은 부모 Run 권한을 공유한다. wrapper Tool ceiling은 없다."""
        return context

    def discover_graph(self, definition, capabilities):
        """Structural Workflow references only; private Agent options are selected later."""
        profile = self._profile(definition, capabilities)
        engine = self.engines.get(profile.engine)
        if agent_mode(engine) != AgentMode.WORKFLOW:
            return self.NO_ENGINE
        return engine.discover_agent(profile.to_dict())

    def checkpoint_engine(self, definition, context):
        """저장·재개 계약을 제공하는 자식만 조율 노드로 선언한다."""
        engine = self._engine(self._profile(definition, context.capabilities))
        return engine if EngineHooks.checkpoint_name(engine) and callable(EngineHooks.resume_validator(engine)) else self.NO_ENGINE

    def instruction_engine(self, definition, context):
        """등록된 공개 소비 기능을 선언한다. 선택 시 factory의 동일 선언을 검증한다."""
        profile = self._profile(definition, context.capabilities)
        return profile.engine if steering_mode(self.engines.get(profile.engine)) == SteeringMode.CONSUME else self.NO_ENGINE

    def binding(self, definition, context):
        """재개 검사와 Step에 런타임 핸들 없이 재현 가능한 정의를 남긴다."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        describe = ConfigurationView.resolver(engine)
        configuration = (describe(context.project.config, profile.engine, session_config=context.session.config)
                         if describe else ConfigurationView.unavailable().to_dict())
        from llm.core.configuration import UNSET
        workflow = resources = execution = UNSET
        if agent_mode(engine) == AgentMode.WORKFLOW:
            workflow = engine.workflow
        else:
            resources = self._resources(profile, context).resources
        bind_execution = EngineHooks.execution_binder(engine)
        if callable(bind_execution):
            execution = bind_execution(context)
        return AgentExecutionBinding.create(AgentNodeDefinition(definition).agent_id, profile,
            self.revision, configuration, workflow=workflow, resources=resources, execution=execution).to_dict()

    def validate(self, definition, context):
        """Validate the selected Agent before its approval or external work."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW:
            AgentNodeDefinition(definition).validate_workflow()
            return
        self._resources(profile, context)
        resources = profile.resources
        if (resources.skills or resources.prompt) and not callable(EngineHooks.instruction_binder(engine)):
            raise ValueError(self.UNSUPPORTED_INSTRUCTIONS)
        AgentNodeDefinition(definition).validate_behavior(profile)

    def validate_selection(self, definition, context, *, owner, records, checkpoint_name, wrapped_graph=INLINE_WORKFLOW):
        """Validate selected descendants through their Engine, including completed containers."""
        context = self.prepare_context(definition, context)
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW and not wrapped_graph:
            return  # Inline Graph children are visited in the parent Graph environment.
        name = EngineHooks.checkpoint_name(engine)
        if agent_mode(engine) != AgentMode.WORKFLOW:
            self._checkpoint_usage(records, owner, name)
        validate = EngineHooks.selection_validator(engine)
        if name is EngineHooks.ABSENT or not callable(validate):
            return
        if agent_mode(engine) != AgentMode.WORKFLOW:
            tools, _ = self._resources(profile, context)
            context = replace(context, tools=tools)
        bridge = EngineCheckpointScope(owner, name, records, parent_name=checkpoint_name)
        if bridge.snapshot() is not EngineCheckpointScope.NO_SNAPSHOT:
            validate(bridge.context(context))

    @classmethod
    def _checkpoint_usage(cls, records, owner, name):
        """선택한 Agent의 실행 기록만 해석하고 필수 사용량을 검증한다."""
        return AgentCheckpoint.selected_usage(records, owner, name)

    def prepare_context(self, definition, context):
        """Request only the selected Agent's public capabilities, before approval/effects."""
        context = context.with_capabilities((AgentDefinition.CAPABILITY,))
        engine = self._engine(self._profile(definition, context.capabilities))
        context = context.with_capabilities(self.additional_capabilities(definition, context.capabilities))
        discover = EngineHooks.discoverer(engine)
        if callable(discover):
            from itertools import count
            for expansion in count():
                names = discover(context.capabilities)
                if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
                    raise ValueError(self.INVALID_CAPABILITIES)
                if all(name in context.capabilities for name in names):
                    break
                context = context.with_capabilities(names, expansion=expansion)
        return context

    async def _graph_call(self, node, engine):
        """다른 Graph 환경으로 위임한다. behavior/resource/Agent 예산을 만들지 않는다."""
        if node.invoke_graph is GraphNodeContext.NO_GRAPH_INVOCATION:
            raise ValueError(self.MISSING_GRAPH_SCOPE)
        self.validate(node.definition, node.context)
        binding = AgentExecutionBinding(self.binding(node.definition, node.context))
        inputs = node.inputs if node.inputs is not GraphNodeContext.NO_INPUTS else node.state
        step_id = new_id()
        context = replace(node.context, state=binding.state(),
                          output_step_id=step_id, output_visibility=OutputVisibility.INTERNAL)
        if context.steering is not EngineContext.NO_STEERING:
            context = replace(context, steering=context.steering.child(target_id=step_id,
                scope=node.checkpoint_key, engine=binding.engine, mode=steering_mode(engine),
                node_id=node.node_id, node_path=node.node_path))
        await node.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind=self.STEP_KIND,
            name=binding.agent_id, metadata=AgentStepMetadata.started(binding, inputs)))
        async def forward(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(event, metadata=AgentStepMetadata.graph_child(event.metadata, step_id))
            await node.emit(event)
        try:
            async with aclosing(BaseEngine.open_instructions(context)) as events:
                async for event in events:
                    await forward(event)
            output = await node.invoke_graph(engine, context, inputs, forward)
            async with aclosing(BaseEngine.close_instructions(context)) as events:
                async for event in events:
                    await forward(event)
        except (asyncio.CancelledError, _GraphPause):
            raise
        except Exception as error:
            await node.emit(BaseEngine.step_failed_event(step_id, error))
            raise
        await node.emit(BaseEngine.step_completed_event(context, step_id,
            EngineOutput(data=output, visibility=OutputVisibility.INTERNAL)))
        return AgentOutputValue.workflow(output).to_dict()

    async def __call__(self, node):
        """업무의 입력/출력을 검증하고 연결 세션과 실행 스코프를 호출별로 정리한다."""
        parent_scope = ToolExecutionScope.require(node.context)
        profile = self._profile(node.definition, node.context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW:
            return await self._graph_call(node, engine)
        tools, resources = self._resources(profile, node.context)
        inputs = node.inputs if node.inputs is not GraphNodeContext.NO_INPUTS else node.state
        profile.validate_input(inputs)
        binding = AgentExecutionBinding(self.binding(node.definition, node.context))
        step_id = new_id()
        await node.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind=self.STEP_KIND,
                                   name=AgentNodeDefinition(node.definition).agent_id,
                                   metadata=AgentStepMetadata.started(binding, inputs)))
        try:
            policy = profile.policy
            async with timeout(policy.timeout_seconds), AsyncExitStack() as stack:
                await self.connect_resources(resources, node.context, tools, stack)
                engine = self.compose_instructions(engine, resources, node.context)
                scope = parent_scope.child(
                    allowed_tools=tools.names(), max_calls=policy.max_tool_calls)
                if node.context.checkpoint:
                    # 완료된 자식 노드를 재사용해도 Agent의 호출 예산/필수 Tool 이력은 보존한다.
                    scope.restore(self._checkpoint_usage(CheckpointRecords.from_checkpoint(node.context.checkpoint).records,
                                                         node.checkpoint_key, EngineHooks.checkpoint_name(engine)))
                await node.record_usage(scope.checkpoint())
                message = next(item for item in node.context.messages if item.id == node.context.run.input_message_id)
                message = replace(message, content=json.dumps(inputs, ensure_ascii=self.INPUT_ENSURE_ASCII, allow_nan=self.INPUT_ALLOW_NAN))
                # 부모 Run과 예산은 유지하며 기록/임시 상태와 선택 capability만 분리한다.
                capabilities = {
                    name: node.context.capabilities[name] for name in required_capabilities(engine) if name != ToolRegistry.CAPABILITY}
                if ToolRegistry.CAPABILITY in required_capabilities(engine):
                    capabilities[ToolRegistry.CAPABILITY] = tools
                discover = EngineHooks.discoverer(engine)
                if callable(discover):
                    # 부모가 이미 승인/해석한 capability만 전달한다. 새 resource를 만들지 않는다.
                    for name in discover({**node.context.capabilities, ToolRegistry.CAPABILITY: tools}):
                        if name in node.context.capabilities and name != ToolRegistry.CAPABILITY:
                            capabilities[name] = node.context.capabilities[name]
                visibility = (OutputVisibility.USER if AgentNodeDefinition(node.definition).emit_text
                              and node.context.output_visibility == OutputVisibility.USER else OutputVisibility.INTERNAL)
                context = replace(node.context, messages=(message,), tools=tools,
                                  state=binding.state(), capabilities=capabilities,
                                  checkpoint=EngineContext.NO_CHECKPOINT, tool_scope=scope,
                                  output_step_id=step_id, output_visibility=visibility)
                if context.steering is not EngineContext.NO_STEERING:
                    context = replace(context, steering=context.steering.child(target_id=step_id,
                        scope=node.checkpoint_key, engine=profile.engine, mode=steering_mode(engine),
                        node_id=node.node_id, node_path=node.node_path))
                bridge = self.NO_BRIDGE
                if self.checkpoint_engine(node.definition, node.context) is not self.NO_ENGINE:
                    records = deepcopy(CheckpointRecords.from_checkpoint(node.context.checkpoint).records) if node.context.checkpoint else {}
                    bridge = EngineCheckpointScope(node.checkpoint_key, engine.checkpoint_name, records,
                                                   parent_name=EngineCheckpointScope.parent_name_for(node))
                    context = bridge.context(context)
                if steering_mode(engine) == SteeringMode.CONSUME and bridge is self.NO_BRIDGE:
                    raise ValueError(self.MISSING_STEERING_CHECKPOINT)
                engine_output = EngineEvent.NO_OUTPUT
                async def forward(event):
                    nonlocal engine_output
                    if event.type == EngineEventType.STEERING and bridge is not self.NO_BRIDGE:
                        event = bridge.wrap_instruction(event)
                    if event.type == EngineEventType.CHECKPOINT or event.has_checkpoint:
                        if bridge is not self.NO_BRIDGE:
                            event = bridge.wrap(event)
                        else:
                            raise ValueError(self.WRONG_CHECKPOINT_OWNER)
                        receipt = event.checkpoint_receipt
                        write = CheckpointWrite(receipt.metadata)
                        value = AgentCheckpoint.with_usage(write.record.value, node.checkpoint_key, scope.checkpoint())
                        receipt = replace(receipt, metadata=write.with_record(value).to_dict())
                        event = replace(event, checkpoint=receipt) if event.has_checkpoint else receipt
                    if event.type == EngineEventType.PAUSED:
                        if bridge is self.NO_BRIDGE:
                            raise ValueError(self.MISSING_PAUSE_SCOPE)
                        raise _GraphPause(node.checkpoint_key)
                    if event.type == EngineEventType.STEP_STARTED:
                        event = replace(event, metadata=AgentStepMetadata.behavior_child(
                            event.metadata, binding, node.node_id, step_id))
                    # 자식 엔진 결과를 Agent 계약 검증 전에 확정하지 않는다. 내부 델타도
                    # 관찰 가능하되 visibility로 대화/UI의 사용자 출력과 구분한다.
                    if event.type == EngineEventType.OUTPUT:
                        if event.output is EngineEvent.NO_OUTPUT or event.output.step_id != step_id:
                            raise ValueError(self.WRONG_OUTPUT_STEP)
                        engine_output = event.output
                        return
                    await node.emit(event)
                    if bridge is not self.NO_BRIDGE and event.type == EngineEventType.STEERING:
                        bridge.accepted_instruction(event, context.steering)
                    if bridge is not self.NO_BRIDGE and (event.type == EngineEventType.CHECKPOINT or event.has_checkpoint):
                        bridge.accepted(event)
                    if event.type in (EngineEventType.STEP_UPDATED, EngineEventType.STEP_FAILED,
                                                       EngineEventType.STEP_COMPLETED, EngineEventType.STEP_CANCELLED):
                        # 승인 이후 효과 이전에도 예산을 기록한다. 불확실한 작업의 명시적 재시도가
                        # Agent 한도를 초기화하지 않도록 소유 컨테이너 체크포인트를 갱신한다.
                        await node.record_usage(scope.checkpoint())
                async with aclosing(BaseEngine.open_instructions(context)) as events:
                    async for event in events:
                        await forward(event)
                # 미지원 합성 엔진 내부의 Loop가 부모 채널을 자동 소비해서는 안 된다.
                execution_context = replace(context, steering=EngineContext.NO_STEERING) if steering_mode(engine) == SteeringMode.UNSUPPORTED else context
                async with aclosing(engine.execute(execution_context)) as events:
                    async for event in events:
                        await forward(event)
                async with aclosing(BaseEngine.close_instructions(context)) as events:
                    async for event in events:
                        await forward(event)
                policy.validate_completion(scope.completed)
                if engine_output is EngineEvent.NO_OUTPUT:
                    raise ValueError(self.MISSING_OUTPUT)
                if engine_output is not EngineEvent.NO_OUTPUT and engine_output.visibility == OutputVisibility.INTERNAL:
                    visibility = OutputVisibility.INTERNAL
                result = AgentOutputValue.from_output(engine_output, AgentNodeDefinition(node.definition).output_format(profile))
                Component.serialize(result.to_dict())
                profile.validate_output(result.to_dict())
        except (asyncio.CancelledError, _GraphPause):
            raise
        except Exception as error:
            await node.emit(BaseEngine.step_failed_event(step_id, error))
            raise
        await node.emit(BaseEngine.step_completed_event(context, step_id,
            EngineOutput(text=result.text, data=result.engine_data, visibility=visibility),
            metadata=AgentStepMetadata.completed_usage(scope.calls, scope.completed)))
        return result.to_dict()


class AgentInvocation:
    """One admitted root Agent; scheduling and persisted effects stay in RunManager."""

    NO_RESOURCES = None
    NO_BINDING = None
    NO_CONFIGURATION = None
    NO_RESUME_CONTEXT = None
    CLOSE_METHOD = "aclose"
    NO_CLOSE = None
    NOT_PREPARED = "Agent invocation requires a prepared execution binding"

    def __init__(self, binding, engine, engines):
        self.binding = AgentBinding.read(binding.to_dict())
        self.profile, self.engine = AgentProfile(self.binding.agent_id, self.binding.definition), engine
        self.selection = AgentExecution(engines=engines)
        self.mode = agent_mode(engine)
        self.resources = self.NO_RESOURCES
        self.runtime_binding = self.NO_BINDING

    def __getattr__(self, name):
        return getattr(self.engine, name)

    @property
    def required_capabilities(self):
        resources = self.profile.resources
        return tuple(dict.fromkeys((*required_capabilities(self.engine),
            *AgentResourceResolver.capabilities(self.profile),
            *((ToolRegistry.CAPABILITY,) if self.profile.tools or resources.mcp or resources.rag else ()))))

    def additional_capabilities(self, capabilities):
        discover = EngineHooks.discoverer(self.engine)
        return discover(capabilities) if callable(discover) else ()

    def prepare_context(self, context):
        ToolExecutionScope.require(context)
        if self.mode == AgentMode.BEHAVIORAL:
            tools, self.resources = self.selection._resources(self.profile, context)
            aliases = self.resources.aliases
            policy = self.profile.policy
            scope = context.tool_scope.child(allowed_tools=(*tools.names(), *aliases),
                                             max_calls=policy.max_tool_calls)
            if context.checkpoint is not EngineContext.NO_CHECKPOINT:
                scope.restore_checkpoint(CheckpointRecords.from_checkpoint(context.checkpoint).records)
            self.engine = self.selection.compose_instructions(self.engine, self.resources, context)
            capabilities = dict(context.capabilities)
            if ToolRegistry.CAPABILITY in capabilities:
                capabilities[ToolRegistry.CAPABILITY] = tools
            context = replace(context, tools=tools, capabilities=capabilities, tool_scope=scope)
            if self.profile.has_input_schema:
                message = next(item for item in context.messages if item.id == context.run.input_message_id)
                self.profile.validate_input(message.content)
        describe = ConfigurationView.resolver(self.engine)
        configuration = (revision_token(describe(context.project.config, self.binding.engine,
                         session_config=context.session.config)) if callable(describe) else self.NO_CONFIGURATION)
        execution = EngineHooks.execution_binder(self.engine)
        self.runtime_binding = AgentInvocationBinding(
            AgentInvocationIdentity(self.binding.agent_id, self.binding.revision, self.binding.engine,
                self.mode.value, configuration, self.resources.to_dict() if self.resources is not self.NO_RESOURCES else self.NO_RESOURCES),
            context.tools.execution_binding(context.tool_scope.binding()),
            AgentExecutionRevision(execution(context)).token if callable(execution) else AgentInvocationBinding.NO_EXECUTION,
        ).to_dict()
        # Root identity is already in Run admission evidence. The Engine owns
        # its private state; binding must not seed or persist application state.
        return context

    def validate_resume(self, checkpoint, *, retry_nodes=(), context=NO_RESUME_CONTEXT, **kwargs):
        validate = EngineHooks.resume_validator(self.engine)
        if not callable(validate):
            raise ValueError(EngineHooks.UNSUPPORTED_RESUME)
        # Connected remote definitions are checked after connection and before execution.
        if self.profile.resources.mcp:
            context = self.NO_RESUME_CONTEXT
        if context is self.NO_RESUME_CONTEXT:
            return validate(checkpoint, retry_nodes=retry_nodes, **kwargs)
        return validate(checkpoint, retry_nodes=retry_nodes, context=context, **kwargs)

    async def execute(self, context):
        if self.runtime_binding is self.NO_BINDING:
            raise RuntimeError(self.NOT_PREPARED)
        policy = self.profile.policy
        async with timeout(policy.timeout_seconds), AsyncExitStack() as stack:
            if self.resources is not self.NO_RESOURCES:
                await self.selection.connect_resources(self.resources, context, context.tools, stack)
            if self.profile.resources.mcp and context.checkpoint is not EngineContext.NO_CHECKPOINT:
                descriptor = ResumeRecord.require(context.run.metadata)
                self.engine.validate_resume(context.checkpoint, context=context,
                    retry_nodes=descriptor.retry_nodes, decisions=descriptor.decisions)
            stream = self.engine.execute(context)
            async with aclosing(stream) if callable(getattr(stream, self.CLOSE_METHOD, self.NO_CLOSE)) else nullcontext(stream):
                async for event in stream:
                    if event.type == EngineEventType.OUTPUT and event.output is not EngineEvent.NO_OUTPUT:
                        policy.validate_completion(context.tool_scope.completed)
                        value = AgentOutputValue.from_output(event.output, self.profile.output_format)
                        output = value.apply_to(event.output)
                        if output is not event.output:
                            event = replace(event, output=output)
                        self.profile.validate_output(value.to_dict())
                    yield event


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentDelegationContext(GraphNodeContext):
    checkpoint_name: str


class AgentDelegation:
    """ToolExecutor용 저장 Agent 어댑터. 실행 알고리즘은 AgentNode와 공유한다.

    큐는 저장 ACK를 연결하는 backpressure일 뿐 실행/권한의 별도 소유자가 아니다.
    등록된 Host 핸들러에만 이 이벤트 계약을 제공하며 Project Python source는 받지 않는다.
    """

    required_capabilities = (AgentDefinition.CAPABILITY,)
    resumable_container = True
    MAX_UNACKNOWLEDGED = 1
    INPUT_ENSURE_ASCII = False
    COLLECT_CLEANUP_ERRORS = True
    ACKNOWLEDGED = None
    REQUIRED_EVENT_CONTRACT = "agent_run requires the Engine ToolExecutor event contract"
    REQUIRED_PARENT = "Agent delegation requires a durable parent Engine checkpoint"
    CHANGED_SELECTION = "Delegated Agent configuration changed; start a new request"
    NO_CONTEXT = None
    NO_GRAPH_OUTPUT = None
    NO_TASK = None

    def __init__(self, runtime):
        self.runtime = runtime
        self.execution_binding = AgentHandlerBinding(runtime.revision).to_dict()

    def bind_arguments(self, arguments, context):
        """Selected definition, not the discoverable Agent catalogue, owns resume identity."""
        definition = AgentDelegationArguments(arguments).definition
        context = self.runtime.prepare_context(definition, context)
        self.runtime.validate(definition, context)
        return self.runtime.binding(definition, context)

    def validate_selection(self, arguments, context, *, request_key, checkpoint_name):
        selected = AgentDelegationArguments(arguments)
        owner = AgentCheckpoint.delegation_key(request_key, selected.agent_id)
        self.runtime.validate_selection(selected.definition, context, owner=owner,
            records=CheckpointRecords.records_or_empty(context.checkpoint), checkpoint_name=checkpoint_name,
            wrapped_graph=AgentExecution.WRAPPED_WORKFLOW)

    def additional_capabilities(self, capabilities):
        return (AgentDefinition.CAPABILITY,)

    async def __call__(self, arguments):
        raise ValueError(self.REQUIRED_EVENT_CONTRACT)

    async def execute_events(self, context, arguments, *, request_key, checkpoint_name, step_id, result):
        if (context is self.NO_CONTEXT or not isinstance(request_key, str) or not request_key.strip()
                or not isinstance(checkpoint_name, str) or not checkpoint_name.strip()):
            raise ValueError(self.REQUIRED_PARENT)
        # 동적 Tool 위임은 부모 Workflow의 사전 예약 경로가 아니다. 부모 Loop의 추가
        # 지시는 다음 부모 completion 경계에서 소비한다. 취소/예산/승인은 계속 공유한다.
        context = replace(context, steering=EngineContext.NO_STEERING)
        selected = AgentDelegationArguments(arguments)
        owner = AgentCheckpoint.delegation_key(request_key, selected.agent_id)
        records = deepcopy(CheckpointRecords.records_or_empty(context.checkpoint))
        definition = selected.definition
        context = self.runtime.prepare_context(definition, context)
        self.runtime.validate(definition, context)
        binding = self.runtime.binding(definition, context)
        marker = records.get(owner)
        if marker is not AgentCheckpoint.ABSENT and AgentCheckpoint.selected_binding(marker) != binding:
            raise ValueError(self.CHANGED_SELECTION)
        queue = asyncio.Queue(maxsize=self.MAX_UNACKNOWLEDGED)

        async def emit(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(event, metadata=AgentStepMetadata.delegated(event.metadata, step_id))
            ack = asyncio.get_running_loop().create_future()
            await queue.put((event, ack))
            await ack

        async def record_usage(usage):
            value = AgentCheckpoint.delegation(owner, binding, usage)
            await emit(EngineEvent(EngineEventType.CHECKPOINT, metadata=CheckpointWrite.record_event(checkpoint_name, owner, value).to_dict()))

        async def invoke_graph(engine, child, inputs, forward):
            bridge = EngineCheckpointScope(owner, engine.checkpoint_name, records, parent_name=checkpoint_name)
            child = bridge.context(child)
            message = next(m for m in child.messages if m.id == child.run.input_message_id)
            child = replace(child, messages=(replace(message, content=json.dumps(inputs, ensure_ascii=self.INPUT_ENSURE_ASCII)),))
            output = self.NO_GRAPH_OUTPUT
            async with aclosing(engine.execute(child)) as events:
                async for event in events:
                    if event.type == EngineEventType.OUTPUT:
                        output = event.output.data
                        continue
                    if event.type == EngineEventType.PAUSED:
                        raise _GraphPause(owner)
                    if event.type == EngineEventType.CHECKPOINT or event.has_checkpoint:
                        event = bridge.wrap(event)
                    elif event.type == EngineEventType.STEERING:
                        event = bridge.wrap_instruction(event)
                    await forward(event)
                    if event.type == EngineEventType.CHECKPOINT or event.has_checkpoint:
                        bridge.accepted(event)
                    elif event.type == EngineEventType.STEERING:
                        bridge.accepted_instruction(event, child.steering)
            return output

        async def produce():
            usage = ToolExecutionScope.initial_usage() if marker is AgentCheckpoint.ABSENT else AgentCheckpoint.required_usage(marker, owner)
            await record_usage(usage)
            node = AgentDelegationContext(context=context, definition=definition, inputs=selected.input, state={},
                node_id=selected.agent_id, node_path=owner, checkpoint_key=owner,
                checkpoint_name=checkpoint_name, emit=emit, record_usage=record_usage, invoke_graph=invoke_graph)
            try:
                ToolEventResult(result).yielded(await self.runtime(node))
            except _GraphPause:
                await emit(EngineEvent(EngineEventType.PAUSED, metadata=CheckpointPause(checkpoint_name).to_dict()))

        producer = asyncio.create_task(produce())
        reader = self.NO_TASK
        try:
            while not producer.done() or not queue.empty():
                reader = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((reader, producer), return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    event, ack = reader.result()
                    reader = self.NO_TASK
                    yield event
                    if not ack.done():
                        ack.set_result(self.ACKNOWLEDGED)
                else:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
                    reader = self.NO_TASK
            await producer
        finally:
            if reader is not self.NO_TASK:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
