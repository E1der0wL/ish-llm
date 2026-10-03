"""Workflow의 LangGraph 컴파일·실행과 Run/Step 이벤트 연결을 한곳에서 관리한다.

공개 GraphEngine과 Run별 내부 실행 객체를 분리하며 LangGraph는 실행 시 지연 로딩한다.
상태 채널에는 JSON만 전달하고 런타임 객체를 저장 형식에 노출하지 않는다.
"""

import asyncio
import inspect
import importlib
import json
import math
import operator
from copy import copy, deepcopy
from llm.core.configuration import engine_configuration
from contextlib import AsyncExitStack, aclosing
from llm.core.interactions import InteractionRequest, approval_request
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Annotated, Awaitable, Callable, Literal, Mapping, Optional, TYPE_CHECKING, TypedDict

from asyncio import timeout
from llm.components.base import Component
from llm.components.workflows.graph import validate_graph
from llm.components.workflows.bindings import bind, read_pointer, validate_value
from llm.core.models import new_id
from llm.core.results import EngineOutput
from llm.core.steering import SteeringMode, SteeringRoute
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType, required_capabilities


if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class GraphExecutionError(RuntimeError):
    """노드 등록, 실행 또는 반복 제한 위반."""


class _GraphPause(Exception):
    """처리기 호출 전의 명시적 대기 경계. 일반 노드 실패와 구분한다."""


def matches(condition: dict, state: dict) -> bool:
    """검증된 JSON Pointer 조건을 평가한다. 누락된 값은 일반 비교에서도 거짓이다."""
    try:
        value = read_pointer(state, condition["path"])
    except KeyError:
        return False
    op = condition["op"]
    if op == "exists":
        return True
    other = condition["value"]
    # JSON에서 boolean과 number는 구분한다(Python의 True == 1과 다름).
    def equal(left, right):
        if isinstance(left, bool) != isinstance(right, bool):
            return False
        if isinstance(left, dict) and isinstance(right, dict):
            return left.keys() == right.keys() and all(equal(left[key], right[key]) for key in left)
        if isinstance(left, list) and isinstance(right, list):
            return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
        return left == right
    if op in ("eq", "ne"):
        result = equal(value, other)
        return result if op == "eq" else not result
    if op == "in":
        return any(equal(value, item) for item in other)
    if not ((type(value) in (int, float) and type(other) in (int, float))
            or (isinstance(value, str) and isinstance(other, str))):
        raise GraphExecutionError("Ordered comparison requires two numbers or two strings")
    return {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}[op](value, other)


# 실행 판단은 JSON만 읽는다. 이벤트·체크포인트 쓰기와 처리기 호출은 아래 런타임이 담당한다.
def _branch_port(definition: dict, state: dict) -> str:
    """선언 순서대로 첫 일치 port를 고른다. 입력 상태와 정의는 변경하지 않는다."""
    return next((case["port"] for case in definition["cases"]
                 if matches(case["when"], state)), definition["default"])


def _loop_continues(definition: dict, state: dict, count: int) -> bool:
    """다음 반복 여부만 판단한다. 고정 반복과 조건부 반복의 상한 계약을 구분한다."""
    condition = definition.get("while")
    proceed = condition is None or matches(condition, state)
    if proceed and count < definition["max_iterations"]:
        return True
    if proceed and condition is not None and definition["on_limit"] == "fail":
        raise GraphExecutionError("Loop iteration limit reached")
    return False


@dataclass(frozen=True, slots=True)
class _NodePlan:
    """한 노드의 다음 동작과 독립된 decision 사본. 저장 모델이나 승인 증명이 아니다."""

    action: Literal["reuse", "pause", "execute"]
    decision: dict = field(default_factory=dict)


def _plan_node(definition: dict, state: dict, *, saved: Optional[dict],
               resuming: bool, decisions: dict, key: str) -> _NodePlan:
    """완료 재사용·사전 대기·실행을 판단한다. 상태/기록을 쓰거나 승인을 생성하지 않는다.

    binding, retry_nodes, Interaction 검증은 기존 resume 진입 경계가 소유한다.
    계획은 즉시 소비하며 완료 결과·검토 입력의 원본은 기존 체크포인트에 남는다.
    """
    if saved and saved.get("container") and saved["input_state"] != state:
        raise GraphExecutionError("Checkpoint container input changed; start a new request")
    if saved and saved["status"] == "completed":
        if saved["input_state"] != state:
            raise GraphExecutionError("Checkpoint node input does not match restored state")
        return _NodePlan("reuse")
    if definition.get("pause_before", False) and not (resuming and saved):
        return _NodePlan("pause")
    decision = decisions.get(key, (saved or {}).get("decision", {}))
    if decision.get("approved") is False:
        raise GraphExecutionError("Workflow continuation rejected by user")
    return _NodePlan("execute", deepcopy(decision))


@dataclass(frozen=True, slots=True)
class GraphNodeContext:
    """state는 전체 상태 복사본, inputs는 명시적으로 선택된 입력이다.

    반환 dict를 검증하고 outputs 매핑으로 선택한 키만 실행 상태에 병합한다.
    inputs/outputs를 생략한 기존 처리기는 전체 상태와 반환 dict 병합 계약을 유지한다.
    """

    context: EngineContext
    node_id: str
    definition: dict
    state: dict
    emit: Callable[[EngineEvent], Awaitable[None]]
    inputs: Optional[dict] = None
    # 선언된 하위 그래프만 현재 Run/체크포인트 범위에서 실행하는 런타임 연결점.
    invoke_graph: Optional[Callable] = None
    checkpoint_key: Optional[str] = None
    record_usage: Optional[Callable] = None
    # 이 노드의 확인 응답이며 Tool 실행 허가는 아니다. Tool은 context.execute_tool을 사용한다.
    decision: Optional[bool] = None
    node_path: Optional[str] = None


