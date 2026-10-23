"""Agent 업무 정의를 등록 엔진에 연결한다. 모든 실행은 부모 Run 안에서 관찰된다."""

from llm.core.configuration_views import ConfigurationView
from llm.core.definition_snapshot import DefinitionSnapshot

from llm.core.checkpoint_records import CheckpointWrite, CheckpointRecords

from llm.core.continuations import ResumeRecord

from llm.core.models import RunErrorCode
from llm.core.results import OutputVisibility

import asyncio
import json
from contextlib import AsyncExitStack, aclosing, nullcontext
from copy import deepcopy
from llm.components.skills.records import SkillRecord
from llm.components.prompts.records import PromptDefinition
from dataclasses import replace
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from asyncio import timeout
from llm.core.agents import AgentBinding, AgentDefinition, AgentMode
from llm.components.base import Component
from llm.components.tools import Tool, ToolRegistry
from llm.core.checkpoints import CheckpointStatus
from llm.core.models import new_id
from llm.services.infrastructure.storage import revision_token
from llm.core.results import EngineOutput
from llm.services.runtime.tools import ToolExecutionScope, ToolRuntime, ToolUsageError
from llm.policies import ExecutionLimitError
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType, required_capabilities, steering_mode, agent_mode
from llm.core.steering import SteeringMode
from llm.core.schema import object_schema, field
from llm.engines.registry import EngineRegistry
from .graph.engine import _GraphPause
from .graph.checkpoints import EngineCheckpointScope


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
    USAGE_FIELD = "agent_usage"
    AGENT_ID_FIELD = "agent_id"

    @classmethod
    def step_agent_id(cls, step):
        """Read this execution Owner's top-level Step selection for deletion guards."""
        return step.metadata.get(cls.AGENT_ID_FIELD)

    @staticmethod
    def agent_references(node):
        """The action owner interprets its stored selection without resolving an Engine."""
        return (AgentDefinition.identifier(node["agent"]),)

    @staticmethod
    def referenced_agents(record):
        """Read only execution-owned selections, never arbitrary application metadata."""
        from llm.core.checkpoints import CheckpointEnvelope, unwrap_record
        while CheckpointEnvelope.is_record(record):
            record, _ = unwrap_record(record)
        selections = []
        if "agent_binding" in record:
            selections.append(record["agent_binding"])
        if "handler_selection" in record:
            selections.append(record["handler_selection"]["binding"])
        return {value["agent_id"] for value in selections if "agent_id" in value}

    @staticmethod
    def describe_config():
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
        candidates = [(source, DefinitionSnapshot.records_from(source)[identifier]) for source in capabilities.get(capability, ())
                      if identifier in DefinitionSnapshot.records_from(source)]
        if len(candidates) != 1:
            raise ValueError(f"{capability}/{identifier} must resolve to exactly one definition")
        return candidates[0]

    def _profile(self, definition, capabilities):
        identifier = definition.get("agent")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Agent node requires an agent ID")
        profile = AgentDefinitions.selected(capabilities.get(AgentDefinition.CAPABILITY, ()), identifier)
        AgentDefinition.validate(identifier, profile)
        return profile

    def _engine(self, profile):
        return self.engines.resolve_agent(profile)

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
        parent = context.tool_scope
        if parent is not None and parent.allowed_tools is not None:
            if not set([*names, *aliases]).issubset(parent.allowed_tools):
                raise ExecutionLimitError(RunErrorCode.TOOL_DENIED, "Child Tool selection widens its parent")
        return tools, {"skills": skills, "skill_revisions": {name: revision_token(value) for name, value in skills.items()},
                       "prompt": prompt, "prompt_revision": revision_token(prompt) if prompt is not None else None,
                       "mcp": mcp, "rag": bool(resources.get("rag"))}

    async def connect_resources(self, resources, context, tools, stack):
        for server, resource in resources["mcp"].items():
            source, _ = self._record(context.capabilities, "mcp", server)
            remote = await stack.enter_async_context(source["connector"](deepcopy(resource["definition"])))
            if not isinstance(remote, ToolRegistry):
                raise TypeError("MCP connector must provide a ToolRegistry")
            for alias, name in resource["aliases"].items():
                tool = remote.get(name)
                tools.register(Tool(alias, tool.description, tool.parameters, tool.handler,
                                    contract=tool.contract, classification=tool.classification))

    @staticmethod
    def compose_instructions(engine, resources, context):
        if resources["skills"] or resources["prompt"]:
            engine = engine.with_instructions(context,
                before=tuple(f"{m.role}: {m.content}" for m in PromptDefinition.message_values(resources["prompt"]))
                       if resources["prompt"] else (),
                after=tuple(f"Skill {name}:\n{SkillRecord.instructions(skill)}" for name, skill in resources["skills"].items()))
        return engine

    def additional_capabilities(self, definition, capabilities):
        profile = self._profile(definition, capabilities)
        resources = profile.get("resources", {})
        return tuple(dict.fromkeys((*required_capabilities(self._engine(profile)),
                     *(name for name in ("skills", "mcp", "rag") if resources.get(name)),
                     *(("prompts",) if resources.get("prompt") else ()))))

    def graph_engine(self, definition, capabilities):
        """Graph 사전 탐색에 하위 실행 정의를 선언한다. 실행은 하지 않는다."""
        engine = self._engine(self._profile(definition, capabilities))
        return engine if agent_mode(engine) == AgentMode.WORKFLOW else None

    def graph_context(self, definition, context):
        """Graph 환경은 부모 Run 권한을 공유한다. wrapper Tool ceiling은 없다."""
        return context

    def discover_graph(self, definition, capabilities):
        """Structural Workflow references only; private Agent options are selected later."""
        profile = self._profile(definition, capabilities)
        engine = self.engines.get(profile["engine"])
        if agent_mode(engine) != AgentMode.WORKFLOW:
            return None
        return engine.discover_agent(profile)

    def checkpoint_engine(self, definition, context):
        """저장·재개 계약을 제공하는 자식만 조율 노드로 선언한다."""
        engine = self._engine(self._profile(definition, context.capabilities))
        return engine if getattr(engine, "checkpoint_name", None) and callable(getattr(engine, "validate_resume", None)) else None

    def instruction_engine(self, definition, context):
        """등록된 공개 소비 기능을 선언한다. 선택 시 factory의 동일 선언을 검증한다."""
        profile = self._profile(definition, context.capabilities)
        return profile["engine"] if steering_mode(self.engines.get(profile["engine"])) == SteeringMode.CONSUME else None

    def binding(self, definition, context):
        """재개 검사와 Step에 런타임 핸들 없이 재현 가능한 정의를 남긴다."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        describe = getattr(engine, ConfigurationView.RESOLVE_METHOD, None)
        binding = {"agent_id": definition["agent"], "agent": profile,
                "agent_revision": AgentDefinition.revision(profile), "engine": profile["engine"],
                "handler_revision": self.revision,
                "configuration": describe(context.project.config, profile["engine"], session_config=context.session.config)
                                 if describe else ConfigurationView.unavailable().to_dict()}
        if agent_mode(engine) == AgentMode.WORKFLOW:
            binding["workflow_id"] = engine.workflow
        else:
            binding["resources"] = self._resources(profile, context)[1]
        bind_execution = getattr(engine, "bind_execution", None)
        if callable(bind_execution):
            binding["execution"] = bind_execution(context)
        Component.serialize(binding)
        return binding

    def validate(self, definition, context):
        """Validate the selected Agent before its approval or external work."""
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW:
            if any(key in definition for key in ("output_format", "emit_text")):
                raise ValueError("Graph Agent nodes do not support output_format or emit_text")
            return
        self._resources(profile, context)
        resources = profile.get("resources", {})
        if (resources.get("skills") or resources.get("prompt")) and not callable(getattr(engine, "with_instructions", None)):
            raise ValueError("Agent Prompt/Skill composition requires Engine.with_instructions")
        if definition.get("output_format", profile.get("output_format", "text")) not in ("text", "json"):
            raise ValueError("Agent output_format must be text or json")
        if type(definition.get("emit_text", False)) is not bool:
            raise ValueError("Agent emit_text must be boolean")

    def validate_selection(self, definition, context, *, owner, records, checkpoint_name, wrapped_graph=False):
        """Validate selected descendants through their Engine, including completed containers."""
        context = self.prepare_context(definition, context)
        profile = self._profile(definition, context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW and not wrapped_graph:
            return  # Inline Graph children are visited in the parent Graph environment.
        name = getattr(engine, "checkpoint_name", None)
        if agent_mode(engine) != AgentMode.WORKFLOW:
            self._checkpoint_usage(records, owner, name)
        validate = getattr(engine, "validate_selections", None)
        if name is None or not callable(validate):
            return
        if agent_mode(engine) != AgentMode.WORKFLOW:
            tools, _ = self._resources(profile, context)
            context = replace(context, tools=tools)
        bridge = EngineCheckpointScope(owner, name, records, parent_name=checkpoint_name)
        if bridge.snapshot() is not None:
            validate(bridge.context(context))

    @classmethod
    def _checkpoint_usage(cls, records, owner, name):
        """선택한 Agent의 실행 기록만 해석하고 필수 사용량을 검증한다."""
        from llm.core.checkpoints import CheckpointEnvelope, unwrap_record
        from llm.core.steering import InstructionCheckpoint

        values = []
        def append(record):
            usage = record.get(cls.USAGE_FIELD)
            if not isinstance(usage, dict) or owner not in usage:
                raise ToolUsageError(f"Tool usage missing from Agent checkpoint: {owner}")
            values.append(usage[owner])

        selected = [record for record in records.values()
                    if record.get("engine_scope") == owner and record.get("checkpoint_name") == name]
        for record in selected:
            payload, _ = unwrap_record(record)
            if InstructionCheckpoint.is_record(payload):
                # 지시는 실행 사용량의 소유자가 아니다. 해당 소유자의 계약으로만 구분한다.
                InstructionCheckpoint.read(payload)
                continue
            append(record)
        marker = records.get(owner)
        if marker is not None:
            if (cls.USAGE_FIELD in marker or marker.get("node_type") == "agent_container"
                    or marker["status"] == CheckpointStatus.STARTED):
                append(marker)
        ToolExecutionScope.validate_usage(values)
        return values

    def prepare_context(self, definition, context):
        """Request only the selected Agent's public capabilities, before approval/effects."""
        context = context.with_capabilities((AgentDefinition.CAPABILITY,))
        engine = self._engine(self._profile(definition, context.capabilities))
        context = context.with_capabilities(self.additional_capabilities(definition, context.capabilities))
        discover = getattr(engine, "additional_capabilities", None)
        if callable(discover):
            from itertools import count
            for expansion in count():
                names = discover(context.capabilities)
                if not isinstance(names, tuple) or any(not isinstance(name, str) or not name for name in names):
                    raise ValueError("additional_capabilities must return a tuple of names")
                if all(name in context.capabilities for name in names):
                    break
                context = context.with_capabilities(names, expansion=expansion)
        return context

    async def _graph_call(self, node, engine):
        """다른 Graph 환경으로 위임한다. behavior/resource/Agent 예산을 만들지 않는다."""
        if node.invoke_graph is None:
            raise ValueError("Graph Agent requires a parent Graph execution scope")
        self.validate(node.definition, node.context)
        binding = self.binding(node.definition, node.context)
        inputs = node.inputs if node.inputs is not None else node.state
        step_id = new_id()
        context = replace(node.context, state={"agent": deepcopy(binding)},
                          output_step_id=step_id, output_visibility=OutputVisibility.INTERNAL)
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
            EngineOutput(data=output, visibility=OutputVisibility.INTERNAL)))
        return {"text": "", "data": output}

    async def __call__(self, node):
        """업무의 입력/출력을 검증하고 연결 세션과 실행 스코프를 호출별로 정리한다."""
        profile = self._profile(node.definition, node.context.capabilities)
        engine = self._engine(profile)
        if agent_mode(engine) == AgentMode.WORKFLOW:
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
                await self.connect_resources(resources, node.context, tools, stack)
                engine = self.compose_instructions(engine, resources, node.context)
                scope = (node.context.tool_scope or ToolExecutionScope(ToolRuntime())).child(
                    allowed_tools=tools.names(), max_calls=policy.get("max_tool_calls"))
                if node.context.checkpoint:
                    # 완료된 자식 노드를 재사용해도 Agent의 호출 예산/필수 Tool 이력은 보존한다.
                    scope.restore(self._checkpoint_usage(CheckpointRecords.from_checkpoint(node.context.checkpoint).records,
                                                         node.checkpoint_key, getattr(engine, "checkpoint_name", None)))
                await node.record_usage(scope.checkpoint())
                message = next(item for item in node.context.messages if item.id == node.context.run.input_message_id)
                message = replace(message, content=json.dumps(inputs, ensure_ascii=False, allow_nan=False))
                # 부모 Run과 예산은 유지하며 기록/임시 상태와 선택 capability만 분리한다.
                capabilities = {
                    name: node.context.capabilities[name] for name in required_capabilities(engine) if name != ToolRegistry.CAPABILITY}
                if ToolRegistry.CAPABILITY in required_capabilities(engine):
                    capabilities[ToolRegistry.CAPABILITY] = tools
                discover = getattr(engine, "additional_capabilities", None)
                if callable(discover):
                    # 부모가 이미 승인/해석한 capability만 전달한다. 새 resource를 만들지 않는다.
                    for name in discover({**node.context.capabilities, ToolRegistry.CAPABILITY: tools}):
                        if name in node.context.capabilities and name != ToolRegistry.CAPABILITY:
                            capabilities[name] = node.context.capabilities[name]
                visibility = (OutputVisibility.USER if node.definition.get("emit_text", False)
                              and node.context.output_visibility == OutputVisibility.USER else OutputVisibility.INTERNAL)
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
                    records = deepcopy(CheckpointRecords.from_checkpoint(node.context.checkpoint).records) if node.context.checkpoint else {}
                    bridge = EngineCheckpointScope(node.checkpoint_key, engine.checkpoint_name, records,
                                                   parent_name=getattr(node, "checkpoint_name", "graph"))
                    context = bridge.context(context)
                if steering_mode(engine) == SteeringMode.CONSUME and bridge is None:
                    raise ValueError("Steerable Agent engine requires a durable checkpoint contract")
                engine_output = None
                async def forward(event):
                    nonlocal engine_output
                    if event.type == EngineEventType.STEERING and bridge is not None:
                        event = bridge.wrap_instruction(event)
                    if event.type == EngineEventType.CHECKPOINT or event.checkpoint is not None:
                        if bridge is not None:
                            event = bridge.wrap(event)
                        else:
                            raise ValueError("Agent engines cannot own Workflow checkpoint lifecycle")
                        receipt = event.checkpoint or event
                        write = CheckpointWrite(receipt.metadata)
                        value = write.record.value
                        value.setdefault(self.USAGE_FIELD, {})[node.checkpoint_key] = scope.checkpoint()
                        receipt = replace(receipt, metadata=write.with_record(value).to_dict())
                        event = replace(event, checkpoint=receipt) if event.checkpoint is not None else receipt
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
                    if bridge is not None and (event.type == EngineEventType.CHECKPOINT or event.checkpoint is not None):
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
                if engine_output is not None and engine_output.visibility == OutputVisibility.INTERNAL:
                    visibility = OutputVisibility.INTERNAL
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


