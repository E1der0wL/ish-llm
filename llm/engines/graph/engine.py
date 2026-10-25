"""Workflow의 LangGraph 컴파일·실행과 Run/Step 이벤트 연결을 한곳에서 관리한다.

공개 GraphEngine과 Run별 내부 실행 객체를 분리하며 LangGraph는 실행 시 지연 로딩한다.
상태 채널에는 JSON만 전달하고 런타임 객체를 저장 형식에 노출하지 않는다.
"""

from llm.core.schema import SchemaExtension, SchemaKeyword, SchemaBoolean

from llm.core.resource_references import ResourceTarget

from llm.core.configuration_views import ConfigurationView, EngineConfigurationKey
from llm.core.definition_snapshot import DefinitionSnapshot

from llm.core.checkpoint_records import CheckpointWrite, CheckpointPause

from llm.core.checkpoint_records import CheckpointContent, CheckpointRecords

from llm.core.checkpoints import CheckpointConversation

from llm.components.workflows.graph import ControlNodeType, ConditionOperator, LoopLimit

from .checkpoints import GRAPH_CHECKPOINT_FORMAT, GRAPH_CHECKPOINT_NAME
from .selections import GraphHandlerSelection
from .configuration import GraphRegistration, GraphParameters, GraphAgentOptions
from .errors import GraphExecutionError, GraphDiagnostic
from .handler_contracts import GraphHandlerHooks
from .runtime_values import GraphScope, PreparedGraph, GraphPreparation, GraphExecutionObservation
from .node_records import (
    GraphNodeRecord,
    GraphDecision,
    GraphCheckpointHeader,
    GraphStepMetadata,
    GraphInput,
    GraphConfirmation,
)
from .execution_binding import GraphExecutionBinding, GraphEnvironment
from .frames import GraphFrame, LangGraphInvocation, _Frame, _LoopFrame, _ParallelFrame, _merge_branches
from .decisions import matches, _branch_port, _loop_continues, _plan_node, _NodePlan, GraphNodeAction
from llm.components.workflows.contracts import WorkflowDocument, WorkflowNode, WorkflowEdge
from llm.engines.agent_checkpoints import AgentCheckpoint

from llm.core.continuations import ResumeRecord

from llm.core.results import OutputVisibility

import asyncio
import inspect
import importlib
import json
from copy import copy, deepcopy
from llm.core.configuration import resolve_engine_config
from llm.core.agents import AgentMode
from llm.core.parameters import ParameterLayout
from llm.core.models import ProjectConfig
from contextlib import AsyncExitStack, aclosing
from llm.core.interactions import InteractionOption, InteractionRequest, approval_request
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable, ClassVar, Mapping, Optional, TYPE_CHECKING

from asyncio import timeout
from llm.components.base import Component
from llm.components.workflows.graph import validate_graph, validate_handler_options
from llm.components.workflows.bindings import (
    bind,
    read_pointer,
    validate_value,
    WorkflowBindings,
    BindingDirection,
)
from llm.core.checkpoints import CheckpointEnvelope, CheckpointStatus
from llm.core.models import new_id
from llm.core.results import EngineOutput
from llm.core.steering import SteeringMode, SteeringRoute
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType, required_capabilities


if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class _GraphPause(Exception):
    """처리기 호출 전의 명시적 대기 경계. 일반 노드 실패와 구분한다."""


@dataclass(frozen=True, slots=True)
class GraphNodeContext:
    """state는 전체 상태 복사본, inputs는 명시적으로 선택된 입력이다.

    반환 dict를 검증하고 outputs 매핑으로 선택한 키만 실행 상태에 병합한다.
    inputs/outputs를 생략한 기존 처리기는 전체 상태와 반환 dict 병합 계약을 유지한다.
    """

    NO_INPUTS: ClassVar[None] = None
    NO_GRAPH_INVOCATION: ClassVar[None] = None

    context: EngineContext
    node_id: str
    definition: dict
    state: dict
    emit: Callable[[EngineEvent], Awaitable[None]]
    inputs: Optional[dict] = NO_INPUTS
    # 선언된 하위 그래프만 현재 Run/체크포인트 범위에서 실행하는 런타임 연결점.
    invoke_graph: Optional[Callable] = NO_GRAPH_INVOCATION
    checkpoint_key: Optional[str] = None
    record_usage: Optional[Callable] = None
    # 이 노드의 확인 응답이며 Tool 실행 허가는 아니다. Tool은 context.execute_tool을 사용한다.
    decision: Optional[bool] = None
    node_path: Optional[str] = None
    # Trusted handler declares a pre-effect waiting boundary; ordinary exceptions stay failures.
    pause: Optional[Callable] = None


def _propagate_cancellation(error: Exception) -> None:
    """LangGraph의 노드 자발적 취소 오류를 Engine 중단 계약으로 돌린다.

    오류 문자열을 비교하거나 일반 노드 오류를 취소로 바꾸지 않는다.
    """
    from langgraph.errors import NodeCancelledError

    if isinstance(error, NodeCancelledError):
        raise asyncio.CancelledError() from error


