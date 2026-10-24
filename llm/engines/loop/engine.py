"""LiteLLM completion 스트림과 Tool 호출을 반복하는 실행 전략. tools capability를 명시적으로 요청하고 설정은 매 Run마다 복사한다.

A BaseEngine subclass: complete, execute requested tools, then repeat."""

from llm.components.processing import COMPLETION_PROCESSORS
from llm.components.tools.registry import ToolRegistry

from llm.core.implementation_parameters import ImplementationParameters

from llm.core.configuration_views import ConfigurationView, EngineConfigurationKey

from llm.core.checkpoint_records import CheckpointWrite, CheckpointPause

from llm.core.checkpoint_records import CheckpointContent, CheckpointRecords

from llm.core.checkpoints import CheckpointConversation

from llm.core.models import ResourceKind, StepKind

from llm.core.continuations import ResumeRecord

from llm.core.results import OutputVisibility
from llm.core.tool_event_result import ToolEventResult
from llm.core.step_tool_metadata import ToolStepMetadata

from llm.core.model_protocol import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ToolMode,
    ToolResult,
    copy_model,
)

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
from llm.components.processing import (
    CompletionMessage,
    CompletionRequest,
    CompletionObservation,
    CompletionPipeline,
)
from llm.core.models import MessageRole, MessageStatus
from llm.core.steering import is_instruction, SteeringMode, validate_instruction_record, InstructionCheckpoint
from llm.core.configuration import resolve_engine_config, UNSET
from llm.core.parameters import ParameterLayout
from llm.core.results import EngineOutput
from llm.core.contracts import ResourceRef
from llm.core.checkpoints import CheckpointStatus
from llm.providers.parameters import merge_params
from llm.services.runtime.tools import ToolExecutor, ToolExecutionError, ToolApprovalRequired
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.policies import CompletionPolicy, ExecutionLimitError
from llm.components.review import CompletionReview, CompletionReviewObservation, ReviewAction, ReviewPolicy
from llm.components.processing import review_identities
from llm.engines.loop.review import LoopReview, LoopIterationRecord
from llm.providers.requests import provider_schema, resolve_provider_options
from llm.engines.contracts import EngineHooks, ResumableHandlerContract
from llm.providers.streaming import CompletionAdapterContract
from .binding import LoopExecutionBinding
from .records import (
    LoopCheckpointRecord,
    LoopToolRecord,
    LoopCheckpointHeader,
    LoopStepMetadata,
    LoopPriorOutput,
    LoopInstructionBoundary,
)
from .configuration import LoopRegistration, LoopParameters, LoopPolicy, LoopAgentOptions
from llm.core.agents import AgentDefinition


