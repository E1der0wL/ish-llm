"""Agent 업무 정의를 등록 엔진에 연결한다. 모든 실행은 부모 Run 안에서 관찰된다."""

import asyncio
import json
from contextlib import AsyncExitStack, aclosing
from copy import deepcopy
from dataclasses import replace

from jsonschema import Draft202012Validator

from asyncio import timeout
from llm.components.agents import AgentComponent
from llm.components.base import Component
from llm.components.tools import Tool, ToolRegistry
from llm.core.models import new_id
from llm.services.infrastructure.storage import revision_token
from llm.core.results import EngineOutput
from llm.services.runtime.tools import ToolExecutionScope, ToolPolicy
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType, required_capabilities, steering_mode
from llm.core.steering import SteeringMode
from llm.core.schema import object_schema, field
from llm.engines.registry import EngineRegistry
from .engine import GraphEngine, _GraphPause
from .checkpoints import EngineCheckpointScope


class AgentNode:
    """저장된 업무를 Graph 노드로 실행한다.

    engines의 각 엔진은 동기 for_agent(definition)으로 호출별 실행기를 반환한다.
    LoopEngine은 이 계약을 제공한다. 커스텀 엔진도 같은 이벤트/ToolExecutor 계약을
    지켜야 한다. GraphEngine은 부모 GraphNodeContext의 중첩 실행기를 사용한다.
    입력은 노드의 inputs, 출력은 {text, data?}이며 Agent schema도 이 계약에 적용한다.
    """

    required_capabilities = ("agents", "tools")
    # Graph 모드의 준비/매핑은 외부 효과를 수행하지 않고 자식 작업만 조율한다.
    resumable_container = True

    @staticmethod
    def configuration_schema():
        return object_schema({"agent": field("string", minLength=1), "emit_text": field("boolean"),
                              "output_format": field("string", enum=["text", "json"])}, required=["agent"])

    def __init__(self, *, engines, revision: str = "1"):
        if not isinstance(revision, str) or not revision:
            raise ValueError("AgentNode revision must be nonempty text")
        self.revision = revision
        if isinstance(engines, EngineRegistry):
            self.engines = engines
        else:
            self.engines = EngineRegistry()
            for name, engine in engines.items():
                self.engines.register(name, engine)

    @staticmethod
    def _record(capabilities, capability, identifier):
        candidates = [(source, source["records"][identifier]) for source in capabilities.get(capability, ())
                      if identifier in source["records"]]
        if len(candidates) != 1:
            raise ValueError(f"{capability}/{identifier} must resolve to exactly one definition")
        return candidates[0]

    def _profile(self, definition, capabilities):
        identifier = definition.get("agent")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Agent node requires an agent ID")
        profile = deepcopy(self._record(capabilities, "agents", identifier)[1])
        AgentComponent().validate_record(identifier, profile)
        return profile

    def _engine(self, profile):
        try:
            engine = self.engines.resolve(profile["engine"])
        except KeyError:
            raise ValueError(f"Unregistered Agent engine: {profile['engine']}") from None
        factory = getattr(engine, "for_agent", None)
        if not callable(factory):
            raise ValueError("Agent engines must implement for_agent(definition)")
        bound = factory(deepcopy(profile))
        if not callable(getattr(bound, "execute", None)):
            raise TypeError("for_agent must return an Engine")
        required_capabilities(bound)
        # factory가 반환한 실제 실행기도 검사한다. Graph 사전 탐색에서 실패해야
        # 앞선 노드의 Tool 효과나 MCP 연결 이후에 구성 오류가 드러나지 않는다.
        if steering_mode(bound) == SteeringMode.CONSUME:
            checkpoint = getattr(bound, "checkpoint_name", None)
            if (not isinstance(checkpoint, str) or not checkpoint.strip()
                    or not callable(getattr(bound, "validate_resume", None))):
                raise ValueError("Steerable Agent engine requires checkpoint_name and validate_resume")
        return bound

    def _resources(self, profile, context):
        resources = profile.get("resources", {})
        skills = {name: deepcopy(self._record(context.capabilities, "skills", name)[1])
                  for name in resources.get("skills", [])}
        prompt = (deepcopy(self._record(context.capabilities, "prompts", resources["prompt"])[1])
                  if resources.get("prompt") else None)
        mcp = {}
        for server, aliases in resources.get("mcp", {}).items():
            source, definition = self._record(context.capabilities, "mcp", server)
            if not callable(source.get("connector")):
                raise ValueError(f"MCP connector is unavailable: {server}")
            mcp[server] = {"definition": deepcopy(definition), "aliases": deepcopy(aliases),
                           "revision": source["revision"]}
        names = list(profile.get("tools", []))
        if resources.get("rag"):
            if not context.capabilities.get("rag"):
                raise ValueError("Agent RAG resource requires the rag component")
            if "rag_search" not in names:
                names.append("rag_search")
        tools = context.tools.select(tuple(names))
        aliases = [alias for server in mcp.values() for alias in server["aliases"]]
        if len(set(aliases)) != len(aliases) or set(aliases) & set(names):
            raise ValueError("Agent MCP Tool aliases must be unique")
        return tools, {"skills": skills, "skill_revisions": {name: revision_token(value) for name, value in skills.items()},
                       "prompt": prompt, "prompt_revision": revision_token(prompt) if prompt is not None else None,
                       "mcp": mcp, "rag": bool(resources.get("rag"))}

    def additional_capabilities(self, definition, capabilities):
        profile = self._profile(definition, capabilities)
        resources = profile.get("resources", {})
        return tuple(dict.fromkeys((*required_capabilities(self._engine(profile)),
                     *(name for name in ("skills", "mcp", "rag") if resources.get(name)),
                     *(("prompts",) if resources.get("prompt") else ()))))

    def graph_engine(self, definition, capabilities):
        """Graph 사전 탐색에 하위 실행 정의를 선언한다. 실행은 하지 않는다."""
        engine = self._engine(self._profile(definition, capabilities))
        return engine if isinstance(engine, GraphEngine) else None

    def graph_context(self, definition, context):
        """Graph 환경은 부모 Run 권한을 공유한다. wrapper Tool ceiling은 없다."""
        self._engine(self._profile(definition, context.capabilities))
        return context

    def checkpoint_engine(self, definition, context):
        """저장·재개 계약을 제공하는 자식만 조율 노드로 선언한다."""
        engine = self._engine(self._profile(definition, context.capabilities))
        return engine if getattr(engine, "checkpoint_name", None) and callable(getattr(engine, "validate_resume", None)) else None

    def instruction_engine(self, definition, context):
        """예약 가능한 실제 소비자만 선언한다. Graph 전달자 자체는 예약 대상이 아니다."""
        profile = self._profile(definition, context.capabilities)
        return profile["engine"] if steering_mode(self._engine(profile)) == SteeringMode.CONSUME else None

    def binding(self, definition, context):
        """재개 검사와 Step에 런타임 핸들 없이 재현 가능한 정의를 남긴다."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        describe = getattr(engine, "configuration", None)
        binding = {"agent_id": definition["agent"], "agent": profile,
                "agent_revision": AgentComponent.revision(profile), "engine": profile["engine"],
                "handler_revision": self.revision,
                "configuration": describe(context.project.config, profile["engine"], session_config=context.session.config)
                                 if describe else {"runtime_only": True}}
        if isinstance(engine, GraphEngine):
            binding["workflow_id"] = engine.workflow
        else:
            binding["resources"] = self._resources(profile, context)[1]
        return binding

    def validate(self, definition, context):
        """전체 Workflow의 참조·정책을 외부 작업 전에 검사한다."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        if isinstance(engine, GraphEngine):
            if any(key in definition for key in ("output_format", "emit_text")):
                raise ValueError("Graph Agent nodes do not support output_format or emit_text")
            return
        self._resources(profile, context)
        if definition.get("output_format", profile.get("output_format", "text")) not in ("text", "json"):
            raise ValueError("Agent output_format must be text or json")
        if type(definition.get("emit_text", False)) is not bool:
            raise ValueError("Agent emit_text must be boolean")

    async def _graph_call(self, node, engine):
        """다른 Graph 환경으로 위임한다. behavior/resource/Agent 예산을 만들지 않는다."""
        if node.invoke_graph is None:
            raise ValueError("Graph Agent requires a parent Graph execution scope")
        self.validate(node.definition, node.context)
        binding = self.binding(node.definition, node.context)
        inputs = node.inputs if node.inputs is not None else node.state
        step_id = new_id()
        context = replace(node.context, state={"agent": deepcopy(binding)},
                          output_step_id=step_id, output_visibility="internal")
        if context.steering is not None:
            context = replace(context, steering=context.steering.child(target_id=step_id,
                scope=node.checkpoint_key, engine=binding["engine"], mode=steering_mode(engine),
                node_id=node.node_id, node_path=node.node_path))
        await node.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind="agent",
            name=binding["agent_id"], metadata={**binding, "inputs": inputs}))
        async def forward(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(event, metadata={**event.metadata,
                    "agent_step_id": event.metadata.get("agent_step_id", step_id)})
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
            EngineOutput(data=output, visibility="internal")))
        return {"text": "", "data": output}

    async def __call__(self, node):
        """업무의 입력/출력을 검증하고 연결 세션과 실행 스코프를 호출별로 정리한다."""
        profile = self._profile(node.definition, node.context.capabilities)
        engine = self._engine(profile)
        if isinstance(engine, GraphEngine):
            return await self._graph_call(node, engine)
        tools, resources = self._resources(profile, node.context)
        inputs = node.inputs if node.inputs is not None else node.state
        if "input_schema" in profile:
            Draft202012Validator(profile["input_schema"]).validate(inputs)
        binding = self.binding(node.definition, node.context)
        step_id = new_id()
        await node.emit(EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id, kind="agent",
                                   name=node.definition["agent"], metadata={**binding, "inputs": inputs}))
        try:
            policy = profile.get("policy", {})
            async with timeout(policy.get("timeout_seconds")), AsyncExitStack() as stack:
                for server, resource in resources["mcp"].items():
                    source, _ = self._record(node.context.capabilities, "mcp", server)
                    remote = await stack.enter_async_context(source["connector"](deepcopy(resource["definition"])))
                    if not isinstance(remote, ToolRegistry):
                        raise TypeError("MCP connector must provide a ToolRegistry")
                    for alias, name in resource["aliases"].items():
                        tool = remote.get(name)
                        tools.register(Tool(alias, tool.description, tool.parameters, tool.handler, contract=tool.contract))
                if resources["skills"] or resources["prompt"]:
                    # purpose는 업무 설명이며 미설정 system_prompt의 대체값이 아니다.
                    # 선택한 Skill만 상속된 명시 프롬프트에 결합한다.
                    prompt = binding["configuration"].get("values", {}).get("config", {}).get("system_prompt", profile.get("system_prompt"))
                    # 순서: 참조 Prompt → 명시 inline/상속 prompt → 선택 Skill.
                    # Prompt의 역할 라벨은 지침 데이터이며 실제 Tool 권한을 만들지 않는다.
                    parts = ([f"{m['role']}: {m['content']}" for m in resources["prompt"]["messages"]]
                             if resources["prompt"] else [])
                    if prompt:
                        parts.append(prompt)
                    parts.extend(f"Skill {name}:\n{skill['instructions']}" for name, skill in resources["skills"].items())
                    prompt = "\n\n".join(parts)
                    engine = self._engine({**profile, "system_prompt": prompt})
                scope = (node.context.tool_scope or ToolExecutionScope(ToolPolicy())).child(
                    allowed_tools=tools.names(), max_calls=policy.get("max_tool_calls"))
                if node.context.checkpoint:
                    # 완료된 자식 노드를 재사용해도 Agent의 호출 예산/필수 Tool 이력은 보존한다.
                    scope.restore([record["agent_usage"][node.checkpoint_key]
                        for record in node.context.checkpoint["records"].values()
                        if node.checkpoint_key in record.get("agent_usage", {})])
                message = next(item for item in node.context.messages if item.id == node.context.run.input_message_id)
                message = replace(message, content=json.dumps(inputs, ensure_ascii=False, allow_nan=False))
                # 부모 Run과 예산은 유지하며 기록/임시 상태와 선택 capability만 분리한다.
                capabilities = {
                    name: node.context.capabilities[name] for name in required_capabilities(engine) if name != "tools"}
                if "tools" in required_capabilities(engine):
                    capabilities["tools"] = tools
                visibility = ("user" if node.definition.get("emit_text", False)
                              and node.context.output_visibility == "user" else "internal")
                context = replace(node.context, messages=(message,), tools=tools,
                                  state={"agent": deepcopy(binding)}, capabilities=capabilities,
                                  checkpoint=None, tool_scope=scope,
                                  output_step_id=step_id, output_visibility=visibility)
                if context.steering is not None:
                    context = replace(context, steering=context.steering.child(target_id=step_id,
                        scope=node.checkpoint_key, engine=profile["engine"], mode=steering_mode(engine),
                        node_id=node.node_id, node_path=node.node_path))
                bridge = None
                if self.checkpoint_engine(node.definition, node.context) is not None:
                    records = deepcopy(node.context.checkpoint["records"]) if node.context.checkpoint else {}
                    bridge = EngineCheckpointScope(node.checkpoint_key, engine.checkpoint_name, records)
                    context = bridge.context(context)
                if steering_mode(engine) == SteeringMode.CONSUME and bridge is None:
                    raise ValueError("Steerable Agent engine requires a durable checkpoint contract")
                engine_output = None
                async def forward(event):
                    nonlocal engine_output
                    if event.type == EngineEventType.STEERING and bridge is not None:
                        event = bridge.wrap_instruction(event)
                    if event.type == EngineEventType.CHECKPOINT:
                        if bridge is not None:
                            event = bridge.wrap(event)
                        else:
                            raise ValueError("Agent engines cannot own Workflow checkpoint lifecycle")
                        metadata = deepcopy(event.metadata)
                        metadata["value"].setdefault("agent_usage", {})[node.checkpoint_key] = scope.checkpoint()
                        event = replace(event, metadata=metadata)
                    if event.type == EngineEventType.PAUSED:
                        if bridge is None:
                            raise ValueError("Agent engine requires a checkpoint scope to pause")
                        raise _GraphPause(node.checkpoint_key)
                    if event.type == EngineEventType.STEP_STARTED:
                        event = replace(event, metadata={**event.metadata,
                            "agent_id": event.metadata.get("agent_id", binding["agent_id"]),
                            "agent_revision": event.metadata.get("agent_revision", binding["agent_revision"]),
                            "engine": event.metadata.get("engine", binding["engine"]),
                            "node_id": event.metadata.get("node_id", node.node_id),
                            "agent_step_id": event.metadata.get("agent_step_id", step_id)})
                    # 자식 엔진 결과를 Agent 계약 검증 전에 확정하지 않는다. 내부 델타도
                    # 관찰 가능하되 visibility로 대화/UI의 사용자 출력과 구분한다.
                    if event.type == EngineEventType.OUTPUT:
                        if event.output is None or event.output.step_id != step_id:
                            raise ValueError("Agent engine output must belong to its Agent Step")
                        engine_output = event.output
                        return
                    await node.emit(event)
                    if bridge is not None and event.type == EngineEventType.STEERING:
                        bridge.accepted_instruction(event, context.steering)
                    if bridge is not None and event.type == EngineEventType.CHECKPOINT:
                        bridge.accepted(event)
                    if bridge is not None and event.type in (EngineEventType.STEP_UPDATED, EngineEventType.STEP_FAILED,
                                                       EngineEventType.STEP_COMPLETED, EngineEventType.STEP_CANCELLED):
                        # 승인 이후 효과 이전에도 예산을 기록한다. 불확실한 작업의 명시적 재시도가
                        # Agent 한도를 초기화하지 않도록 소유 컨테이너 체크포인트를 갱신한다.
                        await node.record_usage(scope.checkpoint())
                async with aclosing(BaseEngine.open_instructions(context)) as events:
                    async for event in events:
                        await forward(event)
                # 미지원 합성 엔진 내부의 Loop가 부모 채널을 자동 소비해서는 안 된다.
                execution_context = replace(context, steering=None) if steering_mode(engine) == SteeringMode.UNSUPPORTED else context
                async with aclosing(engine.execute(execution_context)) as events:
                    async for event in events:
                        await forward(event)
                async with aclosing(BaseEngine.close_instructions(context)) as events:
                    async for event in events:
                        await forward(event)
                if policy.get("require_tool") and not scope.completed:
                    raise ValueError("Agent requires at least one successfully completed Tool")
                if engine_output is None:
                    raise ValueError("Agent engine must emit an EngineOutput")
                if engine_output is not None and engine_output.visibility == "internal":
                    visibility = "internal"
                result = {"text": engine_output.text if engine_output is not None else ""}
                if engine_output.data is not None:
                    result["data"] = engine_output.data
                elif node.definition.get("output_format", profile.get("output_format", "text")) == "json":
                    result["data"] = json.loads(result["text"])
                Component.serialize(result)
                if "output_schema" in profile:
                    Draft202012Validator(profile["output_schema"]).validate(result)
        except (asyncio.CancelledError, _GraphPause):
            raise
        except Exception as error:
            await node.emit(BaseEngine.step_failed_event(step_id, error))
            raise
        await node.emit(BaseEngine.step_completed_event(context, step_id,
            EngineOutput(text=result["text"], data=result.get("data"), visibility=visibility),
            metadata={"tool_calls": scope.calls, "completed_tools": scope.completed}))
        return result
