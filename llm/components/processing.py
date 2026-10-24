"""컴포넌트의 모델 입력 변환·응답 관찰 계약. 영속 도메인이나 특정 컴포넌트를 알지 않는다."""

from .completion_values import (
    CompletionMessage,
    CompletionRequest,
    CompletionObservation,
    CompletionRecovery,
    CompletionRepair,
)
from .completion_input import CompletionInput

from llm.components.review import CompletionReview, ReviewPolicy, ReviewerIdentity, reviewer_identity
from llm.policies import ExecutionLimitError

import math
from typing import AsyncIterator, Optional, Protocol, TYPE_CHECKING

from contextlib import aclosing
from asyncio import timeout


COMPLETION_PROCESSORS = "completion_processors"


if TYPE_CHECKING:
    from llm.engines.base import EngineContext, EngineEvent


class CompletionSession:
    """호출별 세션 기본 클래스. 필요한 훅만 재정의한다.

    prepare는 매 모델 호출 전 변환, after_completion은 Tool 실행 전 모든 정상 모델
    응답 관찰, finish는 최종 정상 응답 후 처리다. aclose(error)는 항상 역순 호출하며
    이벤트를 반환하지 않는다. 취소를 삼키거나 Run/Step 파일을 직접 쓰면 안 된다.
    """

    EMITS_DEFAULT_EVENTS = False
    MISSING_REVIEW = "Declared final reviewers must implement review_final"

    async def prepare(self, request: CompletionRequest) -> AsyncIterator["EngineEvent"]:
        if self.EMITS_DEFAULT_EVENTS:
            yield

    async def after_completion(self, observation: CompletionObservation) -> AsyncIterator["EngineEvent"]:
        if self.EMITS_DEFAULT_EVENTS:
            yield

    async def review_final(self, observation):
        raise NotImplementedError(self.MISSING_REVIEW)

    async def finish(self, observation: CompletionObservation) -> AsyncIterator["EngineEvent"]:
        if self.EMITS_DEFAULT_EVENTS:
            yield

    async def aclose(self, error: Optional[BaseException]) -> None:
        pass


class CompletionProcessor(Protocol):
    """같은 priority에서는 name 순으로 실행한다. close_timeout은 협력적 정리 기한이다."""

    CLOSE_TIMEOUT = "close_timeout"

    name: str
    priority: int
    close_timeout: Optional[float]

    def session(self, context: "EngineContext") -> CompletionSession: ...

    SESSION = session.__name__


# Capture public method names once; replacing a selected hook does not rename it.


class CompletionHook:
    CLOSE = CompletionSession.aclose.__name__
    PREPARE = CompletionSession.prepare.__name__
    AFTER_COMPLETION = CompletionSession.after_completion.__name__
    FINISH = CompletionSession.finish.__name__
    REVIEW_FINAL = CompletionSession.review_final.__name__
    BOUND_FUNCTION = "__func__"


class ProcessorRegistration:
    ABSENT = None
    MIN_CLOSE_TIMEOUT = 0
    MISSING_REVIEWERS = "Active final_review requires declared reviewers"
    INVALID_NAME = "Completion processor names must be unique nonempty strings"
    INVALID_PRIORITY = "Completion processor priority must be an integer"
    INVALID_CLOSE_TIMEOUT = "Completion processor close_timeout must be None or positive and finite"
    MISSING_SESSION = "Completion processor requires session(context)"


def ordered_processors(processors):
    """세션을 만들지 않고 등록을 검증한다. 빈 컬렉션도 유효하다."""
    values, names = list(processors), set()
    for item in values:
        name = getattr(item, ReviewerIdentity.name.__name__, ProcessorRegistration.ABSENT)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(ProcessorRegistration.INVALID_NAME)
        names.add(name)
        if type(getattr(item, ReviewerIdentity.priority.__name__, ProcessorRegistration.ABSENT)) is not int:
            raise ValueError(ProcessorRegistration.INVALID_PRIORITY)
        duration = getattr(item, CompletionProcessor.CLOSE_TIMEOUT, ProcessorRegistration.ABSENT)
        if (
            not hasattr(item, CompletionProcessor.CLOSE_TIMEOUT)
            or duration is not ProcessorRegistration.ABSENT
            and (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= ProcessorRegistration.MIN_CLOSE_TIMEOUT
            )
        ):
            raise ValueError(ProcessorRegistration.INVALID_CLOSE_TIMEOUT)
        if not callable(getattr(item, CompletionProcessor.SESSION, ProcessorRegistration.ABSENT)):
            raise TypeError(ProcessorRegistration.MISSING_SESSION)
    return tuple(sorted(values, key=lambda item: (item.priority, item.name)))


def review_identities(processors):
    values = [
        reviewer_identity(processor)
        for processor in ordered_processors(processors)
        if hasattr(processor, ReviewerIdentity.REVISION_ATTRIBUTE)
    ]
    if not values:
        raise ValueError(ProcessorRegistration.MISSING_REVIEWERS)
    return values