# 모델 응답과 Tool 실행을 반복한다. 동시 Run의 설정을 서로 격리한다.
class LoopEngine(BaseEngine):
    """Common-model completion/Tool loop with a selected runtime adapter."""

    required_capabilities = (ToolRegistry.CAPABILITY, COMPLETION_PROCESSORS)
    checkpoint_name = "loop"
    PAUSE_TOOL_KEY = LoopToolRecord.PAUSE_TOOL_KEY
    CHECKPOINT_FORMAT = LoopCheckpointHeader.FORMAT_VALUE
    steering_mode = SteeringMode.CONSUME
    DISPLAY_NAME = "Loop"
    WRONG_CHECKPOINT = "Checkpoint does not belong to LoopEngine"
    CHANGED_BINDING = "Loop configuration or Tool definitions changed; start a new request"
    INVALID_DECISIONS = "Loop decisions must be an object"
    INVALID_DECISION_KEYS = "Loop decisions require waiting checkpoint keys"
    INVALID_PARAMETERS = "Completion parameters must be a string-keyed mapping"
    INVALID_PROMPT = "System prompt must be a string"
    REVIEW_DISABLED = "Final review cannot be disabled during Resume"
    CHANGED_REVIEWERS = "Reviewer registration changed before execution"
    UNOWNED_CANDIDATE = "Processors cannot publish a reviewed final candidate"
    ITERATION_EXCEEDED = "Loop iteration limit exceeded"
    UNSUPPORTED_CONTINUATION_POLICY = "Completion policy must support prepare_turn for live instructions"
    ITERATION_REACHED = "Loop iteration limit reached"
    DUPLICATE_TOOL_CALL = "Repeated tool call ID"
    COMPLETION_STEP_NAME = "LLM completion"
    COMPLETION_STEP_ERROR = "LLM iteration failed"
    RETRY_SET = "Explicit retry_nodes must match uncertain Tool keys: {value}"
    MISSING_APPROVAL = "Tool approval decisions required: {value}"
    parameter_layout = LoopParameters.LAYOUT
    _option_names = LoopParameters.NAMES

    def additional_capabilities(self, capabilities):
        """선택 Tool의 공개 runtime dependency 선언만 전달한다."""
        names = []
        tools = capabilities.get(ToolRegistry.CAPABILITY)
        for name in tools.names() if tools is not BaseEngine.ABSENT else ():
            handler = tools.get(name).handler
            if not ResumableHandlerContract.declared(handler):
                continue
            names.extend(EngineHooks.required_names(handler))
            describe = EngineHooks.discoverer(handler)
            if describe is not BaseEngine.ABSENT:
                names.extend(describe(capabilities))
        return tuple(dict.fromkeys(names))

    def _binding(self, context):
        """설정·Tool 계약을 비교한다. 실행 함수 교체 시에는 프로젝트 engine revision을 변경한다."""
        # Serialize common definitions explicitly so approval binding retains their full identity.
        values = LoopExecutionBinding.portable(
            LoopExecutionBinding.create(
                context.resolve_config(self.parameter_key),
                self.completion_kwargs,
                self.system_prompt,
                self._agent_options,
                LoopExecutionBinding.adapter_identity(self.completion_adapter),
                context.tools.execution_binding(
                    context.tool_scope.binding() if context.tool_scope else BaseEngine.ABSENT
                ),
            )
        )
        policy = LoopPolicy(
            ImplementationParameters.read(
                ConfigurationView.read(
                    self.resolve_config(
                        context.project.config, context.run.engine, session_config=context.session.config
                    )
                ).values
            ).policy
        ).final_review
        if ReviewPolicy.read(policy) is not BaseEngine.ABSENT:
            LoopExecutionBinding.attach_reviewers(
                values, review_identities(context.capabilities.get(COMPLETION_PROCESSORS, ()))
            )
        return LoopExecutionBinding.digest(values)

    def bind_execution(self, context):
        """Public durable identity, including the selected adapter's runtime contract."""
        return self._binding(context)

    def validate_resume(
        self, checkpoint, *, retry_nodes=(), context=BaseEngine.ABSENT, decisions=BaseEngine.ABSENT
    ):
        """완료 Tool은 재사용하고 효과가 불확실한 Tool만 명시적 재실행 승인을 받는다."""
        if (
            LoopCheckpointHeader(CheckpointContent.from_checkpoint(checkpoint).header).format
            != self.CHECKPOINT_FORMAT
        ):
            raise ValueError(LoopEngine.WRONG_CHECKPOINT)
        LoopReview.require_resume(checkpoint)
        uncertain = set()
        for key, value in CheckpointRecords.from_checkpoint(checkpoint).records.items():
            if key.startswith(LoopIterationRecord.KEY_PREFIX):
                ModelResponse.from_dict(LoopIterationRecord.response(value))
            if LoopInstructionBoundary.matches(key):
                validate_instruction_record(value)
            if LoopCheckpointRecord(value).status == CheckpointStatus.STARTED:
                LoopCheckpointRecord(value).validate_started()
                if LoopCheckpointRecord(value).requires_retry:
                    uncertain.add(key)
        if len(set(retry_nodes)) != len(retry_nodes) or set(retry_nodes) != uncertain:
            raise ValueError(LoopEngine.RETRY_SET.format(value=sorted(uncertain)))
        if context is not BaseEngine.ABSENT and LoopCheckpointHeader(
            CheckpointContent.from_checkpoint(checkpoint).header
        ).binding != self._binding(context):
            raise ValueError(LoopEngine.CHANGED_BINDING)
        if context is not BaseEngine.ABSENT:
            context.tools.validate_selections(replace(context, checkpoint=checkpoint))
        decisions = {} if decisions is BaseEngine.ABSENT else decisions
        if not isinstance(decisions, dict):
            raise TypeError(LoopEngine.INVALID_DECISIONS)
        waiting = {
            key
            for key, value in CheckpointRecords.from_checkpoint(checkpoint).records.items()
            if LoopCheckpointRecord(value).status == CheckpointStatus.WAITING
        }
        if set(decisions) - waiting:
            raise ValueError(LoopEngine.INVALID_DECISION_KEYS)
        if waiting - decisions.keys():
            raise ValueError(LoopEngine.MISSING_APPROVAL.format(value=sorted(waiting)))
        for key in waiting:
            saved = CheckpointRecords.from_checkpoint(checkpoint).records[key]
            if LoopCheckpointRecord(saved).has_interaction:
                LoopCheckpointRecord(saved).interaction.select_decision(decisions[key])

    def _record(self, key, value):
        interaction = (
            LoopCheckpointRecord(value).interaction
            if LoopCheckpointRecord(value).has_interaction
            else BaseEngine.ABSENT
        )
        return EngineEvent(
            EngineEventType.CHECKPOINT,
            interaction=interaction,
            metadata=CheckpointWrite.record_event(LoopEngine.checkpoint_name, key, deepcopy(value)).to_dict(),
        )

    def __init__(
        self,
        *,
        parameter_key=BaseEngine.ABSENT,
        retry_decider=BaseEngine.ABSENT,
        recover_completion=BaseEngine.ABSENT,
        completion_adapter=BaseEngine.ABSENT,
    ) -> None:
        """Host는 구현을 등록한다. 실행값은 Project/Session/Agent 설정에서만 읽는다."""
        LoopRegistration.validate_key(parameter_key)
        self.parameter_key, self._agent_options = parameter_key, {}
        LoopRegistration.validate_retry(retry_decider)
        self.retry_decider = retry_decider
        LoopRegistration.validate_recovery(recover_completion)
        self.recover_completion = recover_completion
        self.completion_adapter = completion_adapter
        self._configure({})

    def _configure(self, values):
        """검증된 실행별 값으로 사본을 준비한다. 저장/공개 설정에 기본값을 만들지 않는다."""
        from llm.core.schema import validate_declared_fields

        validate_declared_fields(LoopParameters(values).declared(self._option_names), self._option_schema())
        (
            max_iterations,
            request_timeout,
            tool_timeout,
            max_tool_calls,
            max_argument_chars,
            max_output_chars,
        ) = LoopParameters(values).execution_values(self._option_names)
        # Queue capacity changes backpressure only; it never drops output or limits execution.
        buffer_size = LoopParameters(values).buffer_size(BaseEngine.DEFAULT_BUFFER_SIZE)
        self.input_policy = deepcopy(LoopParameters(values).input_policy)
        self.provider = deepcopy(LoopParameters(values).provider)
        self.final_review = ReviewPolicy.read(LoopParameters(values).final_review)
        super().__init__(
            LoopEngine.DISPLAY_NAME, completion_adapter=self.completion_adapter, buffer_size=buffer_size
        )
        self.max_tool_calls, self.max_argument_chars, self.max_output_chars = (
            max_tool_calls,
            max_argument_chars,
            max_output_chars,
        )
        self.max_iterations = max_iterations
        self.request_timeout = request_timeout
        self.tool_timeout = tool_timeout
        self.completion_kwargs = copy_model(LoopParameters(values).completion)
        self.system_prompt = LoopParameters(values).system_prompt

    @classmethod
    def _option_schema(cls):
        return LoopParameters.option_schema(cls._option_names)

    def describe_config(self):
        return LoopParameters.describe(self)

    def resolve_config(self, config, name, *, session_config=BaseEngine.ABSENT):
        """실행과 UI가 공유하는 Project → Session → Agent 설정."""
        key = self.parameter_key or name
        agent = self._agent_options
        overrides = self.parameter_layout.unpack(LoopAgentOptions(agent).engine_options)
        if LoopAgentOptions(agent).has_prompt:
            LoopAgentOptions.set_prompt(overrides, LoopAgentOptions(agent).prompt)
        if LoopAgentOptions(agent).optional_completion:
            LoopAgentOptions.set_completion(overrides, LoopAgentOptions(agent).required_completion)
        view = ConfigurationView.read(
            resolve_engine_config(
                config,
                key,
                session_config=session_config,
                agent=self.parameter_layout.pack(overrides),
                schema=self.describe_config(),
            )
        )
        policy = ImplementationParameters.read(view.values).policy
        CompletionPolicy.validate_config(LoopPolicy(policy).completion)
        provider = LoopPolicy(policy).provider
        resolve_provider_options({} if provider is BaseEngine.ABSENT else provider)
        ReviewPolicy.read(LoopPolicy(policy).final_review)
        return view.with_runtime(()).to_dict()

    def _request(self, context: EngineContext, params: dict[str, Any]) -> ModelRequest:
        parameters = copy_model(params)
        self.completion_adapter.validate_request(
            parameters, require_model=CompletionAdapterContract.REQUIRE_MODEL
        )
        definitions = context.tools.definitions(
            constraints=context.tool_scope.argument_constraints if context.tool_scope else BaseEngine.ABSENT
        )
        mode = (
            ToolMode.REQUIRED
            if definitions and LoopAgentOptions.required_by(self) and not context.tool_scope.completed
            else BaseEngine.ABSENT
        )
        return ModelRequest(parameters=parameters, tools=definitions, tool_mode=mode)

    def with_instructions(self, context, *, before=(), after=()):
        """Compose explicit Agent resources using this Engine's own prompt resolution."""
        values = self.parameter_layout.unpack(
            ConfigurationView.read(
                self.resolve_config(
                    context.project.config,
                    self.parameter_key or context.run.engine,
                    session_config=context.session.config,
                )
            ).values
        )
        prompt = LoopParameters(values).system_prompt
        parts = [*before, *([prompt] if prompt else []), *after]
        if any(not isinstance(part, str) for part in parts):
            raise TypeError(LoopAgentOptions.INVALID_INSTRUCTIONS)
        worker = copy(self)
        worker._agent_options = LoopAgentOptions.with_prompt(
            self._agent_options, LoopAgentOptions.join_prompt(parts)
        )
        return worker

    def for_agent(self, definition: dict):
        """업무 정의를 적용한 호출별 엔진. 등록된 객체와 provider 핸들은 변경하지 않는다.

        다른 엔진도 for_agent(definition)와 execute(context)를 제공하여 AgentNode에
        등록할 수 있다. 준비 작업을 구현한 Loop 서브클래스의 메서드도 유지한다.
        """
        params = copy_model(LoopAgentOptions(definition).completion)
        # Agent는 부분 설정이다. 누락된 model은 Project/Session에서 상속한다.
        # 모든 계층에 없으면 최종 request 검증에서 provider 호출 전에 거부한다.
        self.completion_adapter.validate_request(params)
        self.completion_adapter.validate_input_ownership(params)
        options = LoopAgentOptions(definition).engine_options
        from jsonschema import Draft202012Validator

        Draft202012Validator(self.describe_config()).validate(options)
        copy(self)._configure(self.parameter_layout.unpack(options))
        worker = copy(self)
        worker._agent_options = LoopAgentOptions.create(
            deepcopy(options),
            params,
            (
                LoopAgentOptions.prompt_value(LoopAgentOptions(definition).prompt)
                if LoopAgentOptions(definition).has_prompt
                else {}
            ),
        )
        worker.parameter_key = self.parameter_key or AgentDefinition.engine(definition)
        worker._require_tool = LoopAgentOptions(definition).require_tool
        return worker

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        context.tools.validate_selections(context)
        resolved = self.parameter_layout.unpack(
            ConfigurationView.read(
                self.resolve_config(
                    context.project.config, context.run.engine, session_config=context.session.config
                )
            ).values
        )
        # A Run-local instance keeps shared defaults immutable and preserves
        # subclass methods. Only Loop-owned configuration are reinitialized.
        worker = copy(self)
        worker._resume_binding = self._binding(context)
        worker._configure(resolved)
        # 다른 Agent/Engine의 입력 예산을 상속하지 않는다. 처리기는 이 호출의 선택기만 전달받는다.
        context = replace(
            context,
            completion_policy=CompletionPolicy.from_config(
                LoopParameters(resolved).input_policy, context.token_counters
            ),
        )
        async with aclosing(worker._execute(context)) as events:
            async for event in events:
                yield event

    async def _execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        # Evaluate factories after preparation, once per Run. No shared per-Run
        # state lives on the Engine; each provider call gets fresh containers.
        supplied = (
            self.completion_kwargs(context) if callable(self.completion_kwargs) else self.completion_kwargs
        )
        if supplied is BaseEngine.ABSENT:
            supplied = {}
        if not isinstance(supplied, Mapping) or any(not isinstance(key, str) for key in supplied):
            raise ValueError(LoopEngine.INVALID_PARAMETERS)
        params = copy_model(dict(supplied))
        # These fields define the Loop transcript/tool execution contract.
        self.completion_adapter.validate_input_ownership(params)
        self.completion_adapter.validate_request(params)
        prompt = self.system_prompt(context) if callable(self.system_prompt) else self.system_prompt
        if prompt is not BaseEngine.ABSENT and not isinstance(prompt, str):
            raise ValueError(LoopEngine.INVALID_PROMPT)
        messages = [
            CompletionMessage(
                ModelMessage(ModelRole(message.role.value), message.content),
                message.id,
                is_instruction(message),
            )
            for message in context.messages
            if (message.role == MessageRole.USER and message.status == MessageStatus.COMMITTED)
            or (
                message.role != MessageRole.USER
                and message.status
                in (
                    MessageStatus.COMPLETED,
                    MessageStatus.INTERRUPTED,
                    MessageStatus.PAUSED,
                    MessageStatus.FAILED,
                )
            )
        ]
        if prompt:
            messages.insert(0, CompletionMessage(ModelMessage(ModelRole.SYSTEM, prompt)))
        if context.checkpoint is not BaseEngine.ABSENT:
            continuation = ResumeRecord.from_metadata(context.run.metadata)
            self.validate_resume(
                context.checkpoint,
                retry_nodes=(
                    continuation.retry_nodes
                    if continuation is not BaseEngine.ABSENT
                    else ResumeRecord.FRESH_RETRY_NODES
                ),
                decisions=(
                    continuation.decisions if continuation is not BaseEngine.ABSENT else BaseEngine.ABSENT
                ),
            )
            if (
                LoopCheckpointHeader(CheckpointContent.from_checkpoint(context.checkpoint).header).binding
                != self._resume_binding
            ):
                raise ValueError(LoopEngine.CHANGED_BINDING)
        reviewers = (
            review_identities(context.capabilities.get(COMPLETION_PROCESSORS, ()))
            if self.final_review is not BaseEngine.ABSENT
            else []
        )
        review = (
            LoopReview(reviewers, self.final_review, context.checkpoint) if reviewers else BaseEngine.ABSENT
        )
        if (
            review is BaseEngine.ABSENT
            and context.checkpoint is not BaseEngine.ABSENT
            and LoopReview.has_contract(CheckpointContent.from_checkpoint(context.checkpoint).header)
        ):
            raise ValueError(LoopEngine.REVIEW_DISABLED)
        if (
            context.output_step_id is BaseEngine.ABSENT or context.checkpoint_scope is not BaseEngine.ABSENT
        ) and context.checkpoint is BaseEngine.ABSENT:
            yield EngineEvent(
                EngineEventType.CHECKPOINT,
                metadata=CheckpointWrite.initialize_event(
                    LoopEngine.checkpoint_name,
                    LoopCheckpointHeader.create(
                        format=self.CHECKPOINT_FORMAT,
                        binding=self._resume_binding,
                        conversation=CheckpointConversation(
                            tuple((m.id for m in context.messages)), context.run.input_message_id
                        ).to_dict(),
                        review=LoopReview.header_projection(review),
                    ),
                ).to_dict(),
            )
        async with aclosing(self.open_instructions(context)) as events:
            async for event in events:
                yield event
        # 도메인별 정책/저장은 처리기가 소유한다. 각 호출의 세션은 다른 Run/Agent와 공유하지 않는다.
        processor_context = (
            replace(context, output_visibility=OutputVisibility.INTERNAL)
            if review is not BaseEngine.ABSENT
            else context
        )
        async with CompletionPipeline(
            context.capabilities.get(COMPLETION_PROCESSORS, ()),
            processor_context,
            validate_parameters=self.completion_adapter.validate_processing,
            review_policy=self.final_review,
        ) as processors:
            if processors.reviewers != reviewers:
                raise ValueError(LoopEngine.CHANGED_REVIEWERS)
            async with aclosing(self._iterate(context, params, messages, processors, review)) as events:
                async for event in events:
                    yield event

    @staticmethod
    def _candidate_event(event, review):
        if review is BaseEngine.ABSENT:
            return event
        if (
            event.type == EngineEventType.OUTPUT
            and event.output is not BaseEngine.ABSENT
            and event.output.step_id is BaseEngine.ABSENT
        ):
            raise ValueError(LoopEngine.UNOWNED_CANDIDATE)
        return replace(
            event,
            delta=(
                replace(event.delta, visibility=OutputVisibility.INTERNAL)
                if event.delta is not BaseEngine.ABSENT
                else BaseEngine.ABSENT
            ),
            output=(
                replace(event.output, visibility=OutputVisibility.INTERNAL)
                if event.output is not BaseEngine.ABSENT
                else BaseEngine.ABSENT
            ),
        )

    async def _iterate(self, context, params, messages, processors, review=BaseEngine.ABSENT):
        """반복마다 입력 사본을 만들고 관찰 훅을 Tool 효과보다 먼저 처리한다."""
        seen_call_ids: set[str] = set()
        continuations = LoopReview.INITIAL_CONTINUATIONS
        durable = (
            context.output_step_id is BaseEngine.ABSENT or context.checkpoint_scope is not BaseEngine.ABSENT
        )
        records = (
            deepcopy(CheckpointRecords.from_checkpoint(context.checkpoint).records)
            if durable and context.checkpoint
            else {}
        )
        inbox = context.steering
        for iteration in count(LoopIterationRecord.FIRST_ITERATION):
            if self.max_iterations is not BaseEngine.ABSENT and iteration > self.max_iterations:
                raise RuntimeError(LoopEngine.ITERATION_EXCEEDED)
            instruction_key = LoopInstructionBoundary.key(iteration)
            if inbox is not BaseEngine.ABSENT:
                if instruction_key not in records:
                    async with aclosing(
                        self.select_instructions(
                            context, checkpoint=self.checkpoint_name, boundary=instruction_key
                        )
                    ) as events:
                        async for event in events:
                            yield event
                    records[instruction_key] = InstructionCheckpoint.create(
                        inbox.scope, (m.id for m in inbox.messages)
                    ).to_dict()
                selected = [
                    inbox.history[i] for i in InstructionCheckpoint.read(records[instruction_key]).message_ids
                ]
                messages.extend(
                    CompletionMessage(
                        ModelMessage(ModelRole.USER, m.content), m.id, CompletionMessage.CONTINUED_INPUT
                    )
                    for m in selected
                )
            saved = records.get(LoopIterationRecord.key(iteration), {})
            response = (
                ModelResponse.from_dict(LoopIterationRecord.response(saved)) if saved else BaseEngine.ABSENT
            )
            calls, prepared = [], []
            invocation = self._request(context, params)
            request = CompletionRequest(
                invocation.parameters,
                [replace(m, message=copy_model(m.message)) for m in messages],
                iteration,
                tools=invocation.tools,
                tool_mode=invocation.tool_mode,
            )
            if not saved:
                # 부분 답변은 모델 참고 자료일 뿐이다. 완료 응답/Tool 호출/영수증으로 복원하지 않는다.
                partial = [
                    item
                    for item in context.resume_outputs
                    if item.step is not BaseEngine.ABSENT
                    and item.step.kind == StepKind.LLM
                    and item.output.text
                    and LoopStepMetadata(item.step.metadata).checkpoint_name == self.checkpoint_name
                    and LoopStepMetadata(item.step.metadata).checkpoint_scope == context.checkpoint_scope
                    and LoopStepMetadata(item.step.metadata).iteration == iteration
                ]
                if partial:
                    position = next(
                        i
                        for i, message in enumerate(request.messages)
                        if message.source_id == context.run.input_message_id
                    )
                    message = request.messages[position]
                    reference = LoopPriorOutput.encode(partial)
                    request.messages[position] = message.with_text(
                        LoopPriorOutput.append(message.message.text, reference)
                    )
                async with aclosing(processors.prepare(request, messages)) as events:
                    async for event in events:
                        yield self._candidate_event(event, review)
            prepared_request = request.to_model()

            async def complete(_context):
                nonlocal calls, prepared, prepared_request, response

                async def recover(diagnostic, failed_request, attempt):
                    nonlocal prepared_request
                    # Budget filtering can remove only a prior prefix. Locate the
                    # current turn by its unchanged suffix, not by matching text.
                    current = next(
                        i
                        for i, m in enumerate(request.messages)
                        if m.source_id == context.run.input_message_id
                    )
                    index = len(failed_request.messages) - len(request.messages[current:])

                    async def budget(candidate):
                        if context.completion_policy is BaseEngine.ABSENT:
                            return candidate
                        starts = [
                            i for i, m in enumerate(candidate.messages[:index]) if m.role == ModelRole.USER
                        ]
                        return await asyncio.to_thread(
                            context.completion_policy.prepare_turn, candidate, index, starts
                        )

                    value = await CompletionPipeline.recover(
                        failed_request,
                        diagnostic,
                        attempt,
                        current_index=index,
                        decide=self.recover_completion,
                        prepare=budget,
                    )
                    if value is not BaseEngine.ABSENT:
                        prepared_request = copy_model(value)
                    return value

                if context.completion_policy is not BaseEngine.ABSENT:
                    if any(m.continuation for m in messages):
                        prepare_turn = CompletionPolicy.turn_preparer(context.completion_policy)
                        if not callable(prepare_turn):
                            raise ValueError(LoopEngine.UNSUPPORTED_CONTINUATION_POLICY)
                        current_index = next(
                            i
                            for i, m in enumerate(request.messages)
                            if m.source_id == context.run.input_message_id
                        )
                        continued_ids = {m.source_id for m in messages if m.continuation}
                        starts = [
                            i
                            for i, m in enumerate(request.messages[:current_index])
                            if m.message.role == ModelRole.USER and m.source_id not in continued_ids
                        ]
                        prepared_request = await asyncio.to_thread(
                            prepare_turn, prepared_request, current_index, starts
                        )
                    else:
                        prepared_request = await asyncio.to_thread(
                            context.completion_policy.prepare, prepared_request
                        )
                if (
                    inbox is not BaseEngine.ABSENT
                    and InstructionCheckpoint.read(records[instruction_key]).message_ids
                ):
                    async with aclosing(
                        self.apply_instructions(
                            context,
                            checkpoint=self.checkpoint_name,
                            boundary=instruction_key,
                            details=LoopStepMetadata.instruction(iteration=iteration),
                        )
                    ) as events:
                        async for event in events:
                            yield event
                captured = []
                async with aclosing(
                    self.stream_completion(
                        prepared_request,
                        response=captured,
                        provider=self.provider,
                        retry_decider=self.retry_decider,
                        recover_completion=(
                            recover if self.recover_completion is not BaseEngine.ABSENT else BaseEngine.ABSENT
                        ),
                        max_tool_calls=self.max_tool_calls,
                        max_argument_chars=self.max_argument_chars,
                        max_output_chars=self.max_output_chars,
                    )
                ) as deltas:
                    async for text in deltas:
                        yield text
                response = captured[0]
                calls = response.message.calls
                if calls and iteration == self.max_iterations:
                    raise ValueError(LoopEngine.ITERATION_REACHED)
                if any(call.id in seen_call_ids for call in calls):
                    raise ValueError(LoopEngine.DUPLICATE_TOOL_CALL)
                # Validate the whole batch before any tool can have side effects.
                prepared = [
                    context.tools.prepare(
                        call.name,
                        call.arguments,
                        constraints=(
                            context.tool_scope.argument_constraints
                            if context.tool_scope
                            else BaseEngine.ABSENT
                        ),
                    )
                    for call in calls
                ]

            if not saved:
                async with aclosing(
                    self.step(
                        (
                            replace(context, output_visibility=OutputVisibility.INTERNAL)
                            if review is not BaseEngine.ABSENT
                            else context
                        ),
                        complete,
                        name=LoopEngine.COMPLETION_STEP_NAME,
                        kind=StepKind.LLM,
                        timeout_seconds=self.request_timeout,
                        metadata=LoopStepMetadata.completion(
                            iteration=iteration,
                            checkpoint_name=LoopEngine.checkpoint_name,
                            checkpoint_scope=context.checkpoint_scope,
                        ),
                        error_message=LoopEngine.COMPLETION_STEP_ERROR,
                    )
                ) as events:
                    async for event in events:
                        yield event
                # after_completion 검증 실패를 성공한 회차로 저장하지 않는다.
            else:
                calls = response.message.calls
                if any(call.id in seen_call_ids for call in calls):
                    raise ValueError(LoopEngine.DUPLICATE_TOOL_CALL)
                prepared = [
                    context.tools.prepare(
                        c.name,
                        c.arguments,
                        constraints=(
                            context.tool_scope.argument_constraints
                            if context.tool_scope
                            else BaseEngine.ABSENT
                        ),
                    )
                    for c in calls
                ]
            observation = CompletionObservation(
                iteration, prepared_request, response, [m.message for m in messages]
            )
            if not saved:
                async with aclosing(processors.after_completion(observation)) as events:
                    async for event in events:
                        yield self._candidate_event(event, review)
                saved = LoopIterationRecord.create(response, CheckpointStatus.RESPONDED)
                if review is not BaseEngine.ABSENT and not calls:
                    LoopIterationRecord.attach_observation(
                        saved,
                        CompletionReviewObservation(
                            iteration,
                            tuple(prepared_request.messages),
                            response,
                            tuple(m.message for m in messages),
                            prepared_request.tools,
                        ).to_dict(),
                    )
                records[LoopIterationRecord.key(iteration)] = deepcopy(saved)
                if durable:
                    yield self._record(LoopIterationRecord.key(iteration), saved)
            if not calls:
                if review is not BaseEngine.ABSENT:
                    candidate = CompletionReviewObservation.from_dict(LoopIterationRecord.observation(saved))
                    decision_key, attempt_key = LoopReview.decision_key(iteration), LoopReview.attempt_key(
                        iteration
                    )
                    decision = records.get(decision_key)
                    if decision is BaseEngine.ABSENT:
                        attempt = review.reserve(candidate, records)
                        if durable:
                            yield self._record(attempt_key, attempt)
                        records[attempt_key] = attempt
                        decisions = await processors.review_final(candidate)
                        decision = review.decision(attempt, decisions)
                        if durable:
                            yield self._record(decision_key, decision)
                        records[decision_key] = decision
                    if CompletionReview.action_of(decision) == ReviewAction.BLOCK:
                        raise ExecutionLimitError(LoopReview.BLOCKED_CODE, LoopReview.BLOCKED_ERROR)
                    if CompletionReview.action_of(decision) == ReviewAction.CONTINUE:
                        continuations += LoopReview.CONTINUATION_INCREMENT
                        if continuations > ReviewPolicy.max_continuations(review.policy):
                            raise ExecutionLimitError(
                                LoopReview.CONTINUATION_CODE, LoopReview.CONTINUATION_ERROR
                            )
                        messages.append(CompletionMessage(response.message))
                        messages.append(
                            CompletionMessage(
                                ModelMessage(ModelRole.USER, review.feedback(decision)),
                                continuation=CompletionMessage.CONTINUED_INPUT,
                            )
                        )
                        continue
                    observation = CompletionObservation(
                        iteration,
                        replace(prepared_request, messages=list(candidate.messages), tools=candidate.tools),
                        response,
                        list(candidate.original_messages),
                    )
                if inbox is not BaseEngine.ABSENT:
                    next_key = LoopInstructionBoundary.key(
                        iteration + LoopIterationRecord.ITERATION_INCREMENT
                    )
                    if next_key not in records:
                        allowed = self.max_iterations is BaseEngine.ABSENT or iteration < self.max_iterations
                        async with aclosing(
                            self.select_instructions(
                                context,
                                checkpoint=self.checkpoint_name,
                                boundary=next_key,
                                final=BaseEngine.FINAL_BOUNDARY,
                                allow_continue=allowed,
                                reason=(
                                    BaseEngine.ABSENT if allowed else LoopInstructionBoundary.ITERATION_LIMIT
                                ),
                            )
                        ) as events:
                            async for event in events:
                                yield event
                        following = [m.id for m in inbox.messages]
                        if following:
                            records[next_key] = InstructionCheckpoint.create(inbox.scope, following).to_dict()
                    else:
                        following = InstructionCheckpoint.read(records[next_key]).message_ids
                    if following:
                        messages.append(CompletionMessage(response.message))
                        continue
                async with aclosing(processors.finish(observation)) as events:
                    async for event in events:
                        yield self._candidate_event(event, review)
                yield self.output_event(context, EngineOutput(text=response.message.text))
                return
            messages.append(CompletionMessage(response.message))
            for call, (tool, arguments) in zip(calls, prepared):
                seen_call_ids.add(call.id)
                key = LoopToolRecord.key(iteration, call.id)
                stored = records.get(key, {})
                result = deepcopy(LoopToolRecord(stored).result_or_empty)
                result_ref = (
                    ResourceRef(
                        ResourceKind.STEP,
                        LoopToolRecord(stored).step_id,
                        run_id=LoopToolRecord(stored).run_id,
                        step_id=LoopToolRecord(stored).step_id,
                    )
                    if LoopCheckpointRecord(stored).optional_status == CheckpointStatus.COMPLETED
                    else BaseEngine.ABSENT
                )
                executor = ToolExecutor(
                    timeout_seconds=self.tool_timeout, max_output_chars=self.max_output_chars
                )
                if LoopCheckpointRecord(stored).optional_status != CheckpointStatus.COMPLETED:
                    container = ResumableHandlerContract.declared(tool.handler)
                    if durable:
                        yield self._record(
                            key,
                            LoopToolRecord.started(
                                status=CheckpointStatus.STARTED,
                                requires_retry=not container,
                                container=container,
                                name=tool.name,
                                arguments=arguments,
                            ),
                        )
                    try:
                        outer_step = BaseEngine.ABSENT
                        async with aclosing(
                            self.execute_tool(
                                context,
                                tool,
                                arguments,
                                result=result,
                                executor=executor,
                                metadata=LoopStepMetadata.tool(
                                    iteration=iteration,
                                    call_id=call.id,
                                    checkpoint_name=LoopEngine.checkpoint_name,
                                ),
                                checkpoint_key=key,
                            )
                        ) as events:
                            async for event in events:
                                if (
                                    outer_step is BaseEngine.ABSENT
                                    and event.type == EngineEventType.STEP_STARTED
                                ):
                                    outer_step = event.step_id
                                if (
                                    event.type == EngineEventType.STEP_COMPLETED
                                    and event.step_id == outer_step
                                ):
                                    result_ref = ResourceRef(
                                        ResourceKind.STEP,
                                        event.step_id,
                                        run_id=context.run.id,
                                        step_id=event.step_id,
                                    )
                                if (
                                    durable
                                    and event.type == EngineEventType.STEP_COMPLETED
                                    and event.step_id == outer_step
                                ):
                                    event = replace(
                                        event,
                                        checkpoint=self._record(
                                            key,
                                            LoopToolRecord.completed(
                                                status=CheckpointStatus.COMPLETED,
                                                result=result,
                                                step_id=event.step_id,
                                                run_id=context.run.id,
                                            ),
                                        ),
                                    )
                                yield event
                                if event.type == EngineEventType.PAUSED:
                                    return
                    except ToolApprovalRequired as error:
                        if not durable:
                            raise
                        yield self._record(
                            key,
                            LoopToolRecord.waiting(
                                status=CheckpointStatus.WAITING,
                                name=tool.name,
                                arguments=arguments,
                                prompt=str(error),
                                interaction=error.request.bind(LoopEngine.checkpoint_name, key).to_dict(),
                            ),
                        )
                        yield EngineEvent(
                            EngineEventType.PAUSED,
                            metadata=LoopToolRecord.paused(LoopEngine.checkpoint_name, key),
                        )
                        return
                    except ToolExecutionError as error:
                        if durable and error.effect == ToolExecutionError.NO_EFFECT:
                            yield self._record(
                                key,
                                LoopToolRecord.not_applied(
                                    status=CheckpointStatus.NOT_APPLIED, error=str(error)
                                ),
                            )
                        raise
                else:
                    # 재사용도 새 Run에서 관찰·원본 조회할 수 있게 기록한다. 핸들러는 호출하지 않는다.
                    async def reuse(_context):
                        return EngineOutput(
                            data=deepcopy(ToolEventResult(result).value), visibility=OutputVisibility.INTERNAL
                        )

                    async with aclosing(
                        self.step(
                            context,
                            reuse,
                            name=tool.name,
                            kind=StepKind.TOOL,
                            metadata=LoopStepMetadata.reuse(
                                iteration=iteration,
                                call_id=call.id,
                                reused=LoopStepMetadata.REUSED_VALUE,
                                source_run_id=LoopToolRecord(stored).run_id,
                                source_step_id=LoopToolRecord(stored).step_id,
                            ),
                        )
                    ) as events:
                        async for event in events:
                            yield event
                messages.append(
                    CompletionMessage(
                        ModelMessage(
                            ModelRole.TOOL,
                            result=ToolResult(call.id, ToolEventResult(result).content, tool.name),
                        ),
                        result_ref=result_ref,
                    )
                )
            if durable:
                yield self._record(
                    LoopIterationRecord.key(iteration),
                    LoopIterationRecord.create(response, CheckpointStatus.COMPLETED),
                )