class _Frame(TypedDict):
    data: dict
    path: str
    port: str
    scope: list


def _propagate_cancellation(error: Exception) -> None:
    """LangGraph의 노드 자발적 취소 오류를 Engine 중단 계약으로 돌린다.

    오류 문자열을 비교하거나 일반 노드 오류를 취소로 바꾸지 않는다.
    """
    from langgraph.errors import NodeCancelledError

    if isinstance(error, NodeCancelledError):
        raise asyncio.CancelledError() from error


class _LoopFrame(TypedDict):
    data: dict
    path: str
    count: int
    scope: list


def _merge_branches(left: dict, right: dict) -> dict:
    """분기별 결과만 합친다. 공통 상태에 대한 병렬 쓰기는 만들지 않는다."""
    if left.keys() & right.keys():
        raise GraphExecutionError("Duplicate parallel branch result")
    return {**left, **right}


class _ParallelFrame(TypedDict):
    data: dict
    path: str
    branches: Annotated[dict, _merge_branches]
    scope: list


_UNSET = object()


class _WorkflowRuntime:
    """LangGraph 실행 + 노드 결과 체크포인트. 완료 결과만 복원하고 부작용은 재생하지 않는다."""

    def __init__(self, engine: "GraphEngine", context: EngineContext,
                 emit: Callable[[EngineEvent], Awaitable[None]], *, parent=None, routes=()) -> None:
        self.engine = engine
        self.parents = (*parent.parents, parent) if parent is not None else ()
        self.handlers = dict(engine.handlers)
        self.emit = emit
        self._context = ContextVar("workflow_engine_context", default=context)
        self.max_steps = engine.max_steps
        self.visited = 0
        self.semaphore = None if engine.max_parallelism is None else asyncio.Semaphore(engine.max_parallelism)
        # 제어용 gate도 super-step을 사용한다. 실제 예산은 모든 하위 그래프가 공유한다.
        self.config = {} if engine.max_steps is None else {"recursion_limit": engine.max_steps * 3 + 10}
        self.records = parent.records if parent is not None else (
            deepcopy(context.checkpoint["records"]) if context.checkpoint else {})
        self.resuming = context.checkpoint is not None
        self.routes = parent.routes if parent is not None else {r.key: r for r in routes}
        self.root_workflow = parent.root_workflow if parent is not None else engine.workflow

    async def _record(self, key, value):
        interaction = InteractionRequest.from_dict(value["interaction"]) if "interaction" in value else None
        await self.emit(EngineEvent(EngineEventType.CHECKPOINT, interaction=interaction, metadata={
            "name": "graph", "operation": "record", "key": key, "value": value}))
        self.records[key] = deepcopy(value)

    async def _nested(self, engine, context, inputs, scope, path, parent_step_id, emit):
        """루트 저장소/예산을 공유하되 상태와 입출력 계약은 호출마다 분리한다."""
        engine = engine.configured(context)
        graph, _ = engine._prepare(context)
        state = deepcopy(graph.get("initial_state", {}))
        state.update(bind(graph["inputs"], inputs) if "inputs" in graph else deepcopy(inputs))
        validate_value(graph, "input", state)
        step_id = new_id()
        async def child_emit(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(event, metadata={**event.metadata,
                    "parent_step_id": event.metadata.get("parent_step_id", step_id),
                    "workflow_id": event.metadata.get("workflow_id", engine.workflow)})
            await emit(event)
        await emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind="subgraph",
                              name=engine.workflow, metadata={"parent_step_id": parent_step_id,
                              "workflow_id": engine.workflow, "path": path, "inputs": inputs}))
        runtime = _WorkflowRuntime(engine, replace(context, state={}), child_emit, parent=self)
        try:
            async with timeout(engine.timeout_seconds):
                state = await runtime.run(graph, state, path, scope=scope + ["workflow", engine.workflow])
                output = bind(graph["outputs"], state) if "outputs" in graph else deepcopy(state)
                validate_value(graph, "output", output)
        except (asyncio.CancelledError, _GraphPause):
            raise
        except Exception as error:
            await emit(BaseEngine.step_failed_event(step_id, error))
            raise
        await emit(BaseEngine.step_completed_event(context, step_id,
                                                   EngineOutput(data=output, visibility="internal")))
        return output

    def _compile(self, document: dict, entry: Optional[str] = None,
                 stop: Optional[str] = None) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        entry = document["entry"] if entry is None else entry
        outgoing = {name: [] for name in document["nodes"]}
        for edge in document["edges"]:
            outgoing[edge["source"]].append(edge)

        # 병렬 영역은 독립 하위 그래프로 컴파일한다. 부모는 fork 다음 join으로 연결한다.
        region, pending = {}, [entry]
        while pending:
            name = pending.pop()
            if name == stop or name in region:
                continue
            node = document["nodes"][name]
            region[name] = node
            pending.extend([node["join"]] if node["type"] == "parallel"
                           else [edge["target"] for edge in outgoing[name]])
        identifiers = {name: f"node_{index}" for index, name in enumerate(region)}
        # 사용자 ID가 __start__/__end__ 또는 콜론을 포함해도 내부 예약 이름과 충돌하지 않는다.
        def target(name):
            return END if name == stop else identifiers[name]

        builder = StateGraph(_Frame)
        for name, node in region.items():
            child = None
            if node["type"] == "parallel":
                child = self._parallel(document, name, outgoing[name])
            elif node["type"] == "loop":
                child = self._loop(node)
            builder.add_node(identifiers[name], self._node(name, node, child), retry_policy=None)
            if node["type"] == "end":
                builder.add_edge(identifiers[name], END)
            elif node["type"] == "branch":
                builder.add_conditional_edges(identifiers[name], self._port,
                    {edge["port"]: target(edge["target"]) for edge in outgoing[name]})
            elif node["type"] == "parallel":
                builder.add_edge(identifiers[name], target(node["join"]))
            else:
                builder.add_edge(identifiers[name], target(outgoing[name][0]["target"]))
        builder.add_edge(START, identifiers[entry])
        return builder.compile(checkpointer=False)

    @staticmethod
    def _port(frame: _Frame) -> str:
        return frame["port"]

    def _parallel(self, document: dict, name: str, links: list[dict]) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        node = document["nodes"][name]
        builder = StateGraph(_ParallelFrame)
        branch_ids = []
        for index, edge in enumerate(links):
            start = edge["target"]
            child = self._compile(document, start, node["join"])

            async def branch(frame, child=child, start=start):
                # 같은 컴파일 결과를 반복 실행해도 branch의 임시 상태는 회차마다 새로 만든다.
                token = self._context.set(replace(self._context.get(), state={}))
                try:
                    result = await child.ainvoke({"data": deepcopy(frame["data"]), "path": frame["path"],
                                                 "port": "", "scope": frame["scope"] + ["branch", start]}, self.config)
                    return {"branches": {start: result["data"]}}
                finally:
                    self._context.reset(token)

            identifier = f"branch_{index}"
            branch_ids.append(identifier)
            builder.add_node(identifier, branch, retry_policy=None)
            builder.add_edge(START, identifier)
        # LangGraph가 병렬 스케줄링과 모든 경로의 완료 대기를 담당한다.
        builder.add_edge(branch_ids, END)
        return builder.compile(checkpointer=False)

    def _loop(self, node: dict) -> "CompiledStateGraph":
        from langgraph.graph import END, START, StateGraph

        body = self._compile(node["body"])
        builder = StateGraph(_LoopFrame)

        def route(frame):
            return "body" if _loop_continues(node, frame["data"], frame["count"]) else END

        async def advance(frame):
            count = frame["count"] + 1
            result = await body.ainvoke({"data": frame["data"], "path": f"{frame['path']}[{count}]",
                                         "port": "", "scope": frame["scope"] + [count]}, self.config)
            return {"data": result["data"], "count": count}

        # LangGraph의 조건부 후방 간선으로 반복하며 Python while 실행기는 두지 않는다.
        async def gate(frame):
            return {}

        builder.add_node("gate", gate, retry_policy=None)
        builder.add_node("body", advance, retry_policy=None)
        builder.add_edge(START, "gate")
        builder.add_conditional_edges("gate", route, {"body": "body", END: END})
        builder.add_edge("body", "gate")
        return builder.compile(checkpointer=False)

    async def _call(self, handler: Callable[[GraphNodeContext], Awaitable[dict]],
                    node: GraphNodeContext) -> dict:
        """핸들러에 취소를 한 번 전달하고 설정한 시간까지만 정리를 기다린다.

        LangGraph의 중첩 실행기는 형제 실패/종료 과정에서 같은 노드를 여러 번 취소할
        수 있다. 별도 asyncio.Task를 shield하여 처리기의 finally 정리를 두 번째 취소로 끊지 않는다.
        그래프의 노드 선택/병렬 스케줄링은 계속 LangGraph가 담당한다.
        """
        from langgraph import errors as graph_errors

        accepting = True
        original_emit = node.emit
        async def guarded_emit(event):
            if not accepting:
                raise asyncio.CancelledError()
            await original_emit(event)
        def guard(callback):
            if callback is None:
                return None
            async def invoke(*args, **kwargs):
                if not accepting:
                    raise asyncio.CancelledError()
                return await callback(*args, **kwargs)
            return invoke
        pending = asyncio.ensure_future(handler(replace(node, emit=guarded_emit,
            invoke_graph=guard(node.invoke_graph), record_usage=guard(node.record_usage))))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            accepting = False
            if node.context.tool_scope is not None:
                node.context.tool_scope.revoke()
            if not pending.done():
                pending.cancel()
            deadline = asyncio.get_running_loop().time() + self.engine.cleanup_timeout
            while not pending.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    if node.context.pending_work is not None:
                        node.context.pending_work.track(pending)
                    else:
                        pending.add_done_callback(lambda pending: pending.exception() if not pending.cancelled() else None)
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
            raise GraphExecutionError("LangGraph interrupts require explicit resume integration") from error

    async def _execute_node(self, definition, state, child, nested, info, scope):
        """노드의 계산·입출력 연결만 수행한다. 승인/재개와 Step 수명은 _node가 감싼다."""
        kind, context = definition["type"], self._context.get()
        path, step_id = info["path"], info["step_id"]
        key = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
        detail, port = {}, ""
        if kind == "branch":
            port = _branch_port(definition, state)
            detail["port"] = port
        elif kind == "parallel":
            result = await child.ainvoke({"data": state, "path": path, "branches": {}, "scope": scope}, self.config)
            state["branches"] = result["branches"]
        elif kind == "loop":
            result = await child.ainvoke({"data": state, "path": path, "count": 0, "scope": scope}, self.config)
            state = result["data"]
            detail["iterations"] = result["count"]
        elif kind not in ("end", "join"):
            inputs = bind(definition["inputs"], state) if "inputs" in definition else deepcopy(state)
            validate_value(definition, "input", inputs)

            async def node_emit(event):
                if event.type == EngineEventType.OUTPUT and (
                        event.output is None or event.output.step_id is None):
                    raise GraphExecutionError("Nested output must belong to a Step")
                if event.type == EngineEventType.STEP_STARTED:
                    event = replace(event, metadata={"parent_step_id": step_id,
                        "node_checkpoint_key": key, "node_path": path, **event.metadata})
                await self.emit(event)
                if event.type == EngineEventType.CHECKPOINT and event.metadata.get("operation") == "record":
                    self.records[event.metadata["key"]] = deepcopy(event.metadata["value"])

            async def invoke_graph(engine, child_context, child_inputs, emit):
                if nested is None or engine.workflow != nested.workflow:
                    raise GraphExecutionError("Nested Workflow must match its preflight declaration")
                return await self._nested(engine, child_context, child_inputs, scope,
                                          path + "/" + engine.workflow, step_id, emit)

            async def record_usage(usage):
                record = deepcopy(self.records[key])
                record.setdefault("agent_usage", {})[key] = deepcopy(usage)
                await self._record(key, record)

            if kind == "workflow":
                result = await invoke_graph(nested, context, inputs, node_emit)
            else:
                # 조율 노드는 슬롯을 잡지 않는다. 실제 작업만 조상 순서로 획득한다.
                async with AsyncExitStack() as slots:
                    if nested is None:
                        for runtime in (*self.parents, self):
                            if runtime.semaphore is not None:
                                await slots.enter_async_context(runtime.semaphore)
                    result = await self._call(self.handlers[kind], GraphNodeContext(
                        context, info["node_id"], deepcopy(definition), deepcopy(state), node_emit,
                        deepcopy(inputs), invoke_graph if nested is not None else None, key,
                        record_usage if info["container"] else None, info["decision"].get("approved"), path))
            Component.serialize(result)
            validate_value(definition, "output", result)
            updates = bind(definition["outputs"], result) if "outputs" in definition else deepcopy(result)
            detail.update({"input": inputs, "result": result})
            state.update(updates)
        return state, detail, port

    def _node(self, name: str, definition: dict, child: Optional["CompiledStateGraph"]
              ) -> Callable[[_Frame], Awaitable[dict]]:
        async def execute(frame):
            from llm.services.runtime.tools import ToolApprovalRequired
            context = self._context.get()
            for runtime in (*self.parents, self):
                runtime.visited += 1
                if runtime.max_steps is not None and runtime.visited > runtime.max_steps:
                    raise GraphExecutionError("Graph node execution limit reached")
            state = deepcopy(frame["data"])
            kind, step_id = definition["type"], new_id()
            path = f"{frame['path']}/{name}"
            scope = frame["scope"] + [name]
            # 표시용 path에는 / 등이 들어갈 수 있으므로 실행 식별자는 JSON 배열로 구분한다.
            key = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
            nested = self.engine._nested_engine(definition, context.capabilities)
            checkpoint_engine = getattr(self.handlers.get(kind), "checkpoint_engine", None)
            resumable = (nested is not None or callable(checkpoint_engine) and checkpoint_engine(definition, context) is not None)
            saved = self.records.get(key)
            info = {"path": path, "node_id": name, "node_type": kind, "step_id": step_id,
                    "source_run_id": context.run.id, "input_state": deepcopy(state),
                    "container": kind == "workflow" or (resumable and
                        getattr(self.handlers.get(kind), "resumable_container", False) is True)}
            plan = _plan_node(definition, state, saved=saved, resuming=self.resuming,
                              decisions=context.run.metadata.get("resume", {}).get("decisions", {}), key=key)
            if plan.action == "reuse":
                await self.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id,
                    kind="graph_node", name=name, metadata={**info, "checkpoint_key": key,
                        "reused_from_run": saved["source_run_id"], "reused_step_id": saved["step_id"]}))
                await self.emit(BaseEngine.step_completed_event(context, step_id,
                    EngineOutput(data=saved["output"], visibility="internal"), metadata={"reused": True}))
                return {"data": deepcopy(saved["output"]), "path": frame["path"],
                        "port": saved["port"], "scope": frame["scope"]}
            if plan.action == "pause":
                request = approval_request("Workflow 노드 실행 확인",
                    source={"project_id": context.project.id, "session_id": context.session.id,
                            "run_id": context.run.id, "node_id": name, "path": path},
                    action={"definition": deepcopy(definition)}, category="workflow.continue")
                request = replace(request, kind="confirmation", input_schema=definition.get("resume_schema"))
                request = request.bind("graph", key, decision_key="approved", input_key="state")
                await self._record(key, {**info, "status": "waiting", "resume_schema": definition.get("resume_schema"),
                                        "interaction": request.to_dict()})
                raise _GraphPause(key)
            decision = plan.decision
            state.update(deepcopy(decision.get("state", {})))
            info["decision"] = deepcopy(decision)
            # started를 먼저 fsync한다. 완료 저장 전 종료되면 재실행 승인이 필요한 노드다.
            # 예약 경계는 Agent/MCP/모델 실행보다 앞선다. 정수 반복 번호만 제거하고
            # 호출 위치와 병렬 분기 경로는 보존하므로 같은 이름의 다른 노드와 혼동하지 않는다.
            route_key = json.dumps([self.root_workflow, *(v for v in scope if isinstance(v, str))],
                                   ensure_ascii=False, separators=(",", ":"))
            if context.steering is not None and route_key in self.routes:
                async with aclosing(BaseEngine.bind_instruction_route(context, self.routes[route_key], key)) as events:
                    async for event in events:
                        await self.emit(event)
            await self._record(key, {**info, "status": "started",
                "requires_retry": kind not in {"branch", "parallel", "join", "loop", "end"} and not info["container"]})
            await self.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind="graph_node",
                name=name, metadata={"node_id": name, "path": path, "node_type": kind,
                                     "definition": definition, "checkpoint_key": key}))
            try:
                async with timeout(definition.get("timeout_seconds")):
                    state, detail, port = await self._execute_node(definition, state, child, nested, info, scope)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except _GraphPause:
                raise
            except ToolApprovalRequired as error:
                if kind != "tool":
                    raise  # Agent 내부 효과는 노드 전체가 불확실하므로 자동 승인 재개하지 않는다.
                await self._record(key, {**info, "status": "waiting", "approval_required": True,
                                        "prompt": str(error), "resume_schema": definition.get("resume_schema"),
                                        "interaction": error.request.bind("graph", key, decision_key="approved").to_dict()})
                raise _GraphPause(key)
            except Exception as error:
                _propagate_cancellation(error)
                await self.emit(BaseEngine.step_failed_event(step_id, error))
                raise
            await self._record(key, {**info, "status": "completed", "output": deepcopy(state), "port": port})
            await self.emit(BaseEngine.step_completed_event(context, step_id,
                EngineOutput(data=state, visibility="internal"), metadata=detail))
            return {"data": state, "path": frame["path"], "port": port, "scope": frame["scope"]}
        return execute

    async def run(self, document: dict, state: dict, path: str, *, scope=None) -> dict:
        """컴파일한 LangGraph를 실행하고 JSON 상태만 반환한다."""
        graph = self._compile(document)
        try:
            result = await graph.ainvoke({"data": state, "path": path, "port": "", "scope": scope or []}, self.config)
        except Exception as error:
            _propagate_cancellation(error)
            raise
        return result["data"]