class AgentInvocation:
    """One admitted root Agent; scheduling and persisted effects stay in RunManager."""

    def __init__(self, binding, engine, engines):
        self.binding = AgentBinding.read(binding.to_dict())
        self.profile, self.engine = self.binding.definition, engine
        self.selection = AgentExecution(engines=engines)
        self.mode = agent_mode(engine)
        self.resources = None
        self.runtime_binding = None

    def __getattr__(self, name):
        return getattr(self.engine, name)

    @property
    def required_capabilities(self):
        resources = self.profile.get("resources", {})
        return tuple(dict.fromkeys((*required_capabilities(self.engine),
            *(name for name in ("skills", "mcp", "rag") if resources.get(name)),
            *(("prompts",) if resources.get("prompt") else ()),
            *((ToolRegistry.CAPABILITY,) if self.profile.get("tools") or resources.get("mcp") or resources.get("rag") else ()))))

    def additional_capabilities(self, capabilities):
        discover = getattr(self.engine, "additional_capabilities", None)
        return discover(capabilities) if callable(discover) else ()

    def prepare_context(self, context):
        if self.mode == AgentMode.BEHAVIORAL:
            tools, self.resources = self.selection._resources(self.profile, context)
            aliases = [alias for server in self.resources["mcp"].values() for alias in server["aliases"]]
            policy = self.profile.get("policy", {})
            scope = context.tool_scope.child(allowed_tools=(*tools.names(), *aliases),
                                             max_calls=policy.get("max_tool_calls"))
            if context.checkpoint is not None:
                scope.restore_checkpoint(CheckpointRecords.from_checkpoint(context.checkpoint).records)
            self.engine = self.selection.compose_instructions(self.engine, self.resources, context)
            capabilities = dict(context.capabilities)
            if ToolRegistry.CAPABILITY in capabilities:
                capabilities[ToolRegistry.CAPABILITY] = tools
            context = replace(context, tools=tools, capabilities=capabilities, tool_scope=scope)
            if "input_schema" in self.profile:
                message = next(item for item in context.messages if item.id == context.run.input_message_id)
                Draft202012Validator(self.profile["input_schema"]).validate(message.content)
        describe = getattr(self.engine, ConfigurationView.RESOLVE_METHOD, None)
        configuration = (revision_token(describe(context.project.config, self.binding.engine,
                         session_config=context.session.config)) if callable(describe) else None)
        execution = getattr(self.engine, "bind_execution", None)
        self.runtime_binding = {
            "agent_id": self.binding.agent_id, "revision": self.binding.revision,
            "engine": self.binding.engine, "mode": self.mode.value,
            "configuration": configuration, "resources": deepcopy(self.resources),
            **context.tools.execution_binding(context.tool_scope.binding()),
            "execution": revision_token({"value": execution(context)}) if callable(execution) else None,
        }
        Component.serialize(self.runtime_binding)
        # Root identity is already in Run admission evidence. The Engine owns
        # its private state; binding must not seed or persist application state.
        return context

    def validate_resume(self, checkpoint, *, retry_nodes=(), context=None, **kwargs):
        validate = getattr(self.engine, "validate_resume", None)
        if not callable(validate):
            raise ValueError("Engine does not support checkpoint resume")
        # Connected remote definitions are checked after connection and before execution.
        if self.profile.get("resources", {}).get("mcp"):
            context = None
        return validate(checkpoint, retry_nodes=retry_nodes,
                        **({"context": context} if context is not None else {}), **kwargs)

    async def execute(self, context):
        if self.runtime_binding is None:
            raise RuntimeError("Agent invocation requires a prepared execution binding")
        policy = self.profile.get("policy", {})
        async with timeout(policy.get("timeout_seconds")), AsyncExitStack() as stack:
            if self.resources is not None:
                await self.selection.connect_resources(self.resources, context, context.tools, stack)
            if self.profile.get("resources", {}).get("mcp") and context.checkpoint is not None:
                descriptor = ResumeRecord.require(context.run.metadata)
                self.engine.validate_resume(context.checkpoint, context=context,
                    retry_nodes=descriptor.retry_nodes, decisions=descriptor.decisions)
            stream = self.engine.execute(context)
            async with aclosing(stream) if callable(getattr(stream, "aclose", None)) else nullcontext(stream):
                async for event in stream:
                    if event.type == EngineEventType.OUTPUT and event.output is not None:
                        if policy.get("require_tool") and not context.tool_scope.completed:
                            raise ValueError("Agent requires at least one successfully completed Tool")
                        value = {"text": event.output.text}
                        if event.output.data is not None:
                            value["data"] = event.output.data
                        elif self.profile.get("output_format") == "json":
                            value["data"] = json.loads(event.output.text)
                            event = replace(event, output=replace(event.output, data=value["data"]))
                        if "output_schema" in self.profile:
                            Draft202012Validator(self.profile["output_schema"]).validate(value)
                    yield event


