"""LiteLLM completion 스트림과 Tool 호출을 반복하는 실행 전략. tools capability를 명시적으로 요청하고 설정은 매 Run마다 복사한다.

A BaseEngine subclass: complete, execute requested tools, then repeat."""

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
from urllib.parse import urlsplit

from contextlib import aclosing
from llm.components.processing import CompletionMessage, CompletionRequest, CompletionObservation, CompletionPipeline
from llm.core.models import MessageRole, MessageStatus
from llm.core.steering import is_instruction, SteeringMode, validate_instruction_record
from llm.core.configuration import engine_configuration, UNSET
from llm.core.settings import SettingsLayout
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
        values = portable({"settings": context.settings(self.settings_name),
                         "overrides": self._overrides, "completion": self.completion_kwargs,
                         "prompt": self.system_prompt, "agent": self._agent_settings, "tools": context.tools.definitions(),
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
                     if key.startswith("tool:") and value["status"] == "started"}
        if len(set(retry_nodes)) != len(retry_nodes) or set(retry_nodes) != uncertain:
            raise ValueError(f"Explicit retry_nodes must match uncertain Tool keys: {sorted(uncertain)}")
        if context is not None and checkpoint["header"]["binding"] != self._binding(context):
            raise ValueError("Loop settings or Tool definitions changed; start a new request")
        decisions = decisions or {}
        waiting = {key for key, value in checkpoint["records"].items() if value["status"] == "waiting"}
        if set(decisions) - waiting or any(type(v) is not bool for v in decisions.values()):
            raise ValueError("Loop decisions must be booleans for waiting Tool keys")
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

    settings_layout = SettingsLayout(
        config=("completion", "system_prompt", "buffer_size"),
        policy=("max_iterations", "request_timeout", "tool_timeout", "max_tool_calls",
                "max_argument_chars", "max_output_chars", "provider"),
        paths={"input_policy": "policy.completion"})

    _option_names = ("max_iterations", "request_timeout", "tool_timeout", "buffer_size",
                     "max_tool_calls", "max_argument_chars", "max_output_chars")

    def __init__(self, *, max_iterations=UNSET, request_timeout=UNSET, tool_timeout=UNSET,
                 buffer_size=UNSET, max_tool_calls=UNSET, max_argument_chars=UNSET,
                 max_output_chars=UNSET, completion_kwargs=None, system_prompt=UNSET,
                 settings_name=None, completion_fn=completion, input_policy=UNSET, provider=UNSET) -> None:
        supplied = dict(max_iterations=max_iterations, request_timeout=request_timeout,
                        tool_timeout=tool_timeout, buffer_size=buffer_size, max_tool_calls=max_tool_calls,
                        max_argument_chars=max_argument_chars, max_output_chars=max_output_chars)
        self._overrides = {key: value for key, value in supplied.items() if value is not UNSET}
        max_iterations, request_timeout, tool_timeout, max_tool_calls, max_argument_chars, max_output_chars = (
            self._overrides.get(key) for key in self._option_names if key != "buffer_size")
        # Queue capacity changes backpressure only; it never drops output or limits execution.
        buffer_size = 8 if buffer_size is UNSET else buffer_size
        if system_prompt is not UNSET and not callable(system_prompt):
            self._overrides["system_prompt"] = system_prompt
        system_prompt = None if system_prompt is UNSET else system_prompt
        from jsonschema import Draft202012Validator
        from llm.core.models import ProjectConfig
        for name, value, spec in (("input_policy", input_policy, CompletionPolicy.configuration_schema()),
                                 ("provider", provider, {**provider_schema(), "type": ["object", "null"]})):
            if value is not UNSET:
                ProjectConfig.validate_settings({name: value})
                error = next(Draft202012Validator(spec).iter_errors(value), None)
                if error:
                    raise ValueError(f"Invalid {name}: {error.message}")
                self._overrides[name] = deepcopy(value)
        self.input_policy = None if input_policy is UNSET else deepcopy(input_policy)
        self.provider = None if provider is UNSET else deepcopy(provider)
        super().__init__("Loop", completion_fn=completion_fn, buffer_size=buffer_size,
                         max_tool_calls=max_tool_calls, max_argument_chars=max_argument_chars,
                         max_output_chars=max_output_chars)
        if max_iterations is not None and (type(max_iterations) is not int or max_iterations < 1):
            raise ValueError("max_iterations must be a positive integer")
        for value in (request_timeout, tool_timeout):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Timeouts must be positive and finite")
        self.max_iterations = max_iterations
        self.request_timeout = request_timeout
        self.tool_timeout = tool_timeout
        if completion_kwargs is not None and not (
            isinstance(completion_kwargs, Mapping) or callable(completion_kwargs)
        ):
            raise TypeError("completion_kwargs must be a mapping or context factory")
        if system_prompt is not None and not isinstance(system_prompt, str) and not callable(system_prompt):
            raise TypeError("system_prompt must be a string or context factory")
        self.completion_kwargs = (self.copy_params(dict(completion_kwargs))
                                  if isinstance(completion_kwargs, Mapping) else completion_kwargs)
        self.system_prompt = system_prompt
        if settings_name is not None and (not isinstance(settings_name, str) or not settings_name.strip()):
            raise ValueError("settings_name must be a nonempty string or None")
        self.settings_name = settings_name
        self._agent_settings = {}

    def configuration_schema(self):
        from llm.core.schema import object_schema, field, completion_schema, mark_host_overrides
        names = self._option_names
        properties = {name: field((["number", "null"] if name.endswith("timeout") else "integer" if name == "buffer_size" else ["integer", "null"]), exclusiveMinimum=0, **{"x-host-override": name in self._overrides}) for name in names}
        properties["system_prompt"] = {"type": ["string", "null"], "description": "기본 시스템 프롬프트",
                                        "x-host-override": "system_prompt" in self._overrides or callable(self.system_prompt)}
        properties["completion"] = completion_schema()
        properties["input_policy"] = CompletionPolicy.configuration_schema()
        properties["provider"] = {**provider_schema(), "type": ["object", "null"]}
        for name in ("input_policy", "provider"):
            if name in self._overrides:
                mark_host_overrides(properties[name], self._overrides[name])
        supplied = self.completion_kwargs
        if isinstance(supplied, Mapping):
            mark_host_overrides(properties["completion"], supplied)
        elif callable(supplied):
            properties["completion"]["x-host-override"] = True
        return self.settings_layout.schema(object_schema(properties, **({"x-settings-key": self.settings_name} if self.settings_name else {})))

    def configuration(self, config, name, *, session_config=None):
        """실행과 UI가 공유하는 최종 설정. 동적 호스트 함수는 미리 실행하지 않는다."""
        from llm.core.models import ProjectConfig
        key = self.settings_name or name
        agent = self._agent_settings
        host = dict(self._overrides)
        if isinstance(self.system_prompt, str):
            host["system_prompt"] = self.system_prompt
        supplied, runtime = self.completion_kwargs, []
        try:
            ProjectConfig.validate_settings(dict(supplied or {}))
        except (TypeError, ValueError):
            supplied, runtime = {}, ["completion"]
        if supplied:
            host["completion"] = dict(supplied)
        overrides = self.settings_layout.unpack(agent.get("engine_options", {}))
        if "system_prompt" in agent:
            overrides["system_prompt"] = agent["system_prompt"]
        if agent.get("completion"):
            overrides["completion"] = agent["completion"]
        view = engine_configuration(config, key, session_config=session_config,
            agent=self.settings_layout.pack(overrides), host=self.settings_layout.pack(host), schema=self.configuration_schema())
        CompletionPolicy.validate_settings(view["values"].get("policy", {}).get("completion"))
        resolve_provider_options(view["values"].get("policy", {}).get("provider") or {})
        if runtime:
            # 런타임 client/함수를 JSON 값으로 가장하지 않는다. 실행 때만 host 값을 해석한다.
            view["values"].get("config", {}).pop("completion", None)
            for name in ("sources", "editable"):
                for path in tuple(view[name]):
                    if path.startswith("/config/completion/"):
                        del view[name][path]
            view["sources"]["/config/completion"] = "host_runtime"
            view["editable"]["/config/completion"] = False
        if callable(self.system_prompt):
            runtime.append("system_prompt")
            view["values"].get("config", {}).pop("system_prompt", None)
            view["sources"]["/config/system_prompt"] = "host_runtime"
            view["editable"]["/config/system_prompt"] = False
        view["runtime"] = runtime
        return view

    def _request(self, context: EngineContext, params: dict[str, Any]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "stream": True,
        }
        request.update(self.copy_params(params))
        if not isinstance(request.get("model"), str) or not request["model"].strip():
            raise ValueError("Completion model is required")
        if request.get("api_base") is not None:
            url = urlsplit(request["api_base"])
            if (url.scheme not in ("http", "https") or not url.hostname):
                raise ValueError("api_base must be an HTTP URL")
        definitions = context.tools.definitions()
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
        # Agent는 부분 설정이다. 누락된 model은 Project/Session/host에서 상속한다.
        # 모든 계층에 없으면 최종 request 검증에서 provider 호출 전에 거부한다.
        if "model" in params and (not isinstance(params["model"], str) or not params["model"].strip()):
            raise ValueError("Agent completion.model must be nonempty text")
        if any(key in params for key in ("messages", "tools", "functions", "function_call")):
            raise ValueError("Loop Agent owns messages and registered tools")
        if params.get("stream", True) is not True or params.get("n", 1) != 1:
            raise ValueError("Loop Agent requires stream=True and n=1")
        limits = {}
        options = definition.get("engine_options", {})
        from jsonschema import Draft202012Validator
        Draft202012Validator(self.configuration_schema()).validate(options)
        limits.update(self.settings_layout.unpack(options))
        if "completion" in limits:
            limits["completion_kwargs"] = limits.pop("completion")
        limits.update(self._overrides)
        # 등록 시 잘못된 Agent 옵션을 거부하되, 기본값을 호스트 override로 바꾸지는 않는다.
        LoopEngine(**limits)
        worker = copy(self)
        worker._agent_settings = {"engine_options": deepcopy(options), "completion": params,
                                  **({"system_prompt": definition["system_prompt"]} if "system_prompt" in definition else {})}
        worker.settings_name = self.settings_name or definition["engine"]
        worker._require_tool = definition.get("policy", {}).get("require_tool", False)
        return worker

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        settings = context.settings(self.settings_name)
        supplied = self.completion_kwargs(context) if callable(self.completion_kwargs) else self.completion_kwargs
        if supplied is not None and (not isinstance(supplied, Mapping)
                                     or any(not isinstance(key, str) for key in supplied)):
            raise ValueError("Completion parameters must be a string-keyed mapping")
        params = merge_params(settings["engine"].get("config", {}).get("completion", {}),
                              self._agent_settings.get("engine_options", {}).get("config", {}).get("completion", {}))
        params = merge_params(params, self._agent_settings.get("completion", {}))
        params = merge_params(params, dict(supplied or {}))
        resolved = self.settings_layout.unpack(self.configuration(context.project.config, context.run.engine, session_config=context.session.config)["values"])
        params = merge_params(resolved.get("completion", params), dict(supplied or {}))
        limits = {name: resolved[name] for name in (*self._option_names, "input_policy", "provider") if name in resolved}
        prompt = self.system_prompt if self.system_prompt is not None else resolved.get("system_prompt")
        # A Run-local instance keeps shared defaults immutable and preserves
        # subclass methods. Only Loop-owned settings are reinitialized.
        worker = copy(self)
        worker._resume_binding = self._binding(context)
        LoopEngine.__init__(worker, **limits, completion_kwargs=params,
                            system_prompt=prompt, settings_name=self.settings_name,
                            completion_fn=self.completion_fn)
        # 다른 Agent/Engine의 입력 예산을 상속하지 않는다. 처리기는 이 호출의 선택기만 전달받는다.
        context = replace(context, completion_policy=CompletionPolicy.from_settings(
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
                raise ValueError("Loop settings or Tool definitions changed; start a new request")
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
                async with aclosing(self.stream_completion(prepared_request, response=response, provider=self.provider)) as deltas:
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
                prepared = [context.tools.prepare(c["function"]["name"], c["function"]["arguments"]) for c in calls]
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
                    if durable:
                        yield self._record(key, {"status": "started", "requires_retry": True, "name": tool.name, "arguments": arguments})
                    try:
                        async with aclosing(self.execute_tool(
                            context, tool, arguments, result=result, executor=executor,
                            metadata={"iteration": iteration, "tool_call_id": call["id"]},
                            checkpoint_key=key,
                        )) as events:
                            async for event in events:
                                if durable and event.type == EngineEventType.STEP_COMPLETED:
                                    yield self._record(key, {"status": "completed", "result": result,
                                                           "step_id": event.step_id, "run_id": context.run.id})
                                yield event
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
