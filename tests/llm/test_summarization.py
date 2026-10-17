"""Domain-independent summaries preserve budgets, source ranges and cancellation."""

import asyncio
from contextlib import aclosing
from copy import deepcopy
import json
import unittest

from llm.components.summarization import SummaryResult, fit_prefix, summarize, summarize_fragments


async def collect(events):
    async with aclosing(events):
        return [event async for event in events]


def payload(fragment, offset, total, previous):
    return {"fragment": fragment, "offset": offset, "total": total, "previous": previous}


def measure(value):
    return len(json.dumps(value, ensure_ascii=False))


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_call_preserves_prompt_events_and_caller_payload(self):
        source = {"messages": [{"content": "original"}]}
        event = object()
        seen = []
        async def invoke(prompt, value, answer):
            seen.append((prompt, deepcopy(value)))
            value["messages"].clear()
            yield event
            answer["summary"] = "valid"
        result = SummaryResult()
        self.assertEqual(await collect(summarize(prompt="explicit prompt", payload=source, max_chars=5,
                                                invoke=invoke, result=result)), [event])
        self.assertEqual(seen, [("explicit prompt", source)])
        self.assertEqual(source["messages"], [{"content": "original"}])
        self.assertEqual((result.text, result.calls, result.complete), ("valid", 1, True))

    async def test_invalid_summary_is_not_coerced_retried_or_published(self):
        for value in (None, "", "  ", 10, [], {"text": "x"}, "123456"):
            with self.subTest(value=value):
                calls = []
                async def invoke(prompt, payload, answer):
                    calls.append(1)
                    answer["summary"] = value
                    if False:
                        yield
                result = SummaryResult(text="old", complete=True)
                with self.assertRaisesRegex(ValueError, "Summary"):
                    await collect(summarize(prompt="explicit", payload={}, max_chars=5, invoke=invoke, result=result))
                self.assertEqual(calls, [1])
                self.assertFalse(result.complete)
                self.assertEqual(result.text, "")

    async def test_model_failure_propagates_without_retry(self):
        error = ConnectionError("offline")
        closed, calls = [], []
        async def invoke(prompt, payload, answer):
            calls.append(1)
            try:
                yield "observation"
                raise error
            finally:
                closed.append(True)
        result = SummaryResult()
        with self.assertRaises(ConnectionError) as raised:
            await collect(summarize(prompt="explicit", payload={}, max_chars=5, invoke=invoke, result=result))
        self.assertIs(raised.exception, error)
        self.assertEqual((calls, closed), ([1], [True]))
        self.assertFalse(result.complete)

    async def test_cancellation_closes_injected_iterator(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def invoke(prompt, payload, answer):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield
            finally:
                closed.set()
        result = SummaryResult()
        task = asyncio.create_task(collect(summarize(prompt="explicit", payload={}, max_chars=5,
                                                    invoke=invoke, result=result)))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertFalse(result.complete)

    async def test_early_close_cascades_through_fragment_and_model_iterators(self):
        closed = []
        async def invoke(prompt, payload, answer):
            try:
                yield "started"
                answer["summary"] = "ok"
            finally:
                closed.append(True)
        result = SummaryResult()
        stream = summarize_fragments("data", offset=0, previous_summary="", prompt="explicit",
            max_input_size=100, max_summary_chars=5, max_calls=1,
            build_payload=payload, measure=measure, invoke=invoke, result=result)
        self.assertEqual(await anext(stream), "started")
        await stream.aclose()
        self.assertEqual(closed, [True])
        self.assertEqual((result.offset, result.calls, result.complete), (0, 0, False))

    async def test_fragments_cover_unicode_source_once_with_explicit_size_and_call_budget(self):
        source = '한글 \\"\n🙂 ' * 20
        requests, observations, progress = [], [], []
        async def invoke(prompt, value, answer):
            requests.append(deepcopy(value))
            yield ("request", len(requests))
            answer["summary"] = "merged"
        async def checkpoint(value):
            progress.append((value.offset, value.calls, value.complete))
            value.offset = -1  # Callback cannot corrupt the working cursor.
        result = SummaryResult()
        observations.extend(await collect(summarize_fragments(source, offset=0, previous_summary="seed",
            prompt="explicit", max_input_size=100, max_summary_chars=10, max_calls=100,
            build_payload=payload, measure=measure, invoke=invoke, result=result, progress=checkpoint)))
        self.assertTrue(result.complete)
        self.assertEqual(result.offset, len(source))
        self.assertEqual("".join(r["fragment"] for r in requests), source)
        self.assertTrue(all(measure(r) <= 100 for r in requests))
        self.assertEqual(requests[0]["previous"], "seed")
        self.assertTrue(all(r["previous"] == "merged" for r in requests[1:]))
        self.assertEqual(len(observations), result.calls)
        self.assertEqual(progress[-1], (len(source), result.calls, True))
        self.assertEqual([r["offset"] for r in requests], [0, *[v[0] for v in progress[:-1]]])

    async def test_explicit_partial_resume_does_not_revisit_covered_source(self):
        requests = []
        async def invoke(prompt, value, answer):
            requests.append(deepcopy(value))
            answer["summary"] = "merged"
            if False:
                yield
        source, first = "x" * 160, SummaryResult()
        options = dict(prompt="explicit", max_input_size=90, max_summary_chars=10,
                       build_payload=payload, measure=measure, invoke=invoke)
        await collect(summarize_fragments(source, offset=0, previous_summary="", max_calls=1, result=first, **options))
        self.assertEqual(first.calls, 1)
        self.assertFalse(first.complete)
        resumed = SummaryResult()
        await collect(summarize_fragments(source, offset=first.offset, previous_summary=first.text,
                                         max_calls=100, result=resumed, **options))
        self.assertTrue(resumed.complete)
        self.assertEqual(requests[1]["offset"], first.offset)
        self.assertEqual("".join(v["fragment"] for v in requests), source)

    async def test_overhead_cannot_fit_and_empty_source_do_not_invoke(self):
        async def invoke(*args):
            self.fail("No fitting source must not invoke the model")
            yield
        for text in ("content", ""):
            result = SummaryResult()
            await collect(summarize_fragments(text, offset=0, previous_summary="seed", prompt="explicit",
                max_input_size=1, max_summary_chars=10, max_calls=1,
                build_payload=payload, measure=measure, invoke=invoke, result=result))
            self.assertEqual(result.calls, 0)
            self.assertEqual(result.complete, not text)
            self.assertEqual(result.blocked, bool(text))
            self.assertEqual(result.text, "seed")

    async def test_invalid_later_summary_keeps_only_validated_progress(self):
        calls, saved = [], []
        async def invoke(prompt, value, answer):
            calls.append(value)
            answer["summary"] = "ok" if len(calls) == 1 else None
            if False:
                yield
        async def progress(value):
            saved.append(value)
        result = SummaryResult()
        with self.assertRaises(ValueError):
            await collect(summarize_fragments("x" * 160, offset=0, previous_summary="", prompt="explicit",
                max_input_size=90, max_summary_chars=5, max_calls=10,
                build_payload=payload, measure=measure, invoke=invoke, result=result, progress=progress))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(saved), 1)
        self.assertEqual(result, saved[0])
        self.assertFalse(result.complete)

    async def test_progress_storage_failure_stops_without_replay(self):
        calls = []
        async def invoke(prompt, value, answer):
            calls.append(1)
            answer["summary"] = "ok"
            if False:
                yield
        async def progress(value):
            raise OSError("checkpoint write failed")
        result = SummaryResult()
        with self.assertRaises(OSError):
            await collect(summarize_fragments("x" * 160, offset=0, previous_summary="", prompt="explicit",
                max_input_size=90, max_summary_chars=5, max_calls=10,
                build_payload=payload, measure=measure, invoke=invoke, result=result, progress=progress))
        self.assertEqual(calls, [1])
        self.assertEqual(result.calls, 1)
        self.assertFalse(result.complete)

    async def test_fragment_cancellation_preserves_only_completed_progress(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        calls, saved = [], []
        async def invoke(prompt, value, answer):
            calls.append(value)
            if len(calls) == 1:
                answer['summary'] = 'ok'
            else:
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            if False:
                yield
        async def progress(value):
            saved.append(value)
        result = SummaryResult()
        task = asyncio.create_task(collect(summarize_fragments('x' * 160,
            offset=0, previous_summary='', prompt='explicit', max_input_size=90,
            max_summary_chars=5, max_calls=10, build_payload=payload, measure=measure,
            invoke=invoke, result=result, progress=progress)))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(saved), 1)
        self.assertEqual(result, saved[0])
        self.assertFalse(result.complete)

    def test_size_measure_units_and_mutation_are_caller_owned(self):
        value = {"count": 0}
        def build(end):
            value["count"] = end
            return value
        def counter(candidate):
            size = candidate["count"] * 2 + 3
            candidate.clear()
            return size
        self.assertEqual(fit_prefix(0, 10, build=build, measure=counter, maximum=9), 3)
        self.assertEqual(value, {"count": 3})

    def test_invalid_size_measures_are_rejected(self):
        for value in (True, -1, 1.5, float("inf"), None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fit_prefix(0, 10, build=lambda end: {}, measure=lambda _: value, maximum=10)

    def test_prefix_sizing_does_not_scan_the_whole_remaining_source(self):
        for start in (0, 12345):
            with self.subTest(start=start):
                measured = []
                def counter(value):
                    measured.append(value["chars"])
                    return value["chars"] + 13
                end = fit_prefix(start, start + 1_000_000,
                    build=lambda end: {"chars": end - start}, measure=counter, maximum=1000)
                self.assertEqual(end, start + 987)
                # Work is proportional to the chosen fragment, not source length.
                self.assertLess(sum(measured), 50 * 1000)

    async def test_budgets_are_required_and_invalid_values_never_invoke(self):
        async def invoke(*args):
            self.fail("Invalid budgets must fail before invocation")
            yield
        options = dict(offset=0, previous_summary="", prompt="explicit", max_input_size=100,
                       max_summary_chars=5, max_calls=1, build_payload=payload, measure=measure,
                       invoke=invoke, result=SummaryResult())
        for name in ("max_input_size", "max_summary_chars", "max_calls"):
            missing = dict(options)
            del missing[name]
            with self.subTest(missing=name), self.assertRaises(TypeError):
                summarize_fragments("content", **missing)
            for value in (0, -1, None, True):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    await collect(summarize_fragments("content", **{**options, name: value}))

    async def test_concurrent_callers_keep_their_own_summary_state(self):
        entered, both = [], asyncio.Event()
        async def invoke(prompt, value, answer):
            entered.append(prompt)
            if len(entered) == 2:
                both.set()
            await both.wait()
            answer["summary"] = prompt
            if False:
                yield
        results = [SummaryResult(), SummaryResult()]
        await asyncio.wait_for(asyncio.gather(*(collect(summarize(prompt=name, payload={}, max_chars=10,
            invoke=invoke, result=result)) for name, result in zip(("one", "two"), results))), 2)
        self.assertEqual([r.text for r in results], ["one", "two"])
