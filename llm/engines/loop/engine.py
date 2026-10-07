"""LiteLLM completion 스트림과 Tool 호출을 반복하는 실행 전략. tools capability를 명시적으로 요청하고 설정은 매 Run마다 복사한다.

A BaseEngine subclass: complete, execute requested tools, then repeat."""

from llm.providers.schema import completion_schema, validate_model_params

import math
from itertools import count
import asyncio
from copy import copy, deepcopy
from dataclasses import replace
from llm.core.interactions import InteractionRequest
import json
import hashlib
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, Optional, Union

from contextlib import aclosing
from llm.components.processing import CompletionMessage, CompletionRequest, CompletionObservation, CompletionPipeline
from llm.core.models import MessageRole, MessageStatus
from llm.core.steering import is_instruction, SteeringMode, validate_instruction_record
from llm.core.configuration import resolve_engine_config, UNSET
from llm.core.parameters import ParameterLayout
from llm.core.results import EngineOutput
from llm.providers.litellm import completion
from llm.providers.parameters import merge_params
from llm.services.runtime.tools import ToolExecutor, ToolExecutionError, ToolApprovalRequired
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.policies import CompletionPolicy
from llm.providers.requests import provider_schema, resolve_provider_options


# 모델 응답과 Tool 실행을 반복한다. 동시 Run의 설정을 서로 격리한다.
class LoopEngine(BaseEngine):
    """LiteLLM completion/tool loop with runtime kwargs and optional preparation inputs."""

    required_capabilities = ("tools", "completion_processors")
    checkpoint_name = "loop"
    steering_mode = SteeringMode.CONSUME

    def additional_capabilities(self, capabilities):
        """선택 Tool의 공개 runtime dependency 선언만 전달한다."""
        names = []
        tools = capabilities.get("tools")
        for name in tools.names() if tools is not None else ():
            handler = tools.get(name).handler
            if getattr(handler, "resumable_container", False) is not True:
                continue
            names.extend(getattr(handler, "required_capabilities", ()))
            describe = getattr(handler, "additional_capabilities", None)
            if describe is not None:
                names.extend(describe(capabilities))
        return tuple(dict.fromkeys(names))

    def _binding(self, context):
        """설정·Tool 계약을 비교한다. 실행 함수 교체 시에는 프로젝트 engine revision을 변경한다."""
        def portable(value):
            if isinstance(value, dict):
                return {k: portable(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [portable(v) for v in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return {"runtime_type": type(value).__module__ + "." + type(value).__qualname__}
        # Persisted binding vocabulary stays byte-compatible; these are not runtime API aliases.
        values = portable({"settings": context.resolve_config(self.parameter_key),
                         "completion": self.completion_kwargs,
                         "prompt": self.system_prompt, "agent": self._agent_options, "tools": context.tools.definitions(),
                         "tool_contracts": context.tools.contracts(),
                         "tool_policy": context.tool_scope.binding() if context.tool_scope else None})
        return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def validate_resume(self, checkpoint, *, retry_nodes=(), context=None, decisions=None):
        """완료 Tool은 재사용하고 효과가 불확실한 Tool만 명시적 재실행 승인을 받는다."""
        if checkpoint["header"].get("format") != "loop-iterations-v1":
            raise ValueError("Checkpoint does not belong to LoopEngine")
        for key, value in checkpoint["records"].items():
            if key.startswith("steering:"):
                validate_instruction_record(value)
        uncertain = {key for key, value in checkpoint["records"].items()
                     if value["status"] == "started" and value.get("requires_retry", key.startswith("tool:"))}
        if len(set(retry_nodes)) != len(retry_nodes) or set(retry_nodes) != uncertain:
            raise ValueError(f"Explicit retry_nodes must match uncertain Tool keys: {sorted(uncertain)}")
        if context is not None and checkpoint["header"]["binding"] != self._binding(context):
            raise ValueError("Loop configuration or Tool definitions changed; start a new request")
        decisions = decisions or {}
        waiting = {key for key, value in checkpoint["records"].items() if value["status"] == "waiting"}
        if set(decisions) - waiting:
            raise ValueError("Loop decisions require waiting checkpoint keys")
        if waiting - decisions.keys():
            raise ValueError(f"Tool approval decisions required: {sorted(waiting)}")
        for key in waiting:
            saved = checkpoint["records"][key]
            if "interaction" in saved:
                InteractionRequest.from_dict(saved["interaction"]).select_decision(decisions[key])

    def _record(self, key, value):
        interaction = InteractionRequest.from_dict(value["interaction"]) if "interaction" in value else None
        return EngineEvent(EngineEventType.CHECKPOINT, interaction=interaction, metadata={"name": "loop", "operation": "record",
                                                               "key": key, "value": deepcopy(value)})

    parameter_layout = ParameterLayout(
        config=("completion", "system_prompt", "buffer_size"),
        policy=("max_iterations", "request_timeout", "tool_timeout", "max_tool_calls",
                "max_argument_chars", "max_output_chars", "provider"),
        paths={"input_policy": "policy.completion"})

    _option_names = ("max_iterations", "request_timeout", "tool_timeout", "buffer_size",
                     "max_tool_calls", "max_argument_chars", "max_output_chars")

    def __init__(self, *, parameter_key=None, completion_fn=completion) -> None:
        """Host는 구현을 등록한다. 실행값은 Project/Session/Agent 설정에서만 읽는다."""
        if parameter_key is not None and (not isinstance(parameter_key, str) or not parameter_key.strip()):
            raise ValueError("parameter_key must be a nonempty string or None")
        self.parameter_key, self._agent_options = parameter_key, {}
        self.completion_fn = completion_fn
        self._configure({})

    def _configure(self, values):
        """검증된 실행별 값으로 사본을 준비한다. 저장/공개 설정에 기본값을 만들지 않는다."""
        max_iterations, request_timeout, tool_timeout, max_tool_calls, max_argument_chars, max_output_chars = (
            values.get(key) for key in self._option_names if key != "buffer_size")
        # Queue capacity changes backpressure only; it never drops output or limits execution.
        buffer_size = values.get("buffer_size", 8)
        self.input_policy = deepcopy(values.get("input_policy"))
        self.provider = deepcopy(values.get("provider"))
        super().__init__("Loop", completion_fn=self.completion_fn, buffer_size=buffer_size)
        self.max_tool_calls, self.max_argument_chars, self.max_output_chars = max_tool_calls, max_argument_chars, max_output_chars
        if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
            raise ValueError("max_iterations must be a positive integer")
        for value in (request_timeout, tool_timeout):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Timeouts must be positive and finite")
        self.max_iterations = max_iterations
        self.request_timeout = request_timeout
        self.tool_timeout = tool_timeout
        self.completion_kwargs = self.copy_params(values.get("completion", {}))
        self.system_prompt = values.get("system_prompt")

    def describe_config(self):
        from llm.core.schema import object_schema, field
        names = self._option_names
        properties = {name: field((["number", "null"] if name.endswith("timeout") else "integer" if name == "buffer_size" else ["integer", "null"]), exclusiveMinimum=0) for name in names}
        properties["system_prompt"] = {"type": ["string", "null"], "description": "시스템 프롬프트"}
        properties["completion"] = completion_schema()
        properties["input_policy"] = CompletionPolicy.describe_config()
        properties["provider"] = {**provider_schema(), "type": ["object", "null"]}
        for key in self._option_names:
            if key != "buffer_size":
                properties[key]["x-narrowing"] = "maximum"
        return self.parameter_layout.schema(object_schema(properties, **({"x-parameter-key": self.parameter_key} if self.parameter_key else {})))

    def resolve_config(self, config, name, *, session_config=None):
        """실행과 UI가 공유하는 Project → Session → Agent 설정."""
        key = self.parameter_key or name
        agent = self._agent_options
        overrides = self.parameter_layout.unpack(agent.get("engine_options", {}))
        if "system_prompt" in agent:
            overrides["system_prompt"] = agent["system_prompt"]
        if agent.get("completion"):
            overrides["completion"] = agent["completion"]
        view = resolve_engine_config(config, key, session_config=session_config,
            agent=self.parameter_layout.pack(overrides), schema=self.describe_config())
        CompletionPolicy.validate_config(view["values"].get("policy", {}).get("completion"))
        resolve_provider_options(view["values"].get("policy", {}).get("provider") or {})
        view["runtime"] = []
        return view

    def _request(self, context: EngineContext, params: dict[str, Any]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "stream": True,
        }
        request.update(self.copy_params(params))
        validate_model_params(request, require_model=True, json_contract=False)
        definitions = context.tools.definitions(constraints=context.tool_scope.argument_constraints if context.tool_scope else None)
        if definitions:
            request["tools"] = definitions
            if getattr(self, "_require_tool", False) and not context.tool_scope.completed:
                request["tool_choice"] = "required"
        return request

    def for_agent(self, definition: dict):
        """업무 정의를 적용한 호출별 엔진. 등록된 객체와 provider 핸들은 변경하지 않는다.

        다른 엔진도 for_agent(definition)와 execute(context)를 제공하여 AgentNode에
        등록할 수 있다. 준비 작업을 구현한 Loop 서브클래스의 메서드도 유지한다.
        """
        params = self.copy_params(definition.get("completion", {}))
        # Agent는 부분 설정이다. 누락된 model은 Project/Session에서 상속한다.
        # 모든 계층에 없으면 최종 request 검증에서 provider 호출 전에 거부한다.
        validate_model_params(params)
        if any(key in params for key in ("messages", "tools", "functions", "function_call")):
            raise ValueError("Loop Agent owns messages and registered tools")
        if params.get("stream", True) is not True or params.get("n", 1) != 1:
            raise ValueError("Loop Agent requires stream=True and n=1")
        options = definition.get("engine_options", {})
        from jsonschema import Draft202012Validator
        Draft202012Validator(self.describe_config()).validate(options)
        copy(self)._configure(self.parameter_layout.unpack(options))
        worker = copy(self)
        worker._agent_options = {"engine_options": deepcopy(options), "completion": params,
                                  **({"system_prompt": definition["system_prompt"]} if "system_prompt" in definition else {})}
        worker.parameter_key = self.parameter_key or definition["engine"]
        worker._require_tool = definition.get("policy", {}).get("require_tool", False)
        return worker

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        resolved = self.parameter_layout.unpack(self.resolve_config(context.project.config, context.run.engine, session_config=context.session.config)["values"])
        # A Run-local instance keeps shared defaults immutable and preserves
        # subclass methods. Only Loop-owned configuration are reinitialized.
        worker = copy(self)
        worker._resume_binding = self._binding(context)
        worker._configure(resolved)
        # 다른 Agent/Engine의 입력 예산을 상속하지 않는다. 처리기는 이 호출의 선택기만 전달받는다.
        context = replace(context, completion_policy=CompletionPolicy.from_config(
            resolved.get("input_policy"), context.token_counters))
        async with aclosing(worker._execute(context)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        # Evaluate factories after preparation, once per Run. No shared per-Run
        # state lives on the Engine; each provider call gets fresh containers.
        supplied = (self.completion_kwargs(context) if callable(self.completion_kwargs)
                    else self.completion_kwargs)
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, Mapping) or any(not isinstance(key, str) for key in supplied):
            raise ValueError("Completion parameters must be a string-keyed mapping")
        params = self.copy_params(dict(supplied))
        # These fields define the Loop transcript/tool execution contract.
        if any(key in params for key in ("messages", "tools", "functions", "function_call")):
            raise ValueError("Loop owns messages and registered tool definitions")
        if params.get("stream", True) is not True or params.get("n", 1) != 1:
            raise ValueError("Loop requires stream=True and n=1")
        prompt = self.system_prompt(context) if callable(self.system_prompt) else self.system_prompt
        if prompt is not None and not isinstance(prompt, str):
            raise ValueError("System prompt must be a string")
        messages = [CompletionMessage({"role": message.role.value, "content": message.content}, message.id, is_instruction(message))
                    for message in context.messages
                    if (message.role == MessageRole.USER and message.status == MessageStatus.COMMITTED)
                    or (message.role != MessageRole.USER and message.status in (
                        MessageStatus.COMPLETED, MessageStatus.INTERRUPTED, MessageStatus.PAUSED, MessageStatus.FAILED))]
        if prompt:
            messages.insert(0, CompletionMessage({"role": "system", "content": prompt}))
        if context.checkpoint is not None:
            self.validate_resume(context.checkpoint,
                retry_nodes=context.run.metadata.get("resume", {}).get("retry_nodes", ()),
                decisions=context.run.metadata.get("resume", {}).get("decisions"))
            if context.checkpoint["header"]["binding"] != self._resume_binding:
                raise ValueError("Loop configuration or Tool definitions changed; start a new request")
        if (context.output_step_id is None or context.checkpoint_scope is not None) and context.checkpoint is None:
            yield EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "loop", "operation": "initialize",
                "header": {"format": "loop-iterations-v1", "binding": self._resume_binding,
                           "message_ids": [m.id for m in context.messages],
                           "input_message_id": context.run.input_message_id}})
        async with aclosing(self.open_instructions(context)) as events:
            async for event in events:
                yield event
        # 도메인별 정책/저장은 처리기가 소유한다. 각 호출의 세션은 다른 Run/Agent와 공유하지 않는다.
        async with CompletionPipeline(context.capabilities.get("completion_processors", ()), context) as processors:
            async with aclosing(self._iterate(context, params, messages, processors)) as events:
                async for event in events:
                    yield event

    async def _iterate(self, context, params, messages, processors):
        """반복마다 입력 사본을 만들고 관찰 훅을 Tool 효과보다 먼저 처리한다."""
        seen_call_ids: set[str] = set()
        durable = context.output_step_id is None or context.checkpoint_scope is not None
        records = deepcopy(context.checkpoint["records"]) if durable and context.checkpoint else {}
        inbox = context.steering
        for iteration in count(1):
            if self.max_iterations is not None and iteration > self.max_iterations:
                raise RuntimeError("Loop iteration limit exceeded")
            instruction_key = f"steering:{iteration}"
            if inbox is not None:
                if instruction_key not in records:
                    async with aclosing(self.select_instructions(context, checkpoint=self.checkpoint_name,
                                                                  boundary=instruction_key)) as events:
                        async for event in events:
                            yield event
                    records[instruction_key] = {"status": "input", "message_ids": [m.id for m in inbox.messages]}
                selected = [inbox.history[i] for i in records[instruction_key]["message_ids"]]
                messages.extend(CompletionMessage({"role": "user", "content": m.content}, m.id, True) for m in selected)
            saved = records.get(f"iteration:{iteration}", {})
            response: dict[str, Any] = deepcopy(saved.get("response", {}))
            calls, prepared = [], []
            request = CompletionRequest(self._request(context, params),
                [CompletionMessage(self.copy_params(m.value), m.source_id, m.continuation) for m in messages], iteration)
            if not saved:
                async with aclosing(processors.prepare(request, messages)) as events:
                    async for event in events:
                        yield event
            prepared_request = request.to_kwargs()

            async def complete(_context):
                nonlocal calls, prepared, prepared_request
                if context.completion_policy is not None:
                    if any(m.continuation for m in messages):
                        prepare_turn = getattr(context.completion_policy, "prepare_turn", None)
                        if not callable(prepare_turn):
                            raise ValueError("Completion policy must support prepare_turn for live instructions")
                        current_index = next(i for i, m in enumerate(request.messages) if m.source_id == context.run.input_message_id)
                        continued_ids = {m.source_id for m in messages if m.continuation}
                        starts = [i for i, m in enumerate(request.messages[:current_index])
                                  if m.value.get("role") == "user" and m.source_id not in continued_ids]
                        prepared_request = await asyncio.to_thread(prepare_turn, prepared_request, current_index, starts)
                    else:
                        prepared_request = await asyncio.to_thread(context.completion_policy.prepare, prepared_request)
                if inbox is not None and records[instruction_key]["message_ids"]:
                    async with aclosing(self.apply_instructions(context, checkpoint=self.checkpoint_name,
                            boundary=instruction_key, details={"iteration": iteration})) as events:
                        async for event in events:
                            yield event
                async with aclosing(self.stream_completion(prepared_request, response=response, provider=self.provider,
                        max_tool_calls=self.max_tool_calls, max_argument_chars=self.max_argument_chars,
                        max_output_chars=self.max_output_chars)) as deltas:
                    async for text in deltas:
                        yield text
                calls = response.get("tool_calls", [])
                if calls and iteration == self.max_iterations:
                    raise ValueError("Loop iteration limit reached")
                if any(call["id"] in seen_call_ids for call in calls):
                    raise ValueError("Repeated tool call ID")
                # Validate the whole batch before any tool can have side effects.
                prepared = [context.tools.prepare(
                    call["function"]["name"], call["function"]["arguments"],
                    constraints=context.tool_scope.argument_constraints if context.tool_scope else None,
                ) for call in calls]

            if not saved:
                async with aclosing(self.step(
                    context, complete, name="LLM completion", kind="llm",
                    timeout_seconds=self.request_timeout, metadata={"iteration": iteration},
                    error_message="LLM iteration failed",
                )) as events:
                    async for event in events:
                        yield event
                # after_completion 검증 실패를 성공한 회차로 저장하지 않는다.
            else:
                calls = response.get("tool_calls", [])
                prepared = [context.tools.prepare(c["function"]["name"], c["function"]["arguments"],
                    constraints=context.tool_scope.argument_constraints if context.tool_scope else None) for c in calls]
            observation = CompletionObservation(iteration, prepared_request, response,
                                                [m.value for m in messages])
            if not saved:
                async with aclosing(processors.after_completion(observation)) as events:
                    async for event in events:
                        yield event
                if durable:
                    yield self._record(f"iteration:{iteration}", {"status": "responded", "response": response})
            if not calls:
                if inbox is not None:
                    next_key = f"steering:{iteration + 1}"
                    if next_key not in records:
                        allowed = self.max_iterations is None or iteration < self.max_iterations
                        async with aclosing(self.select_instructions(context, checkpoint=self.checkpoint_name,
                                boundary=next_key, final=True, allow_continue=allowed,
                                reason=None if allowed else "iteration_limit")) as events:
                            async for event in events:
                                yield event
                        following = [m.id for m in inbox.messages]
                        if following:
                            records[next_key] = {"status": "input", "message_ids": following}
                    else:
                        following = records[next_key]["message_ids"]
                    if following:
                        messages.append(CompletionMessage(response))
                        continue
                async with aclosing(processors.finish(observation)) as events:
                    async for event in events:
                        yield event
                yield self.output_event(context, EngineOutput(text=response.get("content") or ""))
                return
            messages.append(CompletionMessage(response))
            for call, (tool, arguments) in zip(calls, prepared):
                seen_call_ids.add(call["id"])
                key = f"tool:{iteration}:{call['id']}"
                stored = records.get(key, {})
                result = deepcopy(stored.get("result", {}))
                executor = ToolExecutor(timeout_seconds=self.tool_timeout, max_output_chars=self.max_output_chars)
                if stored.get("status") != "completed":
                    container = getattr(tool.handler, "resumable_container", False) is True
                    if durable:
                        yield self._record(key, {"status": "started", "requires_retry": not container,
                                                "container": container, "name": tool.name, "arguments": arguments})
                    try:
                        outer_step = None
                        async with aclosing(self.execute_tool(
                            context, tool, arguments, result=result, executor=executor,
                            metadata={"iteration": iteration, "tool_call_id": call["id"], "checkpoint_name": "loop"},
                            checkpoint_key=key,
                        )) as events:
                            async for event in events:
                                if outer_step is None and event.type == EngineEventType.STEP_STARTED:
                                    outer_step = event.step_id
                                if durable and event.type == EngineEventType.STEP_COMPLETED and event.step_id == outer_step:
                                    yield self._record(key, {"status": "completed", "result": result,
                                                           "step_id": event.step_id, "run_id": context.run.id})
                                yield event
                                if event.type == EngineEventType.PAUSED:
                                    return
                    except ToolApprovalRequired as error:
                        if not durable:
                            raise
                        yield self._record(key, {"status": "waiting", "name": tool.name,
                                                "arguments": arguments, "prompt": str(error),
                                                "interaction": error.request.bind("loop", key).to_dict()})
                        yield EngineEvent(EngineEventType.PAUSED, metadata={"checkpoint": "loop", "tool_key": key})
                        return
                    except ToolExecutionError as error:
                        if durable and error.effect == "none":
                            yield self._record(key, {"status": "not_applied", "error": str(error)})
                        raise
                else:
                    # 재사용도 새 Run에서 관찰·원본 조회할 수 있게 기록한다. 핸들러는 호출하지 않는다.
                    async def reuse(_context):
                        return EngineOutput(data=deepcopy(result["value"]), visibility="internal")
                    async with aclosing(self.step(context, reuse, name=tool.name, kind="tool",
                            metadata={"iteration": iteration, "tool_call_id": call["id"], "reused": True,
                                      "source_run_id": stored["run_id"], "source_step_id": stored["step_id"]})) as events:
                        async for event in events:
                            yield event
                messages.append(CompletionMessage({"role": "tool", "tool_call_id": call["id"],
                                 "name": tool.name, "content": result["content"]}))
            if durable:
                yield self._record(f"iteration:{iteration}", {"status": "completed", "response": response})