class CompletionPipeline:
    """한 엔진 호출에 귀속되는 처리기 수명·순서·입력 계약. 상태를 엔진 인스턴스에 두지 않는다."""

    ABSENT = None
    FIRST_FAILURE = 0
    INITIAL_FEEDBACK_SIZE = 0
    PROPAGATE_ERROR = False
    MISSING_CLOSE = "Completion session requires aclose(error)"
    INVALID_HOOKS = "Invalid completion session hooks"
    MISSING_REVIEW = "Declared reviewer requires an implemented review_final hook"
    REVIEWER_CHANGED = "Reviewer identity changed during execution"
    REVIEW_TIMEOUT = "Final review hook timed out"
    INVALID_VERDICT = "Final review hook must return CompletionReview"
    FEEDBACK_EXCEEDED = "Final review feedback bound exceeded"
    REVIEW_TIMEOUT_CODE = "completion_review_timeout"

    def __init__(self, processors, context, *, validate_parameters=ABSENT, review_policy=ABSENT):
        self.processors = ordered_processors(processors)
        self.context = context
        self.sessions = []
        self.validate_parameters = validate_parameters
        self.review_policy = ReviewPolicy.read(review_policy)
        self.reviewers = review_identities(self.processors) if self.review_policy is not self.ABSENT else []

    async def __aenter__(self):
        try:
            for processor in self.processors:
                session = processor.session(self.context)
                # 생성자는 자원을 획득하지 않는다. 비동기 자원 획득은 prepare에서 한다.
                if not callable(getattr(session, CompletionHook.CLOSE, self.ABSENT)):
                    raise TypeError(self.MISSING_CLOSE)
                self.sessions.append((processor, session))
                if any(
                    not callable(getattr(session, name, self.ABSENT))
                    for name in (
                        CompletionHook.PREPARE,
                        CompletionHook.AFTER_COMPLETION,
                        CompletionHook.FINISH,
                    )
                ):
                    raise TypeError(self.INVALID_HOOKS)
                if self.review_policy is not self.ABSENT and hasattr(
                    processor, ReviewerIdentity.REVISION_ATTRIBUTE
                ):
                    hook = getattr(session, CompletionHook.REVIEW_FINAL, self.ABSENT)
                    if (
                        not callable(hook)
                        or getattr(hook, CompletionHook.BOUND_FUNCTION, self.ABSENT)
                        is CompletionSession.review_final
                    ):
                        raise TypeError(self.MISSING_REVIEW)
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
        if failures and error is self.ABSENT:
            raise failures[self.FIRST_FAILURE]
        return self.PROPAGATE_ERROR

    _instruction_text = staticmethod(CompletionInput.instruction_text)

    recover = staticmethod(CompletionInput.recover)

    _validate = staticmethod(CompletionInput.validate)

    async def prepare(self, request: CompletionRequest, original):
        invocation = request.to_model()
        self._validate(request, original, invocation, self.context.run.input_message_id)
        for _, session in self.sessions:
            async with aclosing(session.prepare(request)) as events:
                async for event in events:
                    yield event
            self._validate(request, original, invocation, self.context.run.input_message_id)
            if self.validate_parameters is not self.ABSENT:
                self.validate_parameters(request.parameters, original=invocation.parameters)

    async def after_completion(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.after_completion(observation.snapshot())) as events:
                async for event in events:
                    yield event

    async def review_final(self, observation):
        if self.review_policy is self.ABSENT:
            return []
        decisions, feedback_size = [], self.INITIAL_FEEDBACK_SIZE
        for processor, session in self.sessions:
            if not hasattr(processor, ReviewerIdentity.REVISION_ATTRIBUTE):
                continue
            identity = reviewer_identity(processor)
            if identity != self.reviewers[len(decisions)]:
                raise ValueError(self.REVIEWER_CHANGED)
            guard = timeout(ReviewPolicy.hook_timeout(self.review_policy))
            try:
                async with guard:
                    verdict = await session.review_final(observation.snapshot())
            except TimeoutError as error:
                if guard.expired():
                    raise ExecutionLimitError(self.REVIEW_TIMEOUT_CODE, self.REVIEW_TIMEOUT) from error
                raise
            if not isinstance(verdict, CompletionReview):
                raise TypeError(self.INVALID_VERDICT)
            feedback_size += len(verdict.feedback)
            if feedback_size > ReviewPolicy.max_feedback_chars(self.review_policy):
                raise ValueError(self.FEEDBACK_EXCEEDED)
            decisions.append(verdict.record(identity))
        return decisions

    async def finish(self, observation: CompletionObservation):
        for _, session in self.sessions:
            async with aclosing(session.finish(observation.snapshot())) as events:
                async for event in events:
                    yield event