class AgentDelegation:
    """ToolExecutor용 저장 Agent 어댑터. 실행 알고리즘은 AgentNode와 공유한다.

    큐는 저장 ACK를 연결하는 backpressure일 뿐 실행/권한의 별도 소유자가 아니다.
    등록된 Host 핸들러에만 이 이벤트 계약을 제공하며 Project Python source는 받지 않는다.
    """

    required_capabilities = (AgentDefinition.CAPABILITY,)
    resumable_container = True

    def __init__(self, runtime):
        self.runtime = runtime
        self.execution_binding = {"revision": runtime.revision}

    def bind_arguments(self, arguments, context):
        """Selected definition, not the discoverable Agent catalogue, owns resume identity."""
        definition = {"agent": arguments["agent_id"]}
        context = self.runtime.prepare_context(definition, context)
        self.runtime.validate(definition, context)
        return self.runtime.binding(definition, context)

    def validate_selection(self, arguments, context, *, request_key, checkpoint_name):
        owner = json.dumps([request_key, "agent", arguments["agent_id"]], separators=(",", ":"))
        self.runtime.validate_selection({"agent": arguments["agent_id"]}, context, owner=owner,
            records=CheckpointRecords.records_or_empty(context.checkpoint), checkpoint_name=checkpoint_name, wrapped_graph=True)

    def additional_capabilities(self, capabilities):
        return (AgentDefinition.CAPABILITY,)

    async def __call__(self, arguments):
        raise ValueError("agent_run requires the Engine ToolExecutor event contract")

    async def execute_events(self, context, arguments, *, request_key, checkpoint_name, step_id, result):
        if (context is None or not isinstance(request_key, str) or not request_key.strip()
                or not isinstance(checkpoint_name, str) or not checkpoint_name.strip()):
            raise ValueError("Agent delegation requires a durable parent Engine checkpoint")
        # 동적 Tool 위임은 부모 Workflow의 사전 예약 경로가 아니다. 부모 Loop의 추가
        # 지시는 다음 부모 completion 경계에서 소비한다. 취소/예산/승인은 계속 공유한다.
        context = replace(context, steering=None)
        owner = json.dumps([request_key, "agent", arguments["agent_id"]], separators=(",", ":"))
        records = deepcopy(CheckpointRecords.records_or_empty(context.checkpoint))
        definition = {"agent": arguments["agent_id"]}
        context = self.runtime.prepare_context(definition, context)
        self.runtime.validate(definition, context)
        binding = self.runtime.binding(definition, context)
        marker = records.get(owner)
        if marker is not None and marker.get("agent_binding") != binding:
            raise ValueError("Delegated Agent configuration changed; start a new request")
        queue = asyncio.Queue(maxsize=1)

        async def emit(event):
            if event.type == EngineEventType.STEP_STARTED:
                event = replace(event, metadata={**event.metadata,
                    "delegation_step_id": step_id,
                    "parent_step_id": event.metadata.get("parent_step_id", step_id)})
            ack = asyncio.get_running_loop().create_future()
            await queue.put((event, ack))
            await ack

        async def record_usage(usage):
            value = {"node_type": "agent_container", "status": CheckpointStatus.STARTED, "container": True,
                "requires_retry": False, "agent_binding": binding, AgentExecution.USAGE_FIELD: {owner: usage}}
            await emit(EngineEvent(EngineEventType.CHECKPOINT, metadata=CheckpointWrite.record_event(checkpoint_name, owner, value).to_dict()))

        async def invoke_graph(engine, child, inputs, forward):
            bridge = EngineCheckpointScope(owner, engine.checkpoint_name, records, parent_name=checkpoint_name)
            child = bridge.context(child)
            message = next(m for m in child.messages if m.id == child.run.input_message_id)
            child = replace(child, messages=(replace(message, content=json.dumps(inputs, ensure_ascii=False)),))
            output = None
            async with aclosing(engine.execute(child)) as events:
                async for event in events:
                    if event.type == EngineEventType.OUTPUT:
                        output = event.output.data
                        continue
                    if event.type == EngineEventType.PAUSED:
                        raise _GraphPause(owner)
                    if event.type == EngineEventType.CHECKPOINT or event.checkpoint is not None:
                        event = bridge.wrap(event)
                    elif event.type == EngineEventType.STEERING:
                        event = bridge.wrap_instruction(event)
                    await forward(event)
                    if event.type == EngineEventType.CHECKPOINT or event.checkpoint is not None:
                        bridge.accepted(event)
                    elif event.type == EngineEventType.STEERING:
                        bridge.accepted_instruction(event, child.steering)
            return output

        async def produce():
            usage = ToolExecutionScope.initial_usage() if marker is None else marker[AgentExecution.USAGE_FIELD][owner]
            await record_usage(usage)
            node = SimpleNamespace(context=context, definition=definition, inputs=arguments["input"], state={},
                node_id=arguments["agent_id"], node_path=owner, checkpoint_key=owner,
                checkpoint_name=checkpoint_name, emit=emit, record_usage=record_usage, invoke_graph=invoke_graph)
            try:
                result["value"] = await self.runtime(node)
            except _GraphPause:
                await emit(EngineEvent(EngineEventType.PAUSED, metadata={"checkpoint": checkpoint_name}))

        producer = asyncio.create_task(produce())
        reader = None
        try:
            while not producer.done() or not queue.empty():
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
            await producer
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
