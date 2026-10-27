"""Reusable summary algorithms; callers own selection, model configuration and persistence.

The injected model adapter fills SummaryAnswer and yields its own events.
It owns SDK calls, retries, usage observations and any structured-output decoding.
This module never imports an Engine, SDK, domain model or storage implementation.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, ClassVar


NO_PROGRESS = None


type ModelCall = Callable[[str, dict, dict], AsyncIterator[Any]]
type Measure = Callable[[dict], int]
type FragmentPayload = Callable[[str, int, int, str], dict]


class SummaryAnswer:
    """The model reply field and its validation; no persistence or SDK decoding."""

    FIELD = "summary"
    EXAMPLE = '{"' + FIELD + '":"..."}'
    INVALID = "Summary is empty or exceeds the requested size"

    @classmethod
    def store(cls, answer, text):
        answer[cls.FIELD] = text

    @classmethod
    def read(cls, answer, max_chars):
        text = answer.get(cls.FIELD)
        if not isinstance(text, str) or not text.strip() or len(text) > max_chars:
            raise ValueError(cls.INVALID)
        return text


@dataclass(frozen=True, slots=True)
class SummarySource:
    """Caller-owned immutable text reader; no storage or Component knowledge.

    read(offset, count) returns exactly that character range. The caller verifies
    identity on each read. read_chars is explicit and independent of token budgets.
    """

    total_chars: int
    identity: str
    read_chars: int
    read: Callable[[int, int], Awaitable[str]]

    def __post_init__(self):
        if type(self.total_chars) is not int or self.total_chars < 0 or not isinstance(self.identity, str) or not self.identity:
            raise ValueError("Summary source requires a length and immutable identity")
        if type(self.read_chars) is not int or self.read_chars <= 0 or not callable(self.read):
            raise ValueError("Summary source requires an explicit read size and reader")

    def __len__(self):
        return self.total_chars


class SummaryValidation:
    MINIMUM_POSITIVE = 1
    MINIMUM_SIZE = 0
    MINIMUM_OFFSET = 0
    MAXIMUM = "maximum"
    MAX_CHARS = "max_chars"
    MAX_INPUT_SIZE = "max_input_size"
    MAX_SUMMARY_CHARS = "max_summary_chars"
    MAX_CALLS = "max_calls"
    POSITIVE = "{name} must be a positive integer"
    INVALID_MEASURE = "Summary measure must return a nonnegative integer"
    INVALID_RANGE = "Invalid summary source range"
    INCONSISTENT_MEASURE = "Inconsistent summary size measure"
    INVALID_INPUT = "Summary requires a prompt and object payload"
    INVALID_TEXT = "Summary source and previous summary must be text"
    INVALID_OFFSET = "Invalid summary offset"
    CHANGED_PAYLOAD = "Summary payload changed after size validation"


@dataclass(slots=True)
class SummaryResult:
    """In-memory outcome, not a durable receipt. Offset counts source characters.

    A partial result covers only ``text[:offset]`` of the caller's source. The
    caller must bind an offset/summary to its source and configuration revision
    before storing or resuming it. Zero/empty fields represent no completed work,
    not configuration defaults. Calls counts validated completions; failed SDK
    attempts remain the adapter's observations. ``blocked`` means no next fragment fits.
    """

    EMPTY_TEXT: ClassVar[str] = ""
    INITIAL_OFFSET: ClassVar[int] = 0
    NO_CALLS: ClassVar[int] = 0
    SINGLE_CALL: ClassVar[int] = 1
    INCOMPLETE: ClassVar[bool] = False
    COMPLETE: ClassVar[bool] = True
    UNBLOCKED: ClassVar[bool] = False
    BLOCKED: ClassVar[bool] = True

    text: str = EMPTY_TEXT
    offset: int = INITIAL_OFFSET
    calls: int = NO_CALLS
    complete: bool = INCOMPLETE
    blocked: bool = UNBLOCKED


def _positive(value: int, name: str) -> None:
    if type(value) is not int or value < SummaryValidation.MINIMUM_POSITIVE:
        raise ValueError(SummaryValidation.POSITIVE.format(name=name))


def _measure(payload: dict, measure: Measure) -> int:
    size = measure(deepcopy(payload))
    if type(size) is not int or size < SummaryValidation.MINIMUM_SIZE:
        raise ValueError(SummaryValidation.INVALID_MEASURE)
    return size


def fit_prefix(start: int, stop: int, *, build: Callable[[int], dict],
               measure: Measure, maximum: int) -> int:
    """Find a fitting prefix using the caller's monotonic size measure.

    ``build(end)`` includes previous summary and payload overhead. Units belong
    to ``measure`` (characters, bytes or tokens); no conversion is guessed. The
    selected payload is rechecked. Return start if no nonempty prefix fits.
    """
    _positive(maximum, SummaryValidation.MAXIMUM)
    if type(start) is not int or type(stop) is not int or not SummaryValidation.MINIMUM_OFFSET <= start <= stop:
        raise ValueError(SummaryValidation.INVALID_RANGE)
    # Grow only to the first non-fitting prefix. Starting a binary search at
    # stop repeatedly serializes the unprocessed tail of a large source.
    low, high = start, min(start + 1, stop)
    while high < stop and _measure(build(high), measure) <= maximum:
        low, high = high, min(start + 2 * (high - start), stop)
    while low < high:
        middle = (low + high + 1) // 2
        if _measure(build(middle), measure) <= maximum:
            low = middle
        else:
            high = middle - 1
    if low > start and _measure(build(low), measure) > maximum:
        raise ValueError(SummaryValidation.INCONSISTENT_MEASURE)
    return low


async def summarize(*, prompt: str, payload: dict, max_chars: int,
                    invoke: ModelCall, result: SummaryResult) -> AsyncIterator[Any]:
    """Invoke once and accept only a nonempty summary within the explicit limit.

    No prompt, model, timeout, retry or input budget is invented here. The caller
    prepares a fitting payload. Events pass through unchanged; closing/cancelling
    this iterator closes the injected iterator before returning control.
    """
    _positive(max_chars, SummaryValidation.MAX_CHARS)
    if not isinstance(prompt, str) or not prompt or not isinstance(payload, dict):
        raise ValueError(SummaryValidation.INVALID_INPUT)
    result.text, result.offset, result.calls = (
        SummaryResult.EMPTY_TEXT, SummaryResult.INITIAL_OFFSET, SummaryResult.NO_CALLS)
    result.complete, result.blocked = SummaryResult.INCOMPLETE, SummaryResult.UNBLOCKED
    answer = {}
    async with aclosing(invoke(prompt, deepcopy(payload), answer)) as events:
        async for event in events:
            yield event
    text = SummaryAnswer.read(answer, max_chars)
    result.text, result.calls, result.complete = text, SummaryResult.SINGLE_CALL, SummaryResult.COMPLETE


async def summarize_fragments(text: str | SummarySource, *, offset: int, previous_summary: str,
                              prompt: str, max_input_size: int, max_summary_chars: int,
                              max_calls: int, build_payload: FragmentPayload,
                              measure: Measure, invoke: ModelCall, result: SummaryResult,
                              progress: Callable[[SummaryResult], Awaitable[None]] | None = NO_PROGRESS,
                              ) -> AsyncIterator[Any]:
    """Incrementally merge fragments using explicit budgets and caller-owned payloads.

    build_payload(fragment, offset, total_chars, previous_summary) defines source
    semantics. The next call receives the preceding validated summary. max_calls
    bounds semantic calls, not retries inside the adapter. On exhaustion, return
    a partial result. On no fitting fragment, return blocked without invoking.
    Progress receives an independent snapshot after each validated result; its
    failure/cancellation propagates, and nothing is automatically retried/saved.
    """
    for name, value in ((SummaryValidation.MAX_INPUT_SIZE, max_input_size),
                       (SummaryValidation.MAX_SUMMARY_CHARS, max_summary_chars),
                       (SummaryValidation.MAX_CALLS, max_calls)):
        _positive(value, name)
    if not isinstance(text, (str, SummarySource)) or not isinstance(previous_summary, str):
        raise ValueError(SummaryValidation.INVALID_TEXT)
    if type(offset) is not int or not SummaryValidation.MINIMUM_OFFSET <= offset <= len(text):
        raise ValueError(SummaryValidation.INVALID_OFFSET)
    result.text, result.offset, result.calls = previous_summary, offset, SummaryResult.NO_CALLS
    result.complete, result.blocked = offset == len(text), SummaryResult.UNBLOCKED
    while not result.complete and result.calls < max_calls:
        start = result.offset
        if isinstance(text, SummarySource):
            count = min(text.read_chars, len(text) - start)
            fragment = await text.read(start, count)
            if not isinstance(fragment, str) or len(fragment) != count:
                raise ValueError("Summary source returned an inconsistent range")
            stop = start + len(fragment)
        else:
            fragment, stop = None, len(text)
        def build(end):
            value = fragment[:end - start] if fragment is not None else text[start:end]
            return build_payload(value, start, len(text), result.text)
        end = fit_prefix(start, stop, build=build, measure=measure, maximum=max_input_size)
        if end == start:
            result.blocked = SummaryResult.BLOCKED
            return
        payload = build(end)
        if _measure(payload, measure) > max_input_size:
            raise ValueError(SummaryValidation.CHANGED_PAYLOAD)
        partial = SummaryResult()
        async with aclosing(summarize(prompt=prompt, payload=payload, max_chars=max_summary_chars,
                                     invoke=invoke, result=partial)) as events:
            async for event in events:
                yield event
        result.text, result.offset = partial.text, end
        result.calls += SummaryResult.SINGLE_CALL
        result.complete = end == len(text)
        if progress is not NO_PROGRESS:
            await progress(replace(result))
