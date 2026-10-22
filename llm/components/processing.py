"""컴포넌트의 모델 입력 변환·응답 관찰 계약. 영속 도메인이나 특정 컴포넌트를 알지 않는다."""

from llm.core.model_protocol import ModelMessage, ModelRequest, ModelResponse, ModelRole, ToolDefinition, ToolMode, copy_model

from llm.components.review import CompletionReview, ReviewPolicy, reviewer_identity
from llm.policies import ExecutionLimitError

import math
import inspect
from typing import AsyncIterator, Optional, Protocol, TYPE_CHECKING

from contextlib import aclosing
from dataclasses import dataclass, replace
from asyncio import timeout
from llm.core.contracts import Diagnostic, ResourceRef


@dataclass(frozen=True, slots=True)
class CompletionRecovery:
    """Application input-repair proposal boundary; editable indexes are not authority."""
    diagnostic: Diagnostic
    request: ModelRequest
    attempt: int
    editable: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompletionRepair:
    """Replace reference bodies only. No SDK, Tool, policy or message-role changes."""
    replacements: dict[int, str]

if TYPE_CHECKING:
    from llm.engines.base import EngineContext, EngineEvent


@dataclass(frozen=True, slots=True)
class CompletionMessage:
    """모델용 메시지와 원본 출처. 추가 참고자료는 source_id=None이며 ID는 공급자에게 보내지 않는다."""

    message: ModelMessage
    source_id: Optional[str] = None
    # 같은 작업에 추가된 USER 입력. provider kwargs에는 포함하지 않는다.
    continuation: bool = False
    # Completed result identity, excluded from provider kwargs. Call IDs are local
    # to an execution scope and cannot identify sibling Agent results in a Run.
    result_ref: Optional[ResourceRef] = None

    def __post_init__(self):
        if not isinstance(self.message, ModelMessage):
            raise TypeError("CompletionMessage requires ModelMessage")
        object.__setattr__(self, "message", copy_model(self.message))

    def with_text(self, text):
        return replace(self, message=self.message.with_text(text))


@dataclass(slots=True)
class CompletionRequest:
    """한 텍스트 모델 호출의 가변 사본. SDK 인자와 출처/압축 계약을 분리한다.

    The current user instruction is text and remains an exact prefix. Active
    compaction appends reference summaries to that body and declares removed
    Tool exchange IDs. This contract does not interpret multimodal content or
    silently translate alternate summary placements.
    """

    parameters: dict
    messages: list[CompletionMessage]
    iteration: int
    # 완료된 현재 작업의 접두 Tool 교환만 명시적으로 압축할 수 있다. 공급자 인자가 아니다.
    compacted_tool_calls: tuple[str, ...] = ()

    tools: tuple[ToolDefinition, ...] = ()
    tool_mode: ToolMode | None = None

    def to_model(self) -> ModelRequest:
        return ModelRequest([m.message for m in self.messages], self.parameters, self.tools, self.tool_mode)


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    """예산 적용 후 모델에 전달한 요청과 응답, 가공 전 대화의 독립 사본.

    공급자 어댑터의 stream_options 등 내부 기본값 적용 전 요청이다. 관찰자가 사본을
    수정해도 다른 처리기·Tool 실행·최종 출력에는 영향을 주지 않는다.
    """

    iteration: int
    request: ModelRequest
    response: ModelResponse
    original_messages: list[ModelMessage]

    def __post_init__(self):
        if not isinstance(self.request, ModelRequest) or not isinstance(self.response, ModelResponse):
            raise TypeError("Completion observation requires common request and response")
        if any(not isinstance(message, ModelMessage) for message in self.original_messages):
            raise TypeError("Completion observation requires common original messages")
        for name in ("request", "response", "original_messages"):
            object.__setattr__(self, name, copy_model(getattr(self, name)))

    def snapshot(self):
        return CompletionObservation(self.iteration, copy_model(self.request),
                                     copy_model(self.response), copy_model(self.original_messages))


