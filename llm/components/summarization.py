"""Reusable summary algorithms; callers own selection, model configuration and persistence.

The injected model adapter fills ``answer['summary']`` and yields its own events.
It owns SDK calls, retries, usage observations and any structured-output decoding.
This module never imports an Engine, SDK, domain model or storage implementation.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any


type ModelCall = Callable[[str, dict, dict], AsyncIterator[Any]]
type Measure = Callable[[dict], int]
type FragmentPayload = Callable[[str, int, int, str], dict]


@dataclass(slots=True)
class SummaryResult:
    """In-memory outcome, not a durable receipt. Offset counts source characters.

    A partial result covers only ``text[:offset]`` of the caller's source. The
    caller must bind an offset/summary to its source and configuration revision
    before storing or resuming it. Zero/empty fields represent no completed work,
    not configuration defaults. Calls counts validated completions; failed SDK
    attempts remain the adapter's observations. ``blocked`` means no next fragment fits.
    """

    text: str = ""
    offset: int = 0
    calls: int = 0
    complete: bool = False
    blocked: bool = False


def _positive(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _measure(payload: dict, measure: Measure) -> int:
    size = measure(deepcopy(payload))
    if type(size) is not int or size < 0:
        raise ValueError("Summary measure must return a nonnegative integer")
    return size


def fit_prefix(start: int, stop: int, *, build: Callable[[int], dict],
               measure: Measure, maximum: int) -> int:
    """Find a fitting prefix using the caller's monotonic size measure.

    ``build(end)`` includes previous summary and payload overhead. Units belong
    to ``measure`` (characters, bytes or tokens); no conversion is guessed. The
    selected payload is rechecked. Return start if no nonempty prefix fits.
    """
    _positive(maximum, "maximum")
    if type(start) is not int or type(stop) is not int or not 0 <= start <= stop:
        raise ValueError("Invalid summary source range")
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
        raise ValueError("Inconsistent summary size measure")
    return low


async def summarize(*, prompt: str, payload: dict, max_chars: int,
                    invoke: ModelCall, result: SummaryResult) -> AsyncIterator[Any]:
    """Invoke once and accept only a nonempty summary within the explicit limit.

    No prompt, model, timeout, retry or input budget is invented here. The caller
    prepares a fitting payload. Events pass through unchanged; closing/cancelling
    this iterator closes the injected iterator before returning control.
    """
    _positive(max_chars, "max_chars")
    if not isinstance(prompt, str) or not prompt or not isinstance(payload, dict):
        raise ValueError("Summary requires a prompt and object payload")
    result.text, result.offset, result.calls = "", 0, 0
    result.complete, result.blocked = False, False
    answer = {}
    async with aclosing(invoke(prompt, deepcopy(payload), answer)) as events:
        async for event in events:
            yield event
    text = answer.get("summary")
    if not isinstance(text, str) or not text.strip() or len(text) > max_chars:
        raise ValueError("Summary is empty or exceeds the requested size")
    result.text, result.calls, result.complete = text, 1, True


async def summarize_fragments(text: str, *, offset: int, previous_summary: str,
                              prompt: str, max_input_size: int, max_summary_chars: int,
                              max_calls: int, build_payload: FragmentPayload,
                              measure: Measure, invoke: ModelCall, result: SummaryResult,
                              progress: Callable[[SummaryResult], Awaitable[None]] | None = None,
                              ) -> AsyncIterator[Any]:
    """Incrementally merge fragments using explicit budgets and caller-owned payloads.

    build_payload(fragment, offset, total_chars, previous_summary) defines source
    semantics. The next call receives the preceding validated summary. max_calls
    bounds semantic calls, not retries inside the adapter. On exhaustion, return
    a partial result. On no fitting fragment, return blocked without invoking.
    Progress receives an independent snapshot after each validated result; its
    failure/cancellation propagates, and nothing is automatically retried/saved.
    """
    for name, value in (("max_input_size", max_input_size), ("max_summary_chars", max_summary_chars),
                        ("max_calls", max_calls)):
        _positive(value, name)
    if not isinstance(text, str) or not isinstance(previous_summary, str):
        raise ValueError("Summary source and previous summary must be text")
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("Invalid summary offset")
    result.text, result.offset, result.calls = previous_summary, offset, 0
    result.complete, result.blocked = offset == len(text), False
    while not result.complete and result.calls < max_calls:
        start = result.offset
        def build(end):
            return build_payload(text[start:end], start, len(text), result.text)
        end = fit_prefix(start, len(text), build=build, measure=measure, maximum=max_input_size)
        if end == start:
            result.blocked = True
            return
        payload = build(end)
        if _measure(payload, measure) > max_input_size:
            raise ValueError("Summary payload changed after size validation")
        partial = SummaryResult()
        async with aclosing(summarize(prompt=prompt, payload=payload, max_chars=max_summary_chars,
                                     invoke=invoke, result=partial)) as events:
            async for event in events:
                yield event
        result.text, result.offset = partial.text, end
        result.calls += 1
        result.complete = end == len(text)
        if progress is not None:
            await progress(replace(result))
