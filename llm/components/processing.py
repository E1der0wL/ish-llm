"""컴포넌트의 모델 입력 변환·응답 관찰 계약. 영속 도메인이나 특정 컴포넌트를 알지 않는다."""

import math
from typing import AsyncIterator, Optional, Protocol, TYPE_CHECKING

from contextlib import aclosing
from dataclasses import dataclass
from asyncio import timeout
from llm.providers.parameters import copy_params

if TYPE_CHECKING:
    from llm.engines.base import EngineContext, EngineEvent


@dataclass(frozen=True, slots=True)
class CompletionMessage:
    """모델용 메시지와 원본 출처. 추가 참고자료는 source_id=None이며 ID는 공급자에게 보내지 않는다."""

    value: dict
    source_id: Optional[str] = None
    # 같은 작업에 추가된 USER 입력. provider kwargs에는 포함하지 않는다.
    continuation: bool = False


@dataclass(slots=True)
class CompletionRequest:
    """한 모델 호출의 가변 사본. 메시지 구조와 공급자 인자를 분리한다."""

    parameters: dict
    messages: list[CompletionMessage]
    iteration: int
    # 완료된 현재 작업의 접두 Tool 교환만 명시적으로 압축할 수 있다. 공급자 인자가 아니다.
    compacted_tool_calls: tuple[str, ...] = ()

    def to_kwargs(self) -> dict:
        return {**copy_params(self.parameters), "messages": [copy_params(m.value) for m in self.messages]}


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    """예산 적용 후 모델에 전달한 요청과 응답, 가공 전 대화의 독립 사본.

    공급자 어댑터의 stream_options 등 내부 기본값 적용 전 요청이다. 관찰자가 사본을
    수정해도 다른 처리기·Tool 실행·최종 출력에는 영향을 주지 않는다.
    """

    iteration: int
    request: dict
    response: dict
    original_messages: list[dict]

    def snapshot(self):
        return CompletionObservation(self.iteration, copy_params(self.request),
                                     copy_params(self.response), copy_params(self.original_messages))


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

    async def finish(self, observation: CompletionObservation) -> AsyncIterator["EngineEvent"]:
        if False:
            yield

    async def aclose(self, error: Optional[BaseException]) -> None:
        pass


class CompletionProcessor(Protocol):
    """같은 priority에서는 name 순으로 실행한다. close_timeout은 협력적 정리 기한이다."""

    name: str
    priority: int
    close_timeout: float

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
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("Completion processor close_timeout must be positive and finite")
        if not callable(getattr(item, "session", None)):
            raise TypeError("Completion processor requires session(context)")
    return tuple(sorted(values, key=lambda item: (item.priority, item.name)))


class CompletionPipeline:
    """한 엔진 호출에 귀속되는 처리기 수명·순서·입력 계약. 상태를 엔진 인스턴스에 두지 않는다."""

    def __init__(self, processors, context):
        self.processors = ordered_processors(processors)
        self.context = context
        self.sessions = []

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
    def _validate(request, original, parameters, current_id):
        if not isinstance(request.parameters, dict) or "messages" in request.parameters:
            raise ValueError("Completion messages must use CompletionRequest.messages")
        # 처리기가 Tool 권한·반복 계약을 우회하거나 메인 모델 자동 재시도를 추가할 수 없다.
        for key in ("tools", "tool_choice", "functions", "function_call", "stream", "n", "num_retries"):
            if (key in request.parameters) != (key in parameters) or request.parameters.get(key) != parameters.get(key):
                raise ValueError(f"Completion processor cannot change {key}")
        originals = {m.source_id: m.value for m in original if m.source_id is not None}
        seen = set()
        for message in request.messages:
            if not isinstance(message, CompletionMessage) or not isinstance(message.value, dict):
                raise TypeError("Completion messages require CompletionMessage values")
            if message.source_id is not None:
                if message.source_id not in originals or message.source_id in seen:
                    raise ValueError("Unknown or duplicate completion message source")
                seen.add(message.source_id)
                if message.value.get("role") != originals[message.source_id].get("role"):
                    raise ValueError("Completion processor cannot change an original message role")
        if current_id not in seen:
            raise ValueError("Completion processor must preserve the current user message")
        order = [m.source_id for m in original if m.source_id in seen]
        if order != [m.source_id for m in request.messages if m.source_id is not None]:
            raise ValueError("Completion processor must preserve original message order")
        group = []
        for message in original:
            if message.value.get("role") == "user" and not message.continuation:
                kept = [m.source_id in seen for m in group]
                if any(kept) and not all(kept):
                    raise ValueError("Completion processor must remove whole prior turns")
                group = []
            if message.source_id == current_id:
                break
            if message.source_id is not None and message.value.get("role") not in ("system", "developer"):
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
                value = message.value
                if value.get("role") == "assistant" and value.get("tool_calls") and not pending:
                    pending = {c["id"] for c in value["tool_calls"]}
                    ids.extend(c["id"] for c in value["tool_calls"])
                elif value.get("role") == "tool" and value.get("tool_call_id") in pending:
                    pending.remove(value["tool_call_id"])
                else:
                    raise ValueError("Compaction requires complete Tool exchanges")
            if pending or tuple(ids) != request.compacted_tool_calls:
                raise ValueError("Compaction source IDs do not match completed exchanges")
            if request.messages[request_index].value.get("content") == original[index].value.get("content"):
                raise ValueError("Compaction must preserve a summary in the current input")
            tail = tail[removed:]
        if len(tail) != len(actual):
            raise ValueError("Completion processor cannot alter the active tool transcript structure")
        for before, after in zip(tail, actual):
            left, right = copy_params(before.value), copy_params(after.value)
            if left.get("role") == "tool":
                left.pop("content", None)
                right.pop("content", None)
            if before.source_id != after.source_id or left != right:
                raise ValueError("Completion processor cannot alter active tool calls or identifiers")

    async def prepare(self, request: CompletionRequest, original):
        parameters = copy_params(request.parameters)
        for _, session in self.sessions:
            async with aclosing(session.prepare(request)) as events:
                async for event in events:
                    yield event
            self._validate(request, original, parameters, self.context.run.input_message_id)

    async def after_completion(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.after_completion(observation.snapshot())) as events:
                async for event in events:
                    yield event

    async def finish(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.finish(observation.snapshot())) as events:
                async for event in events:
                    yield event