class CompletionSession:
    """호출별 세션 기본 클래스. 필요한 훅만 재정의한다.

    prepare는 매 모델 호출 전 변환, after_completion은 Tool 실행 전 모든 정상 모델
    응답 관찰, finish는 최종 정상 응답 후 처리다. aclose(error)는 항상 역순 호출하며
    이벤트를 반환하지 않는다. 취소를 삼키거나 Run/Step 파일을 직접 쓰면 안 된다.
    """

    async def prepare(self, request: CompletionRequest) -> AsyncIterator["EngineEvent"]:
        if False:
            yield

    async def after_completion(self, observation: CompletionObservation) -> AsyncIterator["EngineEvent"]:
        if False:
            yield

    async def review_final(self, observation):
        raise NotImplementedError("Declared final reviewers must implement review_final")

    async def finish(self, observation: CompletionObservation) -> AsyncIterator["EngineEvent"]:
        if False:
            yield

    async def aclose(self, error: Optional[BaseException]) -> None:
        pass


class CompletionProcessor(Protocol):
    """같은 priority에서는 name 순으로 실행한다. close_timeout은 협력적 정리 기한이다."""

    name: str
    priority: int
    close_timeout: Optional[float]

    def session(self, context: "EngineContext") -> CompletionSession: ...


def ordered_processors(processors):
    """세션을 만들지 않고 등록을 검증한다. 빈 컬렉션도 유효하다."""
    values, names = list(processors), set()
    for item in values:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Completion processor names must be unique nonempty strings")
        names.add(name)
        if type(getattr(item, "priority", None)) is not int:
            raise ValueError("Completion processor priority must be an integer")
        duration = getattr(item, "close_timeout", None)
        if not hasattr(item, "close_timeout") or duration is not None and (
                isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("Completion processor close_timeout must be None or positive and finite")
        if not callable(getattr(item, "session", None)):
            raise TypeError("Completion processor requires session(context)")
    return tuple(sorted(values, key=lambda item: (item.priority, item.name)))


def review_identities(processors):
    values = [reviewer_identity(processor) for processor in ordered_processors(processors)
              if hasattr(processor, "review_revision")]
    if not values:
        raise ValueError("Active final_review requires declared reviewers")
    return values


class CompletionPipeline:
    """한 엔진 호출에 귀속되는 처리기 수명·순서·입력 계약. 상태를 엔진 인스턴스에 두지 않는다."""

    def __init__(self, processors, context, *, validate_parameters=None, review_policy=None):
        self.processors = ordered_processors(processors)
        self.context = context
        self.sessions = []
        self.validate_parameters = validate_parameters
        self.review_policy = ReviewPolicy.read(review_policy)
        self.reviewers = review_identities(self.processors) if self.review_policy is not None else []

    async def __aenter__(self):
        try:
            for processor in self.processors:
                session = processor.session(self.context)
                # 생성자는 자원을 획득하지 않는다. 비동기 자원 획득은 prepare에서 한다.
                if not callable(getattr(session, "aclose", None)):
                    raise TypeError("Completion session requires aclose(error)")
                self.sessions.append((processor, session))
                if any(not callable(getattr(session, name, None))
                       for name in ("prepare", "after_completion", "finish")):
                    raise TypeError("Invalid completion session hooks")
                if self.review_policy is not None and hasattr(processor, "review_revision"):
                    hook = getattr(session, "review_final", None)
                    if not callable(hook) or getattr(hook, "__func__", None) is CompletionSession.review_final:
                        raise TypeError("Declared reviewer requires an implemented review_final hook")
        except BaseException as error:
            await self.__aexit__(type(error), error, error.__traceback__)
            raise
        return self

    async def __aexit__(self, exc_type, error, traceback):
        failures = []
        while self.sessions:
            processor, session = self.sessions.pop()
            try:
                # ContextVar 토큰과 asyncio.Task 소유 자원도 정리할 수 있도록 같은 Task에서 닫는다.
                async with timeout(processor.close_timeout):
                    await session.aclose(error)
            except BaseException as failure:
                failures.append(failure)
        # 이미 진행 중인 오류/취소를 정리 오류로 덮어쓰지 않는다. 정상 경로의 정리 실패는 실패다.
        if failures and error is None:
            raise failures[0]
        return False

    @staticmethod
    def _instruction_text(message):
        content = message.content
        if not isinstance(content, str):
            raise ValueError("Text completion processing requires a string current instruction")
        return content

    @staticmethod
    async def recover(request, diagnostic, attempt, *, current_index, decide, prepare=None):
        """Repair this prepared invocation, without touching persistent input/receipts.

        Current instructions (including embedded data without an explicit separate
        reference boundary) stay whole. Earlier bodies and Tool result bodies can
        be shortened; call IDs/arguments/order and all other fields stay exact.
        """
        original = copy_model(request)
        messages = original.messages
        if not 0 <= current_index < len(messages) or messages[current_index].role != ModelRole.USER:
            raise ValueError("Recovery requires the current user input")
        CompletionPipeline._instruction_text(messages[current_index])
        editable = tuple(i for i, message in enumerate(messages)
            if (isinstance(message.content, str) or message.result is not None)
            and message.role not in (ModelRole.SYSTEM, ModelRole.DEVELOPER)
            and (i < current_index or message.role == ModelRole.TOOL))
        if not editable:
            return None
        selected = decide(CompletionRecovery(Diagnostic.from_dict(diagnostic.to_dict()), copy_model(original), attempt, editable))
        if inspect.isawaitable(selected):
            selected = await selected
        if selected is None:
            return None
        if not isinstance(selected, CompletionRepair) or not isinstance(selected.replacements, dict):
            raise TypeError("Completion recovery requires CompletionRepair or None")
        candidate = copy_model(original)
        for index, content in selected.replacements.items():
            if (type(index) is not int or index not in editable or not isinstance(content, str)
                    or not content.strip()):
                raise ValueError("Recovery can only replace declared reference bodies")
            candidate.messages[index] = candidate.messages[index].with_text(content)
        if sum(len(m.text) for m in candidate.messages) >= sum(len(m.text) for m in messages):
            raise ValueError("Context repair must reduce reference content")
        tail = copy_model(candidate.messages[current_index:])
        instructions = [m for m in messages if m.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
        if prepare is not None:
            candidate = await prepare(candidate)
        # Budget preparation may drop whole prior turns, never the protected tail.
        if (candidate.messages[-len(tail):] != tail or
                [m for m in candidate.messages if m.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)] != instructions or
                candidate.parameters != original.parameters or candidate.tools != original.tools or
                candidate.tool_mode != original.tool_mode):
            raise ValueError("Recovery budget processing removed current input or Tool structure")
        return candidate

    @staticmethod
    def _validate(request, original, invocation, current_id):
        current = request.to_model()
        current.validate_exchanges()
        if current.tools != invocation.tools or current.tool_mode != invocation.tool_mode:
            raise ValueError("Completion processor cannot change Tool selection")
        originals = {m.source_id: m.message for m in original if m.source_id is not None}
        seen = set()
        for message in request.messages:
            if not isinstance(message, CompletionMessage) or not isinstance(message.message, ModelMessage):
                raise TypeError("Completion messages require CompletionMessage values")
            if message.source_id is not None:
                if message.source_id not in originals or message.source_id in seen:
                    raise ValueError("Unknown or duplicate completion message source")
                seen.add(message.source_id)
                if message.message.role != originals[message.source_id].role:
                    raise ValueError("Completion processor cannot change an original message role")
        if current_id not in seen:
            raise ValueError("Completion processor must preserve the current user message")
        current = next(m.message for m in request.messages if m.source_id == current_id)
        before = originals[current_id]
        # Existing processors can append explicitly configured reference context;
        # they cannot replace the original instruction with a summary or an ID.
        if not CompletionPipeline._instruction_text(current).startswith(CompletionPipeline._instruction_text(before)):
            raise ValueError("Completion processor must preserve the current instruction body")
        protected = [m.message for m in original if m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
        supplied = [m.message for m in request.messages if m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
        candidates = iter(supplied)
        if any(not any(value == candidate for candidate in candidates) for value in protected):
            raise ValueError("Completion processor must preserve applied instructions")
        order = [m.source_id for m in original if m.source_id in seen]
        if order != [m.source_id for m in request.messages if m.source_id is not None]:
            raise ValueError("Completion processor must preserve original message order")
        group = []
        for message in original:
            if message.message.role == ModelRole.USER and not message.continuation:
                kept = [m.source_id in seen for m in group]
                if any(kept) and not all(kept):
                    raise ValueError("Completion processor must remove whole prior turns")
                group = []
            if message.source_id == current_id:
                break
            if message.source_id is not None and message.message.role not in (ModelRole.SYSTEM, ModelRole.DEVELOPER):
                group.append(message)
        # 현재 요청 이후의 Tool 호출/결과는 쌍과 순서를 유지한다. 결과 본문의 압축만 허용한다.
        index = next(i for i, m in enumerate(original) if m.source_id == current_id)
        tail = original[index + 1:]
        request_index = next(i for i, m in enumerate(request.messages) if m.source_id == current_id)
        actual = request.messages[request_index + 1:]
        if request.compacted_tool_calls:
            removed = len(tail) - len(actual)
            if removed <= 0:
                raise ValueError("Compaction requires a removed transcript prefix")
            prefix, ids, pending = tail[:removed], [], set()
            for message in prefix:
                value = message.message
                if value.role == ModelRole.ASSISTANT and value.calls and not pending:
                    pending = {c.id for c in value.calls}
                    ids.extend(c.id for c in value.calls)
                elif value.result is not None and value.result.call_id in pending:
                    pending.remove(value.result.call_id)
                else:
                    raise ValueError("Compaction requires complete Tool exchanges")
            if pending or tuple(ids) != request.compacted_tool_calls:
                raise ValueError("Compaction source IDs do not match completed exchanges")
            if request.messages[request_index].message.content == original[index].message.content:
                raise ValueError("Compaction must preserve a summary in the current input")
            tail = tail[removed:]
        if len(tail) != len(actual):
            raise ValueError("Completion processor cannot alter the active tool transcript structure")
        for before, after in zip(tail, actual):
            left, right = copy_model(before.message), copy_model(after.message)
            if left.result is not None:
                left = left.with_text("")
                right = right.with_text("")
            if before.source_id != after.source_id or before.result_ref != after.result_ref or left != right:
                raise ValueError("Completion processor cannot alter active tool calls or identifiers")

    async def prepare(self, request: CompletionRequest, original):
        invocation = request.to_model()
        self._validate(request, original, invocation, self.context.run.input_message_id)
        for _, session in self.sessions:
            async with aclosing(session.prepare(request)) as events:
                async for event in events:
                    yield event
            self._validate(request, original, invocation, self.context.run.input_message_id)
            if self.validate_parameters is not None:
                self.validate_parameters(request.parameters, original=invocation.parameters)

    async def after_completion(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.after_completion(observation.snapshot())) as events:
                async for event in events:
                    yield event

    async def review_final(self, observation):
        if self.review_policy is None:
            return []
        decisions, feedback_size = [], 0
        for processor, session in self.sessions:
            if not hasattr(processor, "review_revision"):
                continue
            identity = reviewer_identity(processor)
            if identity != self.reviewers[len(decisions)]:
                raise ValueError("Reviewer identity changed during execution")
            guard = timeout(self.review_policy["hook_timeout"])
            try:
                async with guard:
                    verdict = await session.review_final(observation.snapshot())
            except TimeoutError as error:
                if guard.expired():
                    raise ExecutionLimitError("completion_review_timeout", "Final review hook timed out") from error
                raise
            if not isinstance(verdict, CompletionReview):
                raise TypeError("Final review hook must return CompletionReview")
            feedback_size += len(verdict.feedback)
            if feedback_size > self.review_policy["max_feedback_chars"]:
                raise ValueError("Final review feedback bound exceeded")
            decisions.append({**identity, "action": str(verdict.action), "feedback": verdict.feedback})
        return decisions

    async def finish(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.finish(observation.snapshot())) as events:
                async for event in events:
                    yield event