class GraphEngine:
    """Workflow를 LangGraph로 실행한다. 처리기는 GraphNodeContext → dict 계약이다.

    처리기.required_capabilities로 기능을 선언하고 선택적인 validate(node, context)로
    실행 전 참조를 검사한다. 각 처리기는 파일/모델 작업을 수행할 수 있지만 Run/Step
    저장에는 emit을 사용한다. 검증 결과의 부적합은 상태로, 실행기 오류는 예외로 보고한다.
    AgentNode는 명시적으로 등록된 업무용 엔진에 실행을 위임한다.
    Workflow 노드 경계 체크포인트를 이벤트로 저장한다. resume은 완료 결과를 복원하고
    미완료 노드부터 실행하며, LangGraph 내부 프레임이나 Python 런타임 객체는 저장하지 않는다.
    """

    def __init__(self, workflow: str, *, handlers: Mapping[str, Callable],
                 max_steps=_UNSET, max_parallelism=_UNSET,
                 timeout_seconds=_UNSET, buffer_size=_UNSET,
                 revision: str = "1", max_nested_depth=_UNSET,
                 cleanup_timeout=_UNSET, config_keys: Optional[tuple[str, ...]] = None,
                 settings_name: Optional[str] = None) -> None:
        supplied = dict(max_steps=max_steps, max_parallelism=max_parallelism, timeout_seconds=timeout_seconds,
                        buffer_size=buffer_size, max_nested_depth=max_nested_depth, cleanup_timeout=cleanup_timeout)
        self._overrides = {key: value for key, value in supplied.items() if value is not _UNSET}
        values = self._overrides
        max_steps, max_parallelism, timeout_seconds = (values.get(k) for k in ("max_steps", "max_parallelism", "timeout_seconds"))
        # Buffer size only controls backpressure. Cleanup must terminate even if a handler ignores cancellation.
        buffer_size, max_nested_depth, cleanup_timeout = values.get("buffer_size", 32), values.get("max_nested_depth"), values.get("cleanup_timeout", 5.0)
        if settings_name is not None and (not isinstance(settings_name, str) or not settings_name.strip()):
            raise ValueError("settings_name must be nonempty text")
        self.settings_name, self._agent_options, self._configured = settings_name, {}, False
        if not isinstance(workflow, str) or not workflow:
            raise ValueError("GraphEngine requires a workflow ID")
        if any(value is not None and (type(value) is not int or value < 1) for value in (max_steps, max_parallelism, buffer_size)):
            raise ValueError("Graph limits must be positive integers")
        self._deadline(timeout_seconds)
        self._deadline(cleanup_timeout)
        if cleanup_timeout is None:
            raise ValueError("cleanup_timeout must be finite")
        if config_keys is not None and (not isinstance(config_keys, tuple) or any(not isinstance(k, str) or not k for k in config_keys)):
            raise ValueError("config_keys must be a tuple of project setting names")
        self.cleanup_timeout, self.config_keys = cleanup_timeout, config_keys
        if max_nested_depth is not None and (type(max_nested_depth) is not int or max_nested_depth < 0):
            raise ValueError("max_nested_depth must be an integer between 0 and 32")
        self.max_nested_depth = max_nested_depth
        if not isinstance(revision, str) or not revision:
            raise ValueError("GraphEngine revision must be nonempty text")
        self.revision = revision
        self.workflow, self.handlers = workflow, dict(handlers)
        controls = {"branch", "parallel", "join", "loop", "end", "workflow"}
        if any(not isinstance(name, str) or not name or name in controls or not callable(handler)
               for name, handler in self.handlers.items()):
            raise ValueError("Invalid handler or reserved control node type")
        self.required_capabilities = tuple(dict.fromkeys(
            ("workflows",) + tuple(name for handler in self.handlers.values()
                                   for name in required_capabilities(handler))))
        self.max_steps, self.max_parallelism = max_steps, max_parallelism
        self.timeout_seconds, self.buffer_size = timeout_seconds, buffer_size

    _option_names = ("max_steps", "max_parallelism", "timeout_seconds", "buffer_size", "max_nested_depth", "cleanup_timeout")

    def configuration_schema(self):
        from llm.core.schema import object_schema, field
        properties = {key: field(["integer", "null"], minimum=1,
                      **{"x-host-override": key in self._overrides}) for key in self._option_names}
        properties["buffer_size"]["type"] = "integer"
        properties["max_nested_depth"].update(minimum=0)
        for key in ("timeout_seconds", "cleanup_timeout"):
            properties[key] = field(["number", "null"] if key == "timeout_seconds" else "number", exclusiveMinimum=0, **{"x-host-override": key in self._overrides})
        return object_schema(properties, **{"x-runtime-configuration": ["workflow", "handlers", "revision", "config_keys"],
            **({"x-settings-key": self.settings_name} if self.settings_name else {})})

    def configuration(self, config, name, *, session_config=None):
        view = engine_configuration(config, self.settings_name or name, session_config=session_config,
            agent=self._agent_options, host=self._overrides, schema=self.configuration_schema())
        if "cleanup_timeout" not in view["values"]:
            view["enforced"] = {"cleanup_timeout": 5.0}
        return view

    def configured(self, context):
        """등록 인스턴스를 변경하지 않는 실행별 설정 사본. 중첩 실행에도 동일하게 적용한다."""
        if self._configured:
            return self
        values = self.configuration(context.project.config, context.run.engine, session_config=context.session.config)["values"]
        worker = copy(self)
        GraphEngine.__init__(worker, self.workflow, handlers=self.handlers, revision=self.revision,
            config_keys=self.config_keys, settings_name=self.settings_name,
            **{key: values[key] for key in self._option_names if key in values})
        worker._overrides, worker._agent_options, worker._configured = self._overrides, self._agent_options, True
        return worker

    def _execution_config(self, context):
        return {key: value for key, value in context.project.config.to_dict().items()
                if self.config_keys is None or key in self.config_keys
                or key in ("policies", "completion", "engines", "session_defaults", "component_configurations")}

    def _binding(self, context, graph, prepared):
        """코드의 버전은 개발자가 revision으로 관리한다. 저장 가능한 실행 설정은 직접 비교한다."""
        if not self._configured:
            return self.configured(context)._binding(context, graph, prepared)
        bindings, nested_graphs = [], []
        for engine, document, child_context in prepared:
            nested_graphs.append({"workflow_id": engine.workflow, "definition": document,
                "project_config": engine._execution_config(child_context),
                "revision": engine.revision, "handlers": sorted(engine.handlers),
                "max_steps": engine.max_steps, "max_parallelism": engine.max_parallelism,
                "max_nested_depth": engine.max_nested_depth, "timeout_seconds": engine.timeout_seconds,
                "cleanup_timeout": engine.cleanup_timeout, "config_keys": list(engine.config_keys) if engine.config_keys is not None else None})
            for node in self._nodes(document):
                bind_handler = getattr(engine.handlers.get(node["type"]), "binding", None)
                if bind_handler is not None:
                    bindings.append(bind_handler(deepcopy(node), child_context))
        Component.serialize({"bindings": bindings})
        return {"workflow": graph, "workflow_id": self.workflow, "revision": self.revision,
                "handler_bindings": bindings,
                "nested_graphs": nested_graphs,
                "handlers": sorted(self.handlers), "max_steps": self.max_steps,
                "max_parallelism": self.max_parallelism, "timeout_seconds": self.timeout_seconds,
                "project_config": self._execution_config(context), "session_config": context.session.config,
                "components": list(context.project.components), "agents": list(context.capabilities.get("agents", ())),
                "tools": context.tools.definitions(), "tool_contracts": context.tools.contracts(),
                "tool_policy": context.tool_scope.binding() if context.tool_scope else None}

    @staticmethod
    def _deadline(value) -> None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value <= 0):
            raise ValueError("Timeout must be positive and finite, or None")

    @staticmethod
    def _nodes(graph):
        for node in graph["nodes"].values():
            yield node
            if node["type"] == "loop":
                yield from GraphEngine._nodes(node["body"])

    def _definition(self, capabilities):
        candidates = [source["records"][self.workflow]
                      for source in capabilities["workflows"] if self.workflow in source["records"]]
        if len(candidates) != 1:
            raise GraphExecutionError("Workflow must resolve to exactly one definition")
        graph = deepcopy(candidates[0])
        validate_graph(graph)
        return graph

    def _nested_engine(self, node, capabilities):
        if node["type"] == "workflow":
            child = copy(self)
            child.workflow = node["workflow"]
            return child
        select = getattr(self.handlers.get(node["type"]), "graph_engine", None)
        engine = select(deepcopy(node), capabilities) if select is not None else None
        if engine is not None and not isinstance(engine, GraphEngine):
            raise TypeError("graph_engine must return GraphEngine or None")
        return engine

    def _walk(self, capabilities, context=None, ancestors=(), remaining=None):
        """참조 전체를 사전 탐색한다. 호출 위치별 정의를 유지하고 순환/깊이를 거부한다."""
        if context is not None and not self._configured:
            yield from self.configured(context)._walk(capabilities, context, ancestors, remaining)
            return
        # capability 탐색에는 Project 문맥이 없다. 실제 깊이는 문맥을 받은 사전 검증에서 제한한다.
        depth = self.max_nested_depth
        remaining = depth if remaining is None else remaining if depth is None else min(remaining, depth)
        if (remaining is not None and remaining < 0) or self.workflow in ancestors:
            raise GraphExecutionError("Cyclic Workflow reference or nested depth limit exceeded")
        if any(name not in capabilities for name in self.required_capabilities):
            if context is not None:
                raise GraphExecutionError("Nested Workflow capability is unavailable")
            yield self, None, None
            return
        graph = self._definition(capabilities)
        yield self, graph, context
        for node in self._nodes(graph):
            child = self._nested_engine(node, capabilities)
            if child is not None:
                child_context = context
                prepare = getattr(self.handlers.get(node["type"]), "graph_context", None)
                if context is not None and prepare is not None:
                    child_context = prepare(deepcopy(node), context)
                yield from child._walk(capabilities, child_context, (*ancestors, self.workflow), None if remaining is None else remaining - 1)

    def _prepare(self, context: EngineContext):
        """실행별 정의 스냅샷을 한 번 탐색해 검증과 재개 바인딩에 함께 사용한다.

        Run 사이에 캐시하지 않는다. 다음 실행/재개는 변경된 정의와 설정을 다시 검사한다.
        """
        prepared = list(self._walk(context.capabilities, context))
        for engine, document, child_context in prepared:
            for node in self._nodes(document):
                engine._deadline(node.get("timeout_seconds"))
                if node["type"] not in ("branch", "parallel", "join", "end", "loop", "workflow"):
                    handler = engine.handlers.get(node["type"])
                    if handler is None:
                        raise GraphExecutionError(f"Unregistered node type: {node['type']}")
                    validate = getattr(handler, "validate", None)
                    if validate is not None:
                        result = validate(deepcopy(node), child_context)
                        if inspect.isawaitable(result):
                            if inspect.iscoroutine(result):
                                result.close()
                            raise TypeError("Handler validate must be synchronous")
        return prepared[0][1], prepared

    def _instruction_routes(self, context, graph):
        """컴파일과 같은 구조 경로를 열거한다. 실행/반복 번호·표시 path는 예약 ID가 아니다.

        실행 스냅샷만 읽으며 조건 분기의 실제 선택은 예측하지 않는다. 방문하지 않은
        분기 예약은 Run 종료 시 unapplied로 남는다.
        """
        routes = []
        def region(engine, document, current, prefix, entry=None, stop=None):
            pending, seen = [document["entry"] if entry is None else entry], set()
            outgoing = {name: [] for name in document["nodes"]}
            for edge in document["edges"]:
                outgoing[edge["source"]].append(edge["target"])
            while pending:
                name = pending.pop()
                if name == stop or name in seen:
                    continue
                seen.add(name)
                node = document["nodes"][name]
                kind = node["type"]
                handler = engine.handlers.get(kind)
                describe = getattr(handler, "instruction_engine", None)
                consumer = describe(node, current) if describe is not None else None
                if consumer is not None:
                    routes.append(SteeringRoute(tuple(prefix), name, consumer))
                nested = engine._nested_engine(node, current.capabilities)
                if nested is not None:
                    prepare = getattr(handler, "graph_context", None)
                    child_context = prepare(node, current) if prepare is not None else current
                    region(nested, nested._definition(child_context.capabilities), child_context,
                           [*prefix, name, "workflow", nested.workflow])
                if kind == "loop":
                    region(engine, node["body"], current, [*prefix, name])
                if kind == "parallel":
                    for start in outgoing[name]:
                        region(engine, document, current, [*prefix, name, "branch", start], start, node["join"])
                    pending.append(node["join"])
                else:
                    pending.extend(outgoing[name])
        region(self, graph, context, [self.workflow])
        return routes

    # 공개 API
    checkpoint_name = "graph"
    steering_mode = SteeringMode.FORWARD

    def for_agent(self, definition: dict):
        """Agent가 지정한 Workflow를 부모 Graph 실행 범위에 연결한다."""
        options = deepcopy(definition.get("engine_options", {}))
        allowed = {"workflow", *self._option_names}
        if options.keys() - allowed:
            raise ValueError("Unsupported Graph Agent engine_options")
        # 모델 지침은 실제 LLM을 실행하는 하위 Agent에서 정의한다.
        if any(key in definition for key in ("completion", "system_prompt")) or any(
                definition.get("resources", {}).get(key) for key in ("skills", "mcp")):
            raise ValueError("Graph Agent model/Skill/MCP settings belong to its leaf Agents")
        worker = copy(self)
        workflow = options.pop("workflow", self.workflow)
        GraphEngine(workflow, handlers=self.handlers, **{**options, **self._overrides})
        worker.workflow = workflow
        worker._agent_options, worker._configured = options, False
        worker.settings_name = self.settings_name or definition["engine"]
        return worker

    def additional_capabilities(self, capabilities) -> tuple[str, ...]:
        """선택한 Workflow의 노드가 요청한 리소스만 서비스에서 구성한다."""
        names = []
        for engine, graph, _ in self._walk(capabilities):
            names.extend(engine.required_capabilities)
            if graph is not None:
                for node in self._nodes(graph):
                    request = getattr(engine.handlers.get(node["type"]), "additional_capabilities", None)
                    if request is not None:
                        extra = request(deepcopy(node), capabilities)
                        if not isinstance(extra, tuple) or any(not isinstance(name, str) or not name.strip() for name in extra):
                            raise TypeError("Handler additional_capabilities must return a tuple of names")
                        names.extend(extra)
        return tuple(dict.fromkeys(names))

    def validate_resume(self, checkpoint: dict, *, retry_nodes=(), context=None, decisions=None) -> None:
        """결과가 불확실한 처리 노드를 누락/중복 없이 명시적으로 승인해야 한다."""
        header, records = checkpoint["header"], checkpoint["records"]
        decisions = decisions or {}
        Component.serialize(decisions)
        if not isinstance(decisions, dict):
            raise ValueError("Resume decisions must be an object")
        for key, decision in decisions.items():
            saved = records.get(key, {})
            if saved.get("status") != "waiting" or not isinstance(decision, dict) or decision.keys() - {"approved", "state"}:
                raise ValueError("Decisions require a waiting node and approved/state fields")
            if "approved" in decision and type(decision["approved"]) is not bool:
                raise ValueError("approved must be boolean")
            if "state" in decision:
                if saved.get("node_type") == "engine_record":
                    raise ValueError("An Engine approval cannot edit its parent input state")
                schema = saved.get("resume_schema")
                if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
                    raise ValueError("State edits require an explicit closed resume_schema")
                validate_value({"resume_schema": schema}, "resume", decision["state"])
            if "interaction" in saved:
                InteractionRequest.from_dict(saved["interaction"]).select_decision(decision, confirm_empty=True)
        for key, saved in records.items():
            if saved.get("approval_required") and "approved" not in decisions.get(key, {}):
                raise ValueError(f"Tool approval decision required: {key}")
        if header.get("format") != "workflow-nodes-v1" or header["binding"]["workflow_id"] != self.workflow:
            raise ValueError("Checkpoint does not belong to this Workflow")
        if header["binding"]["revision"] != self.revision:
            raise ValueError("GraphEngine revision changed; start a new request")
        if context is not None:
            graph, prepared = self._prepare(context)
            if self._binding(context, graph, prepared) != header["binding"]:
                raise ValueError("Workflow, Agent, Tool or execution settings changed; start a new request")
        controls = {"branch", "parallel", "join", "loop", "end"}
        uncertain = {key for key, value in records.items()
                     if value["status"] == "started" and value["node_type"] not in controls
                     and not value.get("container", False)}
        if (any(not isinstance(key, str) for key in retry_nodes) or len(set(retry_nodes)) != len(retry_nodes)
                or set(retry_nodes) != uncertain):
            raise ValueError(f"Explicit retry_nodes must match uncertain node keys: {sorted(uncertain)}")
        if any(value["status"] not in ("started", "completed", "waiting") for value in records.values()):
            raise ValueError("Invalid checkpoint node status")

    async def execute(self, context: EngineContext):
        worker = self.configured(context)
        async with aclosing(worker._execute(context)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext):
        graph, prepared = self._prepare(context)
        routes = self._instruction_routes(context, graph) if context.steering is not None else ()
        binding = self._binding(context, graph, prepared)
        if context.checkpoint is not None:
            self.validate_resume(context.checkpoint, retry_nodes=context.run.metadata["resume"]["retry_nodes"],
                                 decisions=context.run.metadata["resume"].get("decisions"))
            header = deepcopy(context.checkpoint["header"])
            if binding != header["binding"]:
                raise GraphExecutionError("Workflow, Agent, Tool or execution settings changed; start a new request")
        else:
            message = next(item for item in context.messages if item.id == context.run.input_message_id)
            state = deepcopy(graph.get("initial_state", {}))
            if "inputs" in graph:
                state.update(bind(graph["inputs"], {"prompt": message.content, "message_id": message.id}))
            validate_value(graph, "input", state)
            header = {"format": "workflow-nodes-v1", "binding": binding, "initial_state": state,
                      "input_message_id": message.id, "message_ids": [item.id for item in context.messages]}
        Component.serialize(header)
        # 첫 LangGraph import도 UI 루프 밖에서 수행한다. 플러그인 import는 가볍게 유지한다.
        await asyncio.to_thread(importlib.import_module, "langgraph.graph")
        queue = asyncio.Queue(maxsize=self.buffer_size)

        async def emit(event):
            if not isinstance(event, EngineEvent):
                raise TypeError("Graph handlers emit EngineEvent objects")
            ack = asyncio.get_running_loop().create_future()
            await queue.put((deepcopy(event), ack))
            # 서비스가 이벤트를 저장하고 다음 이벤트를 요청한 뒤 작업을 계속한다.
            await ack

        async def produce():
            if context.checkpoint is None:
                await emit(EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "graph",
                    "operation": "initialize", "header": header, "records": {}}))
            async with aclosing(BaseEngine.declare_instruction_routes(context, routes)) as events:
                async for event in events:
                    await emit(event)
            root_id = new_id()
            await emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=root_id,
                                  kind="graph", name=self.workflow,
                                  metadata={"workflow_id": self.workflow, "definition": graph}))
            try:
                async with timeout(self.timeout_seconds):
                    state = deepcopy(header["initial_state"])
                    runtime = _WorkflowRuntime(self, context, emit, routes=routes)
                    state = await runtime.run(graph, state, self.workflow)
                    output = bind(graph["outputs"], state) if "outputs" in graph else deepcopy(state)
                    validate_value(graph, "output", output)
                    context.state["graph"] = deepcopy(output)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except _GraphPause as pause:
                await emit(EngineEvent(EngineEventType.PAUSED,
                    metadata={"checkpoint": "graph", "node_key": str(pause)}))
                return
            except Exception as error:
                await emit(BaseEngine.step_failed_event(root_id, error))
                raise
            await emit(BaseEngine.step_completed_event(context, root_id,
                EngineOutput(data=output, visibility="internal"),
                metadata={"visited_nodes": runtime.visited, "runtime": "langgraph"}))
            await emit(BaseEngine.output_event(context, EngineOutput(data=output)))

        producer = asyncio.create_task(produce())
        reader = None
        try:
            while True:
                if producer.done() and queue.empty():
                    await producer  # 내부 작업 실패를 RunManager로 전달한다.
                    break
                reader = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((reader, producer), return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    event, ack = reader.result()
                    reader = None
                    yield event
                    if not ack.done():
                        ack.set_result(None)
                else:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                    reader = None
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