class _WorkflowRuntime:
    """LangGraph 실행 + 노드 결과 체크포인트. 완료 결과만 복원하고 부작용은 재생하지 않는다."""

    CONTEXT_NAME = "workflow_engine_context"
    INITIAL_VISITED = 0
    VISIT_INCREMENT = 1
    ACCEPTING = True
    CLOSED = False
    EXPIRED_DEADLINE = 0
    ABSENT = None

    def __init__(
        self,
        engine: "GraphEngine",
        context: EngineContext,
        emit: Callable[[EngineEvent], Awaitable[None]],
        *,
        parent=GraphPreparation.ABSENT,
        routes=(),
    ) -> None:
        self.engine = engine
        self.parents = (*parent.parents, parent) if parent is not _WorkflowRuntime.ABSENT else ()
        self.handlers = dict(engine.handlers)
        self.emit = emit
        self._context = ContextVar(self.CONTEXT_NAME, default=context)
        self.max_steps = engine.max_steps
        self.visited = self.INITIAL_VISITED
        self.semaphore = (
            _WorkflowRuntime.ABSENT
            if engine.max_parallelism is _WorkflowRuntime.ABSENT
            else asyncio.Semaphore(engine.max_parallelism)
        )
        # 제어용 gate도 super-step을 사용한다. 실제 예산은 모든 하위 그래프가 공유한다.
        self.invoke_options = LangGraphInvocation.options(engine)
        self.records = (
            parent.records
            if parent is not _WorkflowRuntime.ABSENT
            else (
                deepcopy(CheckpointRecords.from_checkpoint(context.checkpoint).records)
                if context.checkpoint
                else {}
            )
        )
        self.resuming = context.checkpoint is not _WorkflowRuntime.ABSENT
        self.routes = parent.routes if parent is not _WorkflowRuntime.ABSENT else {r.key: r for r in routes}
        self.root_workflow = (
            parent.root_workflow if parent is not _WorkflowRuntime.ABSENT else engine.workflow
        )

    async def _record(self, key, value, *, completed=GraphPreparation.ABSENT):
        interaction = (
            GraphNodeRecord(value).interaction
            if GraphNodeRecord(value).has_interaction
            else _WorkflowRuntime.ABSENT
        )
        event = EngineEvent(
            EngineEventType.CHECKPOINT,
            interaction=interaction,
            metadata=CheckpointWrite.record_event(GRAPH_CHECKPOINT_NAME, key, value).to_dict(),
        )
        await self.emit(
            replace(completed, checkpoint=event) if completed is not _WorkflowRuntime.ABSENT else event
        )
        self.records[key] = deepcopy(value)

    async def _nested(self, engine, context, inputs, scope, path, parent_step_id, emit):
        """루트 저장소/예산을 공유하되 상태와 입출력 계약은 호출마다 분리한다."""
        engine = engine.with_config(context)
        graph, _ = engine._prepare(context)
        state = deepcopy(WorkflowDocument(graph).initial_state)
        state.update(
            bind(WorkflowBindings(graph).inputs, inputs)
            if WorkflowBindings(graph).has_inputs
            else deepcopy(inputs)
        )
        validate_value(graph, BindingDirection.INPUT, state)
        step_id = new_id()

        async def child_emit(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(
                    event,
                    metadata=GraphStepMetadata.nested_child(
                        extension=event.metadata,
                        parent_step_id=step_id,
                        workflow_id=engine.workflow,
                    ),
                )
            await emit(event)

        await emit(
            EngineEvent(
                EngineEventType.STEP_STARTED,
                step_id=step_id,
                kind=GraphExecutionObservation.NESTED_STEP,
                name=engine.workflow,
                metadata=GraphStepMetadata.nested(
                    parent_step_id=parent_step_id, workflow_id=engine.workflow, path=path, inputs=inputs
                ),
            )
        )
        runtime = _WorkflowRuntime(engine, replace(context, state={}), child_emit, parent=self)
        try:
            async with timeout(engine.timeout_seconds):
                state = await runtime.run(
                    graph, state, path, scope=scope + [GraphScope.WORKFLOW, engine.workflow]
                )
                output = (
                    bind(WorkflowBindings(graph).outputs, state)
                    if WorkflowBindings(graph).has_outputs
                    else deepcopy(state)
                )
                validate_value(graph, BindingDirection.OUTPUT, output)
        except (asyncio.CancelledError, _GraphPause):
            raise
        except Exception as error:
            await emit(BaseEngine.step_failed_event(step_id, error))
            raise
        await emit(
            BaseEngine.step_completed_event(
                context, step_id, EngineOutput(data=output, visibility=OutputVisibility.INTERNAL)
            )
        )
        return output

    def _compile(
        self,
        document: dict,
        entry: Optional[str] = GraphPreparation.ABSENT,
        stop: Optional[str] = GraphPreparation.ABSENT,
    ) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        entry = WorkflowDocument(document).entry if entry is _WorkflowRuntime.ABSENT else entry
        outgoing = {name: [] for name in WorkflowDocument(document).nodes}
        for edge in WorkflowDocument(document).edges:
            outgoing[WorkflowEdge(edge).source].append(edge)
        # 병렬 영역은 독립 하위 그래프로 컴파일한다. 부모는 fork 다음 join으로 연결한다.
        region, pending = ({}, [entry])
        while pending:
            name = pending.pop()
            if name == stop or name in region:
                continue
            node = WorkflowDocument(document).nodes[name]
            region[name] = node
            pending.extend(
                [WorkflowNode(node).join]
                if WorkflowNode(node).kind == ControlNodeType.PARALLEL
                else [WorkflowEdge(edge).target for edge in outgoing[name]]
            )
        identifiers = {name: LangGraphInvocation.node_identifier(index) for index, name in enumerate(region)}

        # 사용자 ID가 __start__/__end__ 또는 콜론을 포함해도 내부 예약 이름과 충돌하지 않는다.
        def target(name):
            return END if name == stop else identifiers[name]

        builder = StateGraph(_Frame)
        for name, node in region.items():
            child = _WorkflowRuntime.ABSENT
            if WorkflowNode(node).kind == ControlNodeType.PARALLEL:
                child = self._parallel(document, name, outgoing[name])
            elif WorkflowNode(node).kind == ControlNodeType.LOOP:
                child = self._loop(node)
            builder.add_node(
                identifiers[name], self._node(name, node, child), retry_policy=LangGraphInvocation.NO_RETRY
            )
            if WorkflowNode(node).kind == ControlNodeType.END:
                builder.add_edge(identifiers[name], END)
            elif WorkflowNode(node).kind == ControlNodeType.BRANCH:
                builder.add_conditional_edges(
                    identifiers[name],
                    self._port,
                    {WorkflowEdge(edge).port: target(WorkflowEdge(edge).target) for edge in outgoing[name]},
                )
            elif WorkflowNode(node).kind == ControlNodeType.PARALLEL:
                builder.add_edge(identifiers[name], target(WorkflowNode(node).join))
            else:
                builder.add_edge(identifiers[name], target(WorkflowEdge(outgoing[name][0]).target))
        builder.add_edge(START, identifiers[entry])
        return builder.compile(checkpointer=LangGraphInvocation.CHECKPOINTER)

    @staticmethod
    def _port(frame: _Frame) -> str:
        return GraphFrame(frame).port

    def _parallel(self, document: dict, name: str, links: list[dict]) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        node = WorkflowDocument(document).nodes[name]
        builder = StateGraph(_ParallelFrame)
        branch_ids = []
        for index, edge in enumerate(links):
            start = WorkflowEdge(edge).target
            child = self._compile(document, start, WorkflowNode(node).join)

            async def branch(frame, child=child, start=start):
                # 같은 컴파일 결과를 반복 실행해도 branch의 임시 상태는 회차마다 새로 만든다.
                token = self._context.set(replace(self._context.get(), state={}))
                try:
                    result = await child.ainvoke(
                        GraphFrame.create(
                            deepcopy(GraphFrame(frame).data),
                            GraphFrame(frame).path,
                            GraphFrame.EMPTY_PORT,
                            GraphFrame(frame).scope + [GraphScope.BRANCH, start],
                        ),
                        self.invoke_options,
                    )
                    return GraphFrame.branch_result(start, GraphFrame(result).data)
                finally:
                    self._context.reset(token)

            identifier = LangGraphInvocation.branch_identifier(index)
            branch_ids.append(identifier)
            builder.add_node(identifier, branch, retry_policy=LangGraphInvocation.NO_RETRY)
            builder.add_edge(START, identifier)
        # LangGraph가 병렬 스케줄링과 모든 경로의 완료 대기를 담당한다.
        builder.add_edge(branch_ids, END)
        return builder.compile(checkpointer=LangGraphInvocation.CHECKPOINTER)

    def _loop(self, node: dict) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        body = self._compile(WorkflowNode(node).body_definition)
        builder = StateGraph(_LoopFrame)

        def route(frame):
            return (
                LangGraphInvocation.LOOP_BODY
                if _loop_continues(node, GraphFrame(frame).data, GraphFrame(frame).count)
                else END
            )

        async def advance(frame):
            count = GraphFrame(frame).count + GraphFrame.COUNT_INCREMENT
            result = await body.ainvoke(
                GraphFrame.create(
                    GraphFrame(frame).data,
                    GraphScope.loop_path(GraphFrame(frame).path, count),
                    GraphFrame.EMPTY_PORT,
                    GraphFrame(frame).scope + [count],
                ),
                self.invoke_options,
            )
            return GraphFrame.loop_result(GraphFrame(result).data, count)

        # LangGraph의 조건부 후방 간선으로 반복하며 Python while 실행기는 두지 않는다.
        async def gate(frame):
            return {}

        builder.add_node(LangGraphInvocation.LOOP_GATE, gate, retry_policy=LangGraphInvocation.NO_RETRY)
        builder.add_node(LangGraphInvocation.LOOP_BODY, advance, retry_policy=LangGraphInvocation.NO_RETRY)
        builder.add_edge(START, LangGraphInvocation.LOOP_GATE)
        builder.add_conditional_edges(
            LangGraphInvocation.LOOP_GATE,
            route,
            {LangGraphInvocation.LOOP_BODY: LangGraphInvocation.LOOP_BODY, END: END},
        )
        builder.add_edge(LangGraphInvocation.LOOP_BODY, LangGraphInvocation.LOOP_GATE)
        return builder.compile(checkpointer=LangGraphInvocation.CHECKPOINTER)

    async def _call(
        self, handler: Callable[[GraphNodeContext], Awaitable[dict]], node: GraphNodeContext
    ) -> dict:
        """핸들러에 취소를 한 번 전달하고 설정한 시간까지만 정리를 기다린다.

        LangGraph의 중첩 실행기는 형제 실패/종료 과정에서 같은 노드를 여러 번 취소할
        수 있다. 별도 asyncio.Task를 shield하여 처리기의 finally 정리를 두 번째 취소로 끊지 않는다.
        그래프의 노드 선택/병렬 스케줄링은 계속 LangGraph가 담당한다.
        """
        from langgraph import errors as graph_errors

        accepting = self.ACCEPTING
        original_emit = node.emit

        async def guarded_emit(event):
            if not accepting:
                raise asyncio.CancelledError()
            await original_emit(event)

        def guard(callback):
            if callback is _WorkflowRuntime.ABSENT:
                return _WorkflowRuntime.ABSENT

            async def invoke(*args, **kwargs):
                if not accepting:
                    raise asyncio.CancelledError()
                return await callback(*args, **kwargs)

            return invoke

        pending = asyncio.ensure_future(
            handler(
                replace(
                    node,
                    emit=guarded_emit,
                    invoke_graph=guard(node.invoke_graph),
                    record_usage=guard(node.record_usage),
                    pause=guard(node.pause),
                )
            )
        )
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            accepting = self.CLOSED
            if node.context.tool_scope is not _WorkflowRuntime.ABSENT:
                node.context.tool_scope.revoke()
            if not pending.done():
                pending.cancel()
            # 미설정은 정리 대기를 추가하지 않는다. revoke/emit gate 뒤 미완료
            # 작업은 기존 PendingWork owner에 넘겨 shutdown/reap한다.
            deadline = (
                _WorkflowRuntime.ABSENT
                if self.engine.cleanup_timeout is _WorkflowRuntime.ABSENT
                else asyncio.get_running_loop().time() + self.engine.cleanup_timeout
            )
            while not pending.done():
                remaining = (
                    _WorkflowRuntime.ABSENT
                    if deadline is _WorkflowRuntime.ABSENT
                    else deadline - asyncio.get_running_loop().time()
                )
                if remaining is _WorkflowRuntime.ABSENT or remaining <= self.EXPIRED_DEADLINE:
                    if node.context.pending_work is not _WorkflowRuntime.ABSENT:
                        node.context.pending_work.track(pending)
                    else:
                        pending.add_done_callback(
                            lambda pending: (
                                pending.exception() if not pending.cancelled() else _WorkflowRuntime.ABSENT
                            )
                        )
                    break
                try:
                    await asyncio.wait((pending,), timeout=remaining)
                except asyncio.CancelledError:
                    continue
            if pending.done() and not pending.cancelled():
                pending.exception()  # 정리 오류를 회수하되 원래 중단 신호를 유지한다.
            raise
        except graph_errors.GraphInterrupt as error:
            # 네이티브 interrupt는 노드 경계 pause_before와 다른 계약이다. 성공으로 오인하지 않는다.
            raise GraphExecutionError(GraphDiagnostic.NATIVE_INTERRUPT) from error

    async def _execute_node(self, definition, state, child, nested, info, scope):
        """노드의 계산·입출력 연결만 수행한다. 승인/재개와 Step 수명은 _node가 감싼다."""
        kind, context = (WorkflowNode(definition).kind, self._context.get())
        path, step_id = (GraphNodeRecord(info).path, GraphNodeRecord(info).step_id)
        key = GraphScope.key(scope)
        detail, port = ({}, GraphFrame.EMPTY_PORT)
        if kind == ControlNodeType.BRANCH:
            port = _branch_port(definition, state)
            GraphStepMetadata.add_port(detail, port)
        elif kind == ControlNodeType.PARALLEL:
            result = await child.ainvoke(GraphFrame.parallel(state, path, {}, scope), self.invoke_options)
            GraphFrame.publish_branches(state, GraphFrame(result).branches)
        elif kind == ControlNodeType.LOOP:
            result = await child.ainvoke(
                GraphFrame.loop(state, path, GraphFrame.INITIAL_COUNT, scope), self.invoke_options
            )
            state = GraphFrame(result).data
            GraphStepMetadata.add_iterations(detail, GraphFrame(result).count)
        elif kind not in (ControlNodeType.END, ControlNodeType.JOIN):
            inputs = (
                bind(WorkflowBindings(definition).inputs, state)
                if WorkflowBindings(definition).has_inputs
                else deepcopy(state)
            )
            validate_value(definition, BindingDirection.INPUT, inputs)

            async def node_emit(event):
                if event.type == EngineEventType.OUTPUT and (
                    event.output is _WorkflowRuntime.ABSENT or event.output.step_id is _WorkflowRuntime.ABSENT
                ):
                    raise GraphExecutionError(GraphDiagnostic.UNOWNED_OUTPUT)
                if event.type == EngineEventType.STEP_STARTED:
                    event = replace(
                        event,
                        metadata=GraphStepMetadata.node_child(
                            parent_step_id=step_id,
                            node_checkpoint_key=key,
                            node_path=path,
                            extension=event.metadata,
                        ),
                    )
                await self.emit(event)
                receipt = event.checkpoint or event
                if receipt.type == EngineEventType.CHECKPOINT and CheckpointWrite(receipt.metadata).is_record:
                    entry = CheckpointWrite(receipt.metadata).record
                    self.records[entry.key] = entry.value

            async def invoke_graph(engine, child_context, child_inputs, emit):
                if nested is _WorkflowRuntime.ABSENT or engine.workflow != nested.workflow:
                    raise GraphExecutionError(GraphDiagnostic.NESTED_SELECTION)
                return await self._nested(
                    engine,
                    child_context,
                    child_inputs,
                    scope,
                    path + GraphScope.PATH_SEPARATOR + engine.workflow,
                    step_id,
                    emit,
                )

            async def record_usage(usage):
                record = AgentCheckpoint.with_usage(self.records[key], key, usage)
                await self._record(key, record)

            async def pause(request):
                if not isinstance(request, InteractionRequest):
                    raise TypeError(GraphDiagnostic.INVALID_PAUSE)
                if (
                    request.kind != InteractionRequest.KIND_APPROVAL
                    or request.input_schema is not _WorkflowRuntime.ABSENT
                    or any(
                        (
                            type(option.value) is not bool
                            or option.effect
                            not in (InteractionOption.EFFECT_APPROVE, InteractionOption.EFFECT_DENY)
                            for option in request.options
                        )
                    )
                ):
                    raise ValueError(GraphDiagnostic.PAUSE_CONTRACT)
                await self._record(
                    key,
                    GraphNodeRecord.waiting_approval(
                        extension=info,
                        status=CheckpointStatus.WAITING,
                        approval_required=GraphNodeRecord.APPROVAL_REQUIRED_VALUE,
                        prompt=request.title,
                        interaction=request.bind(
                            GRAPH_CHECKPOINT_NAME, key, decision_key=GraphDecision.APPROVED
                        ).to_dict(),
                    ),
                )
                raise _GraphPause(key)

            if kind == ControlNodeType.WORKFLOW:
                result = await invoke_graph(nested, context, inputs, node_emit)
            else:
                # 조율 노드는 슬롯을 잡지 않는다. 실제 작업만 조상 순서로 획득한다.
                async with AsyncExitStack() as slots:
                    if nested is _WorkflowRuntime.ABSENT:
                        for runtime in (*self.parents, self):
                            if runtime.semaphore is not _WorkflowRuntime.ABSENT:
                                await slots.enter_async_context(runtime.semaphore)
                    result = await self._call(
                        self.handlers[kind],
                        GraphNodeContext(
                            context,
                            GraphNodeRecord(info).node_id,
                            deepcopy(definition),
                            deepcopy(state),
                            node_emit,
                            deepcopy(inputs),
                            (
                                invoke_graph
                                if nested is not _WorkflowRuntime.ABSENT
                                else _WorkflowRuntime.ABSENT
                            ),
                            key,
                            record_usage,
                            GraphDecision(GraphNodeRecord(info).decision).optional_approved,
                            path,
                            pause,
                        ),
                    )
            Component.serialize(result)
            validate_value(definition, BindingDirection.OUTPUT, result)
            updates = (
                bind(WorkflowBindings(definition).outputs, result)
                if WorkflowBindings(definition).has_outputs
                else deepcopy(result)
            )
            GraphStepMetadata.add_result(detail, inputs, result)
            state.update(updates)
        return (state, detail, port)

    def _node(
        self, name: str, definition: dict, child: Optional["CompiledStateGraph"]
    ) -> Callable[[_Frame], Awaitable[dict]]:

        async def execute(frame):
            context = self._context.get()
            for runtime in (*self.parents, self):
                runtime.visited += self.VISIT_INCREMENT
                if runtime.max_steps is not _WorkflowRuntime.ABSENT and runtime.visited > runtime.max_steps:
                    raise GraphExecutionError(GraphDiagnostic.NODE_LIMIT)
            state = deepcopy(GraphFrame(frame).data)
            kind, step_id = (WorkflowNode(definition).kind, new_id())
            path = GraphScope.node_path(GraphFrame(frame).path, name)
            scope = GraphFrame(frame).scope + [name]
            # 표시용 path에는 / 등이 들어갈 수 있으므로 실행 식별자는 JSON 배열로 구분한다.
            key = GraphScope.key(scope)
            handler = self.handlers.get(kind)
            if GraphHandlerHooks.selection_bound(handler):
                prepare = GraphHandlerHooks.preparer(handler)
                if callable(prepare):
                    context = prepare(deepcopy(definition), context)
                self.engine._validate_handler(handler, definition, context)
                self._context.set(context)
            nested = self.engine._nested_engine(definition, context.capabilities)
            checkpoint_engine = GraphHandlerHooks.checkpoint_engine(self.handlers.get(kind))
            resumable = nested is not _WorkflowRuntime.ABSENT or (
                callable(checkpoint_engine)
                and checkpoint_engine(definition, context) is not _WorkflowRuntime.ABSENT
            )
            saved = self.records.get(key)
            info = GraphNodeRecord.create(
                path=path,
                node_id=name,
                node_type=kind,
                step_id=step_id,
                source_run_id=context.run.id,
                input_state=deepcopy(state),
                container=kind == ControlNodeType.WORKFLOW
                or (resumable and GraphHandlerHooks.resumable(self.handlers.get(kind))),
            )
            handler = self.handlers.get(kind)
            if GraphHandlerHooks.selection_bound(handler):
                selection = GraphHandlerSelection.create(
                    self.engine._environment(),
                    deepcopy(definition),
                    handler.binding(deepcopy(definition), context),
                )
                if saved is not _WorkflowRuntime.ABSENT and (not selection.matches(saved)):
                    raise GraphExecutionError(GraphDiagnostic.CHANGED_HANDLER)
                selection.attach(info)
            plan = _plan_node(
                definition,
                state,
                saved=saved,
                resuming=self.resuming,
                decisions=ResumeRecord.decisions_for(context.run.metadata),
                key=key,
            )
            if plan.action == GraphNodeAction.REUSE:
                await self.emit(
                    EngineEvent(
                        EngineEventType.STEP_STARTED,
                        step_id=step_id,
                        kind=GraphExecutionObservation.NODE_STEP,
                        name=name,
                        metadata=GraphStepMetadata.reused_node(
                            extension=info,
                            checkpoint_key=key,
                            reused_from_run=GraphNodeRecord(saved).source_run_id,
                            reused_step_id=GraphNodeRecord(saved).step_id,
                        ),
                    )
                )
                await self.emit(
                    BaseEngine.step_completed_event(
                        context,
                        step_id,
                        EngineOutput(
                            data=GraphNodeRecord(saved).output, visibility=OutputVisibility.INTERNAL
                        ),
                        metadata=GraphStepMetadata.reused_completion(reused=GraphStepMetadata.REUSED_VALUE),
                    )
                )
                return GraphFrame.create(
                    deepcopy(GraphNodeRecord(saved).output),
                    GraphFrame(frame).path,
                    GraphNodeRecord(saved).port,
                    GraphFrame(frame).scope,
                )
            if plan.action == GraphNodeAction.PAUSE:
                request = approval_request(
                    GraphConfirmation.TITLE,
                    source=GraphConfirmation.source(
                        project_id=context.project.id,
                        session_id=context.session.id,
                        run_id=context.run.id,
                        node_id=name,
                        path=path,
                    ),
                    action=GraphConfirmation.action(definition=deepcopy(definition)),
                    category=GraphConfirmation.CATEGORY,
                )
                request = replace(
                    request,
                    kind=InteractionRequest.KIND_CONFIRMATION,
                    input_schema=WorkflowBindings(definition).resume_schema,
                )
                request = request.bind(
                    GRAPH_CHECKPOINT_NAME,
                    key,
                    decision_key=GraphDecision.APPROVED,
                    input_key=GraphDecision.STATE,
                )
                await self._record(
                    key,
                    GraphNodeRecord.waiting_confirmation(
                        extension=info,
                        status=CheckpointStatus.WAITING,
                        resume_schema=WorkflowBindings(definition).resume_schema,
                        interaction=request.to_dict(),
                    ),
                )
                raise _GraphPause(key)
            decision = plan.decision
            state.update(deepcopy(GraphDecision(decision).state_or_empty))
            GraphNodeRecord.attach_decision(info, deepcopy(decision))
            # started를 먼저 fsync한다. 완료 저장 전 종료되면 재실행 승인이 필요한 노드다.
            # 예약 경계는 Agent/MCP/모델 실행보다 앞선다. 정수 반복 번호만 제거하고
            # 호출 위치와 병렬 분기 경로는 보존하므로 같은 이름의 다른 노드와 혼동하지 않는다.
            route_key = GraphScope.route_key(self.root_workflow, scope)
            if context.steering is not _WorkflowRuntime.ABSENT and route_key in self.routes:
                async with aclosing(
                    BaseEngine.bind_instruction_route(context, self.routes[route_key], key)
                ) as events:
                    async for event in events:
                        await self.emit(event)
            await self._record(
                key,
                GraphNodeRecord.started(
                    extension=info,
                    status=CheckpointStatus.STARTED,
                    requires_retry=kind not in ControlNodeType.structural()
                    and (not GraphNodeRecord(info).required_container),
                ),
            )
            await self.emit(
                EngineEvent(
                    EngineEventType.STEP_STARTED,
                    step_id=step_id,
                    kind=GraphExecutionObservation.NODE_STEP,
                    name=name,
                    metadata=GraphStepMetadata.node(
                        node_id=name, path=path, node_type=kind, definition=definition, checkpoint_key=key
                    ),
                )
            )
            try:
                async with timeout(WorkflowNode(definition).timeout_seconds):
                    state, detail, port = await self._execute_node(
                        definition, state, child, nested, info, scope
                    )
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except _GraphPause:
                raise
            except Exception as error:
                _propagate_cancellation(error)
                await self.emit(BaseEngine.step_failed_event(step_id, error))
                raise
            await self._record(
                key,
                GraphNodeRecord.completed(
                    extension=info, status=CheckpointStatus.COMPLETED, output=deepcopy(state), port=port
                ),
                completed=BaseEngine.step_completed_event(
                    context,
                    step_id,
                    EngineOutput(data=state, visibility=OutputVisibility.INTERNAL),
                    metadata=detail,
                ),
            )
            return GraphFrame.create(state, GraphFrame(frame).path, port, GraphFrame(frame).scope)

        return execute

    async def run(self, document: dict, state: dict, path: str, *, scope=GraphPreparation.ABSENT) -> dict:
        """컴파일한 LangGraph를 실행하고 JSON 상태만 반환한다."""
        graph = self._compile(document)
        try:
            result = await graph.ainvoke(
                GraphFrame.create(state, path, GraphFrame.EMPTY_PORT, scope or []), self.invoke_options
            )
        except Exception as error:
            _propagate_cancellation(error)
            raise
        return GraphFrame(result).data


class GraphEngine:
    """Workflow를 LangGraph로 실행한다. 처리기는 GraphNodeContext → dict 계약이다.

    처리기.required_capabilities로 기능을 선언하고 선택적인 validate(node, context)로
    실행 전 참조를 검사한다. 각 처리기는 파일/모델 작업을 수행할 수 있지만 Run/Step
    저장에는 emit을 사용한다. 검증 결과의 부적합은 상태로, 실행기 오류는 예외로 보고한다.
    AgentNode는 명시적으로 등록된 업무용 엔진에 실행을 위임한다.
    Workflow 노드 경계 체크포인트를 이벤트로 저장한다. resume은 완료 결과를 복원하고
    미완료 노드부터 실행하며, LangGraph 내부 프레임이나 Python 런타임 객체는 저장하지 않는다.
    """

    checkpoint_name = GRAPH_CHECKPOINT_NAME
    PAUSE_NODE_KEY = "node_key"
    steering_mode = SteeringMode.FORWARD
    agent_mode = AgentMode.WORKFLOW
    ABSENT = GraphPreparation.ABSENT
    DRAIN_EXCEPTIONS = True

    TIMEOUT_PARAMETER = GraphParameters.TIMEOUT_SECONDS
    DEFAULT_BUFFER_SIZE = GraphParameters.DEFAULT_BUFFER_SIZE
    parameter_layout = GraphParameters.LAYOUT
    _option_names = GraphParameters.NAMES
    REQUIRED_CONFIG_SECTIONS = (ProjectConfig.POLICIES, ProjectConfig.PARAMETERS)

    def __init__(
        self,
        *,
        handlers: Mapping[str, Callable],
        revision: str = GraphRegistration.DEFAULT_REVISION,
        config_keys: Optional[tuple[str, ...]] = GraphRegistration.ABSENT,
        parameter_key: Optional[str] = GraphRegistration.ABSENT,
    ) -> None:
        GraphRegistration.validate_parameter_key(parameter_key)
        self.parameter_key, self._agent_options, self._configured = (
            parameter_key,
            {},
            GraphParameters.UNCONFIGURED,
        )
        GraphRegistration.validate_config_keys(config_keys)
        self.config_keys = config_keys
        GraphRegistration.validate_revision(revision)
        self.revision = revision
        self.workflow, self.handlers = GraphRegistration.ABSENT, dict(handlers)
        controls = frozenset(ControlNodeType)
        GraphRegistration.validate_handlers(self.handlers, controls)
        self.required_capabilities = tuple(
            dict.fromkeys(
                (WorkflowDocument.COMPONENT,)
                + tuple(name for handler in self.handlers.values() for name in required_capabilities(handler))
            )
        )
        self._configure({})

    def _configure(self, values):
        GraphParameters.configure(self, values)

    @classmethod
    def _option_schema(cls):
        return GraphParameters.schema(cls._option_names, GraphEngine.TIMEOUT_PARAMETER)

    def describe_config(self):
        return GraphParameters.describe(self)

    def resolve_config(self, config, name, *, session_config=GraphPreparation.ABSENT):
        view = resolve_engine_config(
            config,
            self.parameter_key or name,
            session_config=session_config,
            agent=self._agent_options,
            schema=self.describe_config(),
        )
        return view

    def with_config(self, context):
        """등록 인스턴스를 변경하지 않는 실행별 설정 사본. 중첩 실행에도 동일하게 적용한다."""
        if self._configured:
            return self
        values = self.parameter_layout.unpack(
            ConfigurationView.read(
                self.resolve_config(
                    context.project.config, context.run.engine, session_config=context.session.config
                )
            ).values
        )
        worker = copy(self)
        worker._configure(values)
        worker._configured = GraphParameters.CONFIGURED
        worker.workflow = self.workflow
        return worker

    def _execution_config(self, context):
        return {
            key: value
            for key, value in context.project.config.to_dict().items()
            if self.config_keys is GraphPreparation.ABSENT
            or key in self.config_keys
            or key in GraphEngine.REQUIRED_CONFIG_SECTIONS
        }

    def _binding(self, context, graph, prepared):
        """코드의 버전은 개발자가 revision으로 관리한다. 저장 가능한 실행 설정은 직접 비교한다."""
        from llm.services.runtime.tools import ToolExecutionScope
        if not self._configured:
            return self.with_config(context)._binding(context, graph, prepared)
        bindings, nested_graphs = [], []
        for engine, document, child_context, selected in prepared:
            if selected:
                continue
            nested_graphs.append(
                GraphExecutionBinding.nested(
                    workflow_id=engine.workflow,
                    definition=document,
                    project_config=engine._execution_config(child_context),
                    revision=engine.revision,
                    handlers=sorted(engine.handlers),
                    max_steps=engine.max_steps,
                    max_parallelism=engine.max_parallelism,
                    max_nested_depth=engine.max_nested_depth,
                    timeout_seconds=engine.timeout_seconds,
                    cleanup_timeout=engine.cleanup_timeout,
                    config_keys=(
                        list(engine.config_keys)
                        if engine.config_keys is not GraphPreparation.ABSENT
                        else GraphPreparation.ABSENT
                    ),
                )
            )
            for node in self._nodes(document):
                bind_handler = GraphHandlerHooks.binder(engine.handlers.get(WorkflowNode(node).kind))
                if bind_handler is not GraphPreparation.ABSENT and not GraphHandlerHooks.selection_bound(
                    engine.handlers.get(WorkflowNode(node).kind)
                ):
                    bindings.append(bind_handler(deepcopy(node), child_context))
        Component.serialize(GraphExecutionBinding.handler_payload(bindings=bindings))
        return GraphExecutionBinding.create(
            workflow=graph,
            workflow_id=self.workflow,
            revision=self.revision,
            handler_bindings=bindings,
            nested_graphs=nested_graphs,
            handlers=sorted(self.handlers),
            max_steps=self.max_steps,
            max_parallelism=self.max_parallelism,
            timeout_seconds=self.timeout_seconds,
            project_config=self._execution_config(context),
            session_config=context.session.config,
            components=list(context.project.components),
            extension=context.tools.execution_binding(
                ToolExecutionScope.require(context).binding()
            ),
        )

    def _environment(self):
        return GraphEnvironment.create(
            workflow=self.workflow,
            revision=self.revision,
            parameter_key=self.parameter_key,
            options=deepcopy(self._agent_options),
            handlers=sorted(self.handlers),
        )

    def bind_execution(self, context):
        """Public orchestration binding; selected handlers own their private definitions."""
        graph, prepared = self._prepare(context)
        return self._binding(context, graph, prepared)

    def _validate_selections(self, context, prepared, records):
        if not records:
            return
        context.tools.validate_selections(
            replace(context, checkpoint=GraphNodeRecord.selection_checkpoint(records))
        )
        # Resolve actual selected environments, not hypothetical Agent configurations.
        # A parent selection makes its nested environment available for descendants.
        environments = [item for item in prepared if not item.selected]
        pending = GraphHandlerSelection.pending(records)
        while pending:
            advanced = False
            for key, selected in list(pending.items()):
                matches = [
                    item for item in environments if item.engine._environment() == selected.environment
                ]
                if not matches:
                    continue
                for engine, _, child_context, _ in matches:
                    definition = deepcopy(selected.definition)
                    handler = engine.handlers.get(WorkflowNode(definition).kind)
                    if not GraphHandlerHooks.selection_bound(handler):
                        raise ValueError(GraphDiagnostic.MISSING_HANDLER)
                    prepare = GraphHandlerHooks.preparer(handler)
                    if callable(prepare):
                        child_context = prepare(definition, child_context)
                    if handler.binding(definition, child_context) != selected.binding:
                        raise ValueError(GraphDiagnostic.CHANGED_HANDLER)
                    validate = GraphHandlerHooks.selection_validator(handler)
                    if callable(validate):
                        validate(
                            definition,
                            child_context,
                            owner=key,
                            records=records,
                            checkpoint_name=GRAPH_CHECKPOINT_NAME,
                        )
                    nested = engine._nested_engine(definition, child_context.capabilities)
                    if nested is not GraphPreparation.ABSENT:
                        _, children = nested._prepare(child_context)
                        for item in children:
                            if not item.selected and not any(
                                item.engine._environment() == e.engine._environment() for e in environments
                            ):
                                environments.append(item)
                pending.pop(key)
                advanced = True
            if not advanced:
                raise ValueError(GraphDiagnostic.CHANGED_ENVIRONMENT)

    def validate_selections(self, context):
        _, prepared = self._prepare(context)
        self._validate_selections(context, prepared, CheckpointRecords.records_or_empty(context.checkpoint))

    @classmethod
    def _deadline(cls, value) -> None:
        from llm.core.schema import validate_declared_fields

        validate_declared_fields({cls.TIMEOUT_PARAMETER: value}, cls._option_schema())

    @staticmethod
    def _nodes(graph):
        for node in WorkflowDocument(graph).nodes.values():
            yield node
            if WorkflowNode(node).kind == ControlNodeType.LOOP:
                yield from GraphEngine._nodes(WorkflowNode(node).body_definition)

    def _definition(self, capabilities):
        if self.workflow is GraphPreparation.ABSENT:
            raise GraphExecutionError(GraphDiagnostic.MISSING_WORKFLOW)
        candidates = [
            DefinitionSnapshot.records_from(source)[self.workflow]
            for source in capabilities[WorkflowDocument.COMPONENT]
            if self.workflow in DefinitionSnapshot.records_from(source)
        ]
        if len(candidates) != GraphPreparation.ONE_DEFINITION:
            raise GraphExecutionError(GraphDiagnostic.AMBIGUOUS_WORKFLOW)
        graph = deepcopy(candidates[0])
        validate_graph(graph)
        return graph

    def _nested_engine(self, node, capabilities, *, selected=GraphPreparation.SELECTED):
        if WorkflowNode(node).kind == ControlNodeType.WORKFLOW:
            child = copy(self)
            child.workflow = WorkflowNode(node).workflow
            return child
        handler = self.handlers.get(WorkflowNode(node).kind)
        select = GraphHandlerHooks.graph_engine(handler)
        if not selected:
            select = GraphHandlerHooks.graph_discoverer(handler, select)
        engine = (
            select(deepcopy(node), capabilities)
            if select is not GraphPreparation.ABSENT
            else GraphPreparation.ABSENT
        )
        if engine is not GraphPreparation.ABSENT and not isinstance(engine, GraphEngine):
            raise TypeError(GraphDiagnostic.INVALID_NESTED_ENGINE)
        return engine

    def _walk(
        self,
        capabilities,
        context=GraphPreparation.ABSENT,
        ancestors=(),
        remaining=GraphPreparation.ABSENT,
        *,
        selected=GraphPreparation.STRUCTURAL,
    ):
        """참조 전체를 사전 탐색한다. 호출 위치별 정의를 유지하고 순환/깊이를 거부한다."""
        if context is not GraphPreparation.ABSENT and not self._configured:
            yield from self.with_config(context)._walk(
                capabilities, context, ancestors, remaining, selected=selected
            )
            return
        # capability 탐색에는 Project 문맥이 없다. 실제 깊이는 문맥을 받은 사전 검증에서 제한한다.
        depth = self.max_nested_depth
        remaining = (
            depth
            if remaining is GraphPreparation.ABSENT
            else remaining if depth is GraphPreparation.ABSENT else min(remaining, depth)
        )
        if (
            remaining is not GraphPreparation.ABSENT and remaining < GraphPreparation.MINIMUM_DEPTH
        ) or self.workflow in ancestors:
            raise GraphExecutionError(GraphDiagnostic.CYCLIC_WORKFLOW)
        if any(name not in capabilities for name in self.required_capabilities):
            if context is not GraphPreparation.ABSENT:
                raise GraphExecutionError(GraphDiagnostic.MISSING_CAPABILITY)
            (yield PreparedGraph(self, GraphPreparation.ABSENT, GraphPreparation.ABSENT, selected))
            return
        graph = self._definition(capabilities)
        (yield PreparedGraph(self, graph, context, selected))
        for node in self._nodes(graph):
            if WorkflowNode(node).kind not in frozenset(ControlNodeType):
                handler = self.handlers.get(WorkflowNode(node).kind)
                if handler is GraphPreparation.ABSENT:
                    raise GraphExecutionError(
                        GraphDiagnostic.UNREGISTERED_NODE.format(value=WorkflowNode(node).kind)
                    )
                validate_handler_options(node, handler)
            child = self._nested_engine(node, capabilities, selected=GraphPreparation.STRUCTURAL)
            if child is not GraphPreparation.ABSENT:
                child_context = context
                prepare = GraphHandlerHooks.graph_context(self.handlers.get(WorkflowNode(node).kind))
                if context is not GraphPreparation.ABSENT and prepare is not GraphPreparation.ABSENT:
                    child_context = prepare(deepcopy(node), context)
                yield from child._walk(
                    capabilities,
                    child_context,
                    (*ancestors, self.workflow),
                    (
                        GraphPreparation.ABSENT
                        if remaining is GraphPreparation.ABSENT
                        else remaining - GraphPreparation.DEPTH_INCREMENT
                    ),
                    selected=selected
                    or GraphHandlerHooks.selection_bound(self.handlers.get(WorkflowNode(node).kind)),
                )

    def _prepare(self, context: EngineContext):
        """실행별 정의 스냅샷을 한 번 탐색해 검증과 재개 바인딩에 함께 사용한다.

        Run 사이에 캐시하지 않는다. 다음 실행/재개는 변경된 정의와 설정을 다시 검사한다.
        """
        prepared = list(self._walk(context.capabilities, context))
        for engine, document, child_context, _ in prepared:
            for node in self._nodes(document):
                engine._deadline(WorkflowNode(node).timeout_seconds)
                if WorkflowNode(node).kind not in frozenset(ControlNodeType):
                    handler = engine.handlers.get(WorkflowNode(node).kind)
                    if handler is GraphPreparation.ABSENT:
                        raise GraphExecutionError(
                            GraphDiagnostic.UNREGISTERED_NODE.format(value=WorkflowNode(node).kind)
                        )
                    if not GraphHandlerHooks.selection_bound(handler):
                        self._validate_handler(handler, node, child_context)
        return prepared[0].document, prepared

    @staticmethod
    def _validate_handler(handler, node, context):
        validate = GraphHandlerHooks.validator(handler)
        if validate is not GraphPreparation.ABSENT:
            result = validate(deepcopy(node), context)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError(GraphDiagnostic.ASYNC_VALIDATOR)

    def _instruction_routes(self, context, graph):
        """컴파일과 같은 구조 경로를 열거한다. 실행/반복 번호·표시 path는 예약 ID가 아니다.

        실행 스냅샷만 읽으며 조건 분기의 실제 선택은 예측하지 않는다. 방문하지 않은
        분기 예약은 Run 종료 시 unapplied로 남는다.
        """
        routes = []

        def region(
            engine, document, current, prefix, entry=GraphPreparation.ABSENT, stop=GraphPreparation.ABSENT
        ):
            pending, seen = [
                WorkflowDocument(document).entry if entry is GraphPreparation.ABSENT else entry
            ], set()
            outgoing = {name: [] for name in WorkflowDocument(document).nodes}
            for edge in WorkflowDocument(document).edges:
                outgoing[WorkflowEdge(edge).source].append(WorkflowEdge(edge).target)
            while pending:
                name = pending.pop()
                if name == stop or name in seen:
                    continue
                seen.add(name)
                node = WorkflowDocument(document).nodes[name]
                kind = WorkflowNode(node).kind
                handler = engine.handlers.get(kind)
                describe = GraphHandlerHooks.instruction_engine(handler)
                consumer = (
                    describe(node, current)
                    if describe is not GraphPreparation.ABSENT
                    else GraphPreparation.ABSENT
                )
                if consumer is not GraphPreparation.ABSENT:
                    routes.append(SteeringRoute(tuple(prefix), name, consumer))
                nested = engine._nested_engine(
                    node, current.capabilities, selected=GraphPreparation.STRUCTURAL
                )
                if nested is not GraphPreparation.ABSENT:
                    prepare = GraphHandlerHooks.graph_context(handler)
                    child_context = (
                        prepare(node, current) if prepare is not GraphPreparation.ABSENT else current
                    )
                    region(
                        nested,
                        nested._definition(child_context.capabilities),
                        child_context,
                        [*prefix, name, GraphScope.WORKFLOW, nested.workflow],
                    )
                if kind == ControlNodeType.LOOP:
                    region(engine, WorkflowNode(node).body_definition, current, [*prefix, name])
                if kind == ControlNodeType.PARALLEL:
                    for start in outgoing[name]:
                        region(
                            engine,
                            document,
                            current,
                            [*prefix, name, GraphScope.BRANCH, start],
                            start,
                            WorkflowNode(node).join,
                        )
                    pending.append(WorkflowNode(node).join)
                else:
                    pending.extend(outgoing[name])

        region(self, graph, context, [self.workflow])
        return routes

    # 공개 API

    def workflow_handlers(self):
        """Registered action owners for retained-definition reference checks; no execution."""
        return dict(self.handlers)

    def agent_references(self):
        """The selected Graph owns the meaning of its stored Workflow choice."""
        from llm.components.workflows import WorkflowComponent

        return (ResourceTarget(WorkflowComponent.name, self.workflow).to_dict(),)

    def discover_agent(self, definition):
        """Resolve only structural references before selecting private Agent options."""
        return self._select_workflow(GraphAgentOptions.discover(definition))

    def _select_workflow(self, workflow):
        """Bind the Graph-owned Workflow selection; read its definition at execution."""
        GraphAgentOptions.validate_workflow(workflow)
        worker = copy(self)
        worker.workflow = workflow
        worker._configured = GraphParameters.UNCONFIGURED
        return worker

    def for_agent(self, definition: dict):
        """Agent가 지정한 Workflow를 부모 Graph 실행 범위에 연결한다."""
        options = GraphAgentOptions.from_definition(definition)
        options.validate_fields()
        # 빈 resources/tools도 의미 없는 별도 behavior/authority 계층이다.
        from llm.core.agents import AgentDefinition

        AgentDefinition.validate_mode(definition, self.agent_mode)
        worker = self._select_workflow(options.pop_workflow())
        options = options.to_dict()
        from jsonschema import Draft202012Validator

        Draft202012Validator(self.describe_config()).validate(options)
        copy(self)._configure(self.parameter_layout.unpack(options))
        worker._agent_options, worker._configured = options, GraphParameters.UNCONFIGURED
        worker.parameter_key = self.parameter_key or AgentDefinition.engine(definition)
        return worker

    def additional_capabilities(self, capabilities) -> tuple[str, ...]:
        """선택한 Workflow의 노드가 요청한 리소스만 서비스에서 구성한다."""
        names = []
        for engine, graph, _, _ in self._walk(capabilities):
            names.extend(engine.required_capabilities)
            if graph is not GraphPreparation.ABSENT:
                for node in self._nodes(graph):
                    if GraphHandlerHooks.selection_bound(engine.handlers.get(WorkflowNode(node).kind)):
                        continue
                    request = GraphHandlerHooks.discoverer(engine.handlers.get(WorkflowNode(node).kind))
                    if request is not GraphPreparation.ABSENT:
                        extra = request(deepcopy(node), capabilities)
                        if not isinstance(extra, tuple) or any(
                            not isinstance(name, str) or not name.strip() for name in extra
                        ):
                            raise TypeError(GraphDiagnostic.INVALID_CAPABILITIES)
                        names.extend(extra)
        return tuple(dict.fromkeys(names))

    def validate_resume(
        self,
        checkpoint: dict,
        *,
        retry_nodes=(),
        context=GraphPreparation.ABSENT,
        decisions=GraphPreparation.ABSENT,
    ) -> None:
        """결과가 불확실한 처리 노드를 누락/중복 없이 명시적으로 승인해야 한다."""
        header, records = (
            CheckpointContent.from_checkpoint(checkpoint).header,
            CheckpointRecords.from_checkpoint(checkpoint).records,
        )
        decisions = decisions or {}
        Component.serialize(decisions)
        if not isinstance(decisions, dict):
            raise ValueError(GraphDiagnostic.INVALID_DECISIONS)
        for key, decision in decisions.items():
            saved = records.get(key, {})
            if CheckpointEnvelope.is_record(saved):
                from llm.core.checkpoints import unwrap_record

                unwrap_record(saved)
                if (
                    GraphNodeRecord(saved).optional_status != CheckpointStatus.WAITING
                    or not GraphNodeRecord(saved).has_interaction
                ):
                    raise ValueError(GraphDiagnostic.CHILD_NOT_WAITING)
                GraphNodeRecord(saved).interaction.select_decision(decision)
                continue
            if (
                GraphNodeRecord(saved).optional_status != CheckpointStatus.WAITING
                or not isinstance(decision, dict)
                or GraphDecision(decision).extra_fields
            ):
                raise ValueError(GraphDiagnostic.INVALID_DECISION_FIELDS)
            if GraphDecision(decision).has_approved and type(GraphDecision(decision).approved) is not bool:
                raise ValueError(GraphDiagnostic.INVALID_APPROVED)
            if GraphDecision(decision).has_state:
                if CheckpointEnvelope.is_record(saved):
                    raise ValueError(GraphDiagnostic.PARENT_STATE_EDIT)
                schema = GraphNodeRecord(saved).resume_schema
                if (
                    not isinstance(schema, dict)
                    or schema.get(SchemaKeyword.ADDITIONAL_PROPERTIES) is not SchemaBoolean.REJECT
                ):
                    raise ValueError(GraphDiagnostic.OPEN_RESUME_SCHEMA)
                WorkflowBindings.validate_resume_state(schema, GraphDecision(decision).state)
            if GraphNodeRecord(saved).has_interaction:
                GraphNodeRecord(saved).interaction.select_decision(
                    decision, confirm_empty=GraphDecision.CONFIRM_EMPTY
                )
        for key, saved in records.items():
            if CheckpointEnvelope.is_record(saved):
                if (
                    GraphNodeRecord(saved).optional_status == CheckpointStatus.WAITING
                    and key not in decisions
                ):
                    raise ValueError(GraphDiagnostic.MISSING_CHILD_DECISION.format(value=key))
                continue
            if (
                GraphNodeRecord(saved).approval_required
                and not GraphDecision(decisions.get(key, {})).has_approved
            ):
                raise ValueError(GraphDiagnostic.MISSING_TOOL_DECISION.format(value=key))
        if (
            GraphCheckpointHeader(header).format != GRAPH_CHECKPOINT_FORMAT
            or GraphExecutionBinding(GraphCheckpointHeader(header).binding).workflow_id != self.workflow
        ):
            raise ValueError(GraphDiagnostic.WRONG_WORKFLOW)
        if GraphExecutionBinding(GraphCheckpointHeader(header).binding).revision != self.revision:
            raise ValueError(GraphDiagnostic.CHANGED_REVISION)
        if context is not GraphPreparation.ABSENT:
            graph, prepared = self._prepare(context)
            self._validate_selections(context, prepared, records)
            if self._binding(context, graph, prepared) != GraphCheckpointHeader(header).binding:
                raise ValueError(GraphDiagnostic.CHANGED_EXECUTION)
        controls = ControlNodeType.structural()
        uncertain = {
            key
            for key, value in records.items()
            if GraphNodeRecord(value).status == CheckpointStatus.STARTED
            and GraphNodeRecord(value).node_type not in controls
            and GraphNodeRecord(value).non_container
        }
        if (
            any(not isinstance(key, str) for key in retry_nodes)
            or len(set(retry_nodes)) != len(retry_nodes)
            or set(retry_nodes) != uncertain
        ):
            raise ValueError(GraphDiagnostic.RETRY_SET.format(value=sorted(uncertain)))
        if any(
            GraphNodeRecord(value).status
            not in (CheckpointStatus.STARTED, CheckpointStatus.COMPLETED, CheckpointStatus.WAITING)
            for value in records.values()
        ):
            raise ValueError(GraphDiagnostic.INVALID_NODE_STATUS)

    async def execute(self, context: EngineContext):
        from llm.services.runtime.tools import ToolExecutionScope
        ToolExecutionScope.require(context)
        worker = self.with_config(context)
        async with aclosing(worker._execute(context)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext):
        graph, prepared = self._prepare(context)
        routes = (
            self._instruction_routes(context, graph)
            if context.steering is not GraphPreparation.ABSENT
            else ()
        )
        binding = self._binding(context, graph, prepared)
        if context.checkpoint is not GraphPreparation.ABSENT:
            self._validate_selections(
                context, prepared, CheckpointRecords.from_checkpoint(context.checkpoint).records
            )
            self.validate_resume(
                context.checkpoint,
                retry_nodes=ResumeRecord.require(context.run.metadata).retry_nodes,
                decisions=ResumeRecord.require(context.run.metadata).decisions,
            )
            header = deepcopy(CheckpointContent.from_checkpoint(context.checkpoint).header)
            if binding != GraphCheckpointHeader(header).binding:
                raise GraphExecutionError(GraphDiagnostic.CHANGED_EXECUTION)
        else:
            message = next(item for item in context.messages if item.id == context.run.input_message_id)
            state = deepcopy(WorkflowDocument(graph).initial_state)
            if WorkflowBindings(graph).has_inputs:
                state.update(
                    bind(
                        WorkflowBindings(graph).inputs,
                        GraphInput.create(prompt=message.content, message_id=message.id),
                    )
                )
            validate_value(graph, BindingDirection.INPUT, state)
            header = GraphCheckpointHeader.create(
                format=GRAPH_CHECKPOINT_FORMAT,
                binding=binding,
                initial_state=state,
                extension=CheckpointConversation(
                    tuple((item.id for item in context.messages)), message.id
                ).to_dict(),
            )
        Component.serialize(header)
        # 첫 LangGraph import도 UI 루프 밖에서 수행한다. 플러그인 import는 가볍게 유지한다.
        await asyncio.to_thread(importlib.import_module, LangGraphInvocation.MODULE)
        queue = asyncio.Queue(maxsize=self.buffer_size)

        async def emit(event):
            if not isinstance(event, EngineEvent):
                raise TypeError(GraphDiagnostic.INVALID_EVENT)
            ack = asyncio.get_running_loop().create_future()
            await queue.put((deepcopy(event), ack))
            # 서비스가 이벤트를 저장하고 다음 이벤트를 요청한 뒤 작업을 계속한다.
            await ack

        async def produce():
            if context.checkpoint is GraphPreparation.ABSENT:
                await emit(
                    EngineEvent(
                        EngineEventType.CHECKPOINT,
                        metadata=CheckpointWrite.initialize_event(
                            GRAPH_CHECKPOINT_NAME, header, {}
                        ).to_dict(),
                    )
                )
            async with aclosing(BaseEngine.declare_instruction_routes(context, routes)) as events:
                async for event in events:
                    await emit(event)
            root_id = new_id()
            await emit(
                EngineEvent(
                    EngineEventType.STEP_STARTED,
                    step_id=root_id,
                    kind=GraphExecutionObservation.ROOT_STEP,
                    name=self.workflow,
                    metadata=GraphStepMetadata.root(workflow_id=self.workflow, definition=graph),
                )
            )
            try:
                async with timeout(self.timeout_seconds):
                    state = deepcopy(GraphCheckpointHeader(header).initial_state)
                    runtime = _WorkflowRuntime(self, context, emit, routes=routes)
                    state = await runtime.run(graph, state, self.workflow)
                    output = (
                        bind(WorkflowBindings(graph).outputs, state)
                        if WorkflowBindings(graph).has_outputs
                        else deepcopy(state)
                    )
                    validate_value(graph, BindingDirection.OUTPUT, output)
                    GraphExecutionObservation.publish(context.state, deepcopy(output))
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except _GraphPause as pause:
                await emit(
                    EngineEvent(
                        EngineEventType.PAUSED,
                        metadata={
                            **CheckpointPause(GRAPH_CHECKPOINT_NAME).to_dict(),
                            self.PAUSE_NODE_KEY: str(pause),
                        },
                    )
                )
                return
            except Exception as error:
                await emit(BaseEngine.step_failed_event(root_id, error))
                raise
            await emit(
                BaseEngine.step_completed_event(
                    context,
                    root_id,
                    EngineOutput(data=output, visibility=OutputVisibility.INTERNAL),
                    metadata=GraphStepMetadata.completed(
                        visited_nodes=runtime.visited, runtime=GraphExecutionObservation.RUNTIME
                    ),
                )
            )
            await emit(BaseEngine.output_event(context, EngineOutput(data=output)))

        producer = asyncio.create_task(produce())
        reader = GraphPreparation.ABSENT
        try:
            while True:
                if producer.done() and queue.empty():
                    await producer  # 내부 작업 실패를 RunManager로 전달한다.
                    break
                reader = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((reader, producer), return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    event, ack = reader.result()
                    reader = GraphPreparation.ABSENT
                    yield event
                    if not ack.done():
                        ack.set_result(GraphPreparation.ABSENT)
                else:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=self.DRAIN_EXCEPTIONS)
                    reader = GraphPreparation.ABSENT
        finally:
            if reader is not GraphPreparation.ABSENT:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=self.DRAIN_EXCEPTIONS)
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=self.DRAIN_EXCEPTIONS)
