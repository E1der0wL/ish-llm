"""명시적 진단/대기 설정과 오류 보존, 승인·완료 Tool의 재실행 방지를 검증한다."""

from llm.core.model_protocol import ModelMessage, ModelRequest

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from llm.components.tools import Tool, ToolContract, ToolClassification, ToolRegistry
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.errors import stable_error_code
from llm.llm import LargeLanguageModel, BackendServices
from llm.providers.requests import ProviderError, invoke, error_code, resolve_provider_options
from llm.providers.retry import retry_after
from llm.providers.runtime import (ProviderDiagnostics, logging_scope, diagnostic_scope,
    diagnostic_context, diagnostic_failure, request_details)
from llm.services.runtime.tools import ToolRuntime
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from llm.components.tools import ToolEffect, ToolIsolation


def failure(status=400, *, retry_after_value=None):
    error = RuntimeError("PRIVATE raw provider response")
    error.status_code = status
    error.body = {"error": {"message": "unsupported field tools", "param": "tools", "type": "invalid_request",
                            "code": "bad_field", "ignored": "PRIVATE document"}}
    error.response = SimpleNamespace(headers={"x-request-id": "server-request-1", "authorization": "PRIVATE key"})
    if retry_after_value is not None:
        error.response.headers["Retry-After"] = retry_after_value
    return error


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_detail_collection_is_explicit_and_summary_has_no_payload(self):
        request = {"model": "openai/test", "api_base": "https://user:PRIVATE@server/v1?key=PRIVATE#PRIVATE",
            "api_key": "PRIVATE", "tools": [{"description": "PRIVATE"}], "messages": [
                {"role": "user", "content": "PRIVATE prompt"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "PRIVATE-call"}]},
                {"role": "tool", "tool_call_id": "PRIVATE-call", "content": "PRIVATE result"}]}
        for enabled in (False, True):
            options = ProviderDiagnostics(error_details=True, request_summary=True, max_detail_chars=40) if enabled else None
            with logging_scope(self.root, diagnostics=options), diagnostic_context(step_id="step", call_id="call", attempt=2):
                event = diagnostic_failure(failure(), operation="completion", stage="request", **request_details(request))
            details = event.details
            self.assertEqual((details["step_id"], details["call_id"], details["attempt"]), ("step", "call", 2))
            self.assertEqual(details["exceptions"][0]["request_id"], "server-request-1")
            self.assertIn("litellm", details["sdk_versions"])
            self.assertEqual("provider_error" in details["exceptions"][0], enabled)
            self.assertEqual("request_summary" in details, enabled)
            if enabled:
                self.assertEqual(details["endpoint"], "https://server/v1")
                self.assertEqual(details["exceptions"][0]["provider_error"]["param"], "tools")
                self.assertEqual(details["request_summary"]["unresolved_tool_calls"], 0)
                self.assertEqual(details["request_summary"]["unmatched_tool_results"], 0)
            self.assertNotIn("PRIVATE", json.dumps(event.to_dict()))

    def test_summary_identifies_bad_tool_pairing_without_ids(self):
        messages = [{"role": "assistant", "tool_calls": [{"id": "x"}, {"id": "x"}]},
                    {"role": "user", "content": "private"}, {"role": "tool", "tool_call_id": "missing"}]
        with logging_scope(self.root, diagnostics=ProviderDiagnostics(request_summary=True)):
            summary = request_details({"messages": messages})["request_summary"]
        self.assertEqual((summary["duplicate_tool_call_ids"], summary["unresolved_tool_calls"],
                          summary["unmatched_tool_results"], summary["out_of_order_messages"]), (1, 1, 1, 1))

    async def test_concurrent_and_nested_scopes_preserve_owner_without_leaking_options(self):
        async def observe(name, enabled):
            with logging_scope(self.root / name, run_id=name, diagnostics=ProviderDiagnostics(error_details=enabled)):
                await asyncio.sleep(0)
                with logging_scope(self.root / name, project_id="project"):
                    return diagnostic_failure(failure(), operation="arerank", stage="invoke")
        first, second = await asyncio.gather(observe("a", True), observe("b", False))
        self.assertEqual([e.details["run_id"] for e in (first, second)], ["a", "b"])
        self.assertIn("provider_error", first.details["exceptions"][0])
        self.assertNotIn("provider_error", second.details["exceptions"][0])
        with logging_scope(self.root / "a", run_id="a", diagnostics=ProviderDiagnostics(error_details=True)):
            with logging_scope(self.root / "other"):
                other = diagnostic_failure(failure(), operation="arerank", stage="invoke")
        self.assertNotIn("run_id", other.details)
        self.assertNotIn("provider_error", other.details["exceptions"][0])

    def test_detail_limit_and_empty_infrastructure_projection(self):
        self.assertIsNone(BackendServices().describe()["values"]["provider_diagnostics"])
        self.assertEqual(BackendServices(provider_diagnostics=ProviderDiagnostics()).describe()["values"]["provider_diagnostics"], {})
        with logging_scope(self.root, diagnostics=ProviderDiagnostics(error_details=True, max_detail_chars=5)):
            event = diagnostic_failure(failure(), operation="completion", stage="request")
        self.assertEqual(event.details["exceptions"][0]["provider_error"]["message"], "unsup")
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                ProviderDiagnostics(max_detail_chars=value)
        with self.assertRaises(TypeError):
            ProviderDiagnostics(error_details="yes")

    def test_provider_classification_traverses_unsuppressed_context_and_keeps_trusted_code(self):
        wrapper = RuntimeError("wrapper")
        wrapper.__context__ = failure(429)
        self.assertEqual(error_code(wrapper), "provider_rate_limit")
        wrapper.__suppress_context__ = True
        self.assertEqual(error_code(wrapper), "provider_failed")
        wrapper.code = "provider_rate_limit"  # arbitrary .code is not the CodedError contract
        self.assertEqual(error_code(wrapper), "provider_failed")

    def test_provider_classification_reads_trusted_code_once_and_ignores_vendor_code_getter(self):
        from llm.errors import CodedError

        class VendorError(Exception):
            status_code = 429

            @property
            def code(self):
                raise AssertionError("Vendor code is not execution classification evidence")

        class ChangingCode(CodedError, Exception):
            status_code = 429

            def __init__(self):
                self.reads = 0

            @property
            def code(self):
                self.reads += 1
                return None if self.reads == 1 else "changed_after_observation"

        for kind in (VendorError, ChangingCode):
            with self.subTest(kind=kind):
                error = kind()
                self.assertEqual(error_code(error), "provider_rate_limit")
                if isinstance(error, ChangingCode):
                    self.assertEqual(error.reads, 1)

    async def test_real_sdk_error_body_and_request_id_are_preserved_without_full_response(self):
        import httpx
        from openai import BadRequestError
        response = httpx.Response(400, request=httpx.Request("POST", "https://server/v1/chat/completions"),
                                  headers={"x-request-id": "sdk-request", "authorization": "PRIVATE"})
        error = BadRequestError("bad request", response=response,
                                body={"message": "missing tool result", "param": "messages", "code": "invalid"})
        with logging_scope(self.root, diagnostics=ProviderDiagnostics(error_details=True)), self.assertRaises(ProviderError) as caught:
            await invoke("acompletion", {"model": "openai/test"}, AsyncMock(side_effect=error), {})
        entry = caught.exception.diagnostic.details["exceptions"][0]
        self.assertEqual(entry["request_id"], "sdk-request")
        self.assertEqual(entry["provider_error"]["param"], "messages")
        self.assertEqual(caught.exception.code, "provider_invalid_request")
        self.assertNotIn("PRIVATE", json.dumps(caught.exception.diagnostic.to_dict()))

    def test_failed_log_writer_does_not_replace_error_or_disable_observer(self):
        events = []
        with logging_scope(self.root), diagnostic_scope(events.append), \
                patch("llm.providers.runtime.logging.Logger.log", side_effect=OSError("disk full")):
            event = diagnostic_failure(failure(503), operation="acompletion", stage="invoke")
        self.assertEqual(event.code, "provider_unavailable")
        self.assertEqual(events, [event])


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def test_retry_after_seconds_dates_and_invalid_values(self):
        self.assertEqual(retry_after(failure(429, retry_after_value="2")), 2)
        now = time.time()
        date = format_datetime(datetime.fromtimestamp(now + 30, timezone.utc), usegmt=True)
        with patch("llm.providers.retry.time.time", return_value=now):
            self.assertAlmostEqual(retry_after(failure(503, retry_after_value=date)), 30, delta=1)
        for value in ("nan", "inf", "-1", "nonsense", {}, None):
            self.assertIsNone(retry_after(failure(503, retry_after_value=value)))
        self.assertEqual(resolve_provider_options({}), {})
        for options in ({"jitter": 1}, {"respect_retry_after": "yes"}, {"unknown": True}):
            with self.assertRaises(Exception):
                resolve_provider_options(options)

    async def test_both_transports_use_explicit_jitter_and_server_floor_only(self):
        for streaming in (False, True):
            for enabled in (False, True):
                events = []
                options = {"max_attempts": 2, "delay_seconds": 4, "max_delay_seconds": 10}
                if enabled:
                    options.update(respect_retry_after=True, jitter=True)
                error = failure(429, retry_after_value="3")
                with patch("llm.policies.retry.random.uniform", return_value=1) as random, \
                        patch("llm.providers.requests.asyncio.sleep", new_callable=AsyncMock) as sleep, diagnostic_scope(events.append):
                    if streaming:
                        model = ScriptedCompletion([error], [chunk("done", finish="stop")])
                        await self.consume(model, options)
                        self.assertEqual(len(model.requests), 2)
                    else:
                        call_model = AsyncMock(side_effect=[error, "done"])
                        self.assertEqual(await invoke("arerank", {}, call_model, options), "done")
                    sleep.assert_awaited_once_with(3 if enabled else 4)
                    self.assertEqual(random.call_count, int(enabled))
                failed = next(e for e in events if e.code == "provider_rate_limit")
                retry = next(e for e in events if e.code == "provider_retry")
                self.assertEqual(failed.details["call_id"], retry.details["call_id"])
                self.assertEqual((failed.details["attempt"], retry.details["attempt"]), (1, 2))

    async def consume(self, model, options, **request):
        return [event async for event in BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)).stream_completion(
            ModelRequest([], {"model": "openai/test", **request}), provider=options)]

    async def test_server_wait_cannot_exceed_explicit_cap_or_deadline(self):
        for streaming in (False, True):
            for limits, code in (({"max_delay_seconds": 1}, "provider_rate_limit"),
                                 ({"wall_timeout": 1}, "provider_timeout")):
                options = {"max_attempts": 3, "respect_retry_after": True, **limits}
                error = failure(429, retry_after_value="2")
                model = ScriptedCompletion([error])
                call_model = AsyncMock(side_effect=error)
                with self.assertRaises(Exception) as caught:
                    if streaming:
                        await self.consume(model, options)
                    else:
                        await invoke("arerank", {}, call_model, options)
                self.assertEqual(stable_error_code(caught.exception), code)
                self.assertEqual(len(model.requests) if streaming else call_model.await_count, 1)

    async def test_new_flags_do_not_enable_retry_or_multiply_sdk_retry(self):
        for options, request in (({"jitter": True, "respect_retry_after": True}, {}),
                                 ({"max_attempts": 4, "jitter": True}, {"max_retries": 2})):
            call_model = AsyncMock(side_effect=failure(503))
            with self.assertRaises(ProviderError):
                await invoke("arerank", request, call_model, options)
            self.assertEqual(call_model.await_count, 1)

    async def test_retry_wait_cancellation_never_starts_next_attempt(self):
        for streaming in (False, True):
            ready = asyncio.Event()
            model = ScriptedCompletion([failure(503, retry_after_value="10")])
            call_model = AsyncMock(side_effect=failure(503, retry_after_value="10"))
            def observe(event):
                if event.code == "provider_retry":
                    ready.set()
            options = {"max_attempts": 3, "respect_retry_after": True}
            with diagnostic_scope(observe):
                task = asyncio.create_task(self.consume(model, options) if streaming else invoke("arerank", {}, call_model, options))
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(len(model.requests) if streaming else call_model.await_count, 1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def setup_backend(self, responses, *, provider=None, limits=None, approval=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        effects = []
        async def effect(arguments):
            effects.append(arguments)
        model = ScriptedCompletion(*responses)
        tools = ToolRegistry((Tool("act", "write", {"type": "object"}, effect,
            contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=approval, operation_key_required=False, retry_fields=())),))
        app = LargeLanguageModel(temporary.name, components=[RuntimeTools(tools)], engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
            services=BackendServices(provider_diagnostics=ProviderDiagnostics(error_details=True, request_summary=True),
                tool_runtime=ToolRuntime(classify=lambda _: ToolClassification("file.write", "test", 10))))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=["tools"], config={"parameters": {"engines": {"loop": {
            "config": {"completion": {"model": "openai/test"}}, "policy": {"provider": provider or {}, **(limits or {})}}}},
            "policies": {"approval": {"enabled": True, "risk_scheme": "test", "rules": [
                {"id": "write", "category": "file.write", "max_risk": 10}]}}})
        await project.components.tools.aenable("act")
        await project.agents.acreate({"engine": "loop", "tools": ["act"]}, identifier="loop")
        return app, await project.sessions.acreate(agent_ids=sorted(await project.agents.alist())), model, effects

    async def test_approval_retry_and_durable_resume_never_replay_effect(self):
        app, session, model, effects = await self.setup_backend([
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")],
            [failure(503, retry_after_value="0")], [failure(503)], [chunk("done", finish="stop")]],
            provider={"max_attempts": 2, "jitter": True, "respect_retry_after": True}, approval=True)
        paused = (await (await session.run.submit("write", recipients=["loop"])).wait())[0]
        self.assertEqual(paused.data.status, "paused")
        self.assertEqual([r.actor for r in await paused.ainteraction_responses()], ["policy"])
        self.assertEqual(effects, [])
        failed = (await (await session.run.resume(paused.id, agent_id="loop")).wait())[0]
        self.assertEqual(failed.data.error_code, "provider_unavailable")
        self.assertEqual(effects, [{}])
        # Run worker was created earlier: log files, not the submitting task's observer, are authoritative diagnostics.
        steps = await failed.steps.alist()
        step = next(s for s in steps if s.kind == "llm" and s.status == "failed")
        self.assertEqual(step.diagnostic.code, "provider_unavailable")
        self.assertEqual(step.diagnostic.details["step_id"], step.id)
        self.assertEqual(step.diagnostic.details["run_id"], failed.id)
        self.assertEqual(step.diagnostic.details["attempt"], 2)
        self.assertEqual(step.diagnostic.details["request_summary"]["unresolved_tool_calls"], 0)
        completed = (await (await session.run.resume(failed.id, agent_id="loop")).wait())[0]
        self.assertEqual(completed.data.status, "completed", completed.data.error)
        self.assertEqual(effects, [{}])
        self.assertEqual(len(model.requests), 4)
        self.assertTrue(any(s.metadata.get("reused") for s in await completed.steps.alist()))
        self.assertEqual(model.requests[1]["messages"], model.requests[2]["messages"])
        self.assertEqual(model.requests[2]["messages"], model.requests[3]["messages"])

    async def test_400_after_successful_tool_stops_without_tool_or_model_retry(self):
        _, session, model, effects = await self.setup_backend([
            [chunk(calls=[call("{}", name="act")], finish="tool_calls")], [failure(400)]],
            provider={"max_attempts": 4, "respect_retry_after": True, "jitter": True})
        run = (await (await session.run.submit("write", recipients=["loop"])).wait())[0]
        self.assertEqual(run.data.error_code, "provider_invalid_request")
        self.assertEqual((len(model.requests), effects), (2, [{}]))

    async def test_completion_failure_codes_and_partial_output_are_durable(self):
        for response, limits, code in (([chunk("partial", finish="length")], {}, "completion_incomplete"),
                ([chunk("partial"), ConnectionError("disconnected")], {}, "provider_connection"),
                ([chunk("ok"), chunk("too long", finish="stop")], {"max_output_chars": 2}, "completion_output_limit"),
                ([{"choices": "invalid"}], {}, "completion_invalid_response")):
            with self.subTest(code=code):
                _, session, model, effects = await self.setup_backend([response], provider={"max_attempts": 3}, limits=limits)
                run = (await (await session.run.submit("go", recipients=["loop"])).wait())[0]
                self.assertEqual(run.data.error_code, code, run.data.error)
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(effects, [])
                step = next(s for s in await run.steps.alist() if s.kind == "llm")
                self.assertEqual(step.diagnostic.code, code)
                if code in ("completion_incomplete", "provider_connection"):
                    message = next(m for m in await session.aconversation() if m.id == run.data.assistant_message_id)
                    self.assertIn("partial", message.content)
                if code == "completion_incomplete":
                    self.assertEqual(step.diagnostic.details["raw_finish_reason"], "length")
                    self.assertEqual(step.diagnostic.details["termination"], "output_limit")

    async def test_step_deadline_is_distinct_from_action_and_provider_timeout(self):
        # 공통 Step 경계에서 coroutine/iterator와 소비자 저장 대기를 각각 확인한다.
        context = SimpleNamespace(run=SimpleNamespace(id="run"), output_visibility="user", output_step_id=None)
        async def slow(context):
            await asyncio.Event().wait()
        async def slow_stream(context):
            await asyncio.Event().wait()
            yield "never"
        async def own_error(context):
            raise TimeoutError("action timeout")
        for action, expected in ((slow, "step_timeout"), (slow_stream, "step_timeout"), (own_error, None)):
            events = []
            with self.assertRaises(Exception) as caught:
                async for event in BaseEngine().step(context, action, name="timed", timeout_seconds=.01):
                    events.append(event)
            self.assertEqual(stable_error_code(caught.exception), expected)
            self.assertEqual(events[-1].failure_code, expected)

    async def test_loop_request_deadline_is_persisted_as_step_timeout(self):
        _, session, _, _ = await self.setup_backend([], limits={"request_timeout": .02})
        def slow(**kwargs):
            time.sleep(.05)
            yield chunk("late", finish="stop")
        # Host implementation is supplied before the Run is constructed.
        session.app.engines.get("loop").completion_adapter = LiteLLMCompletionAdapter(completion_fn=slow)
        run = (await (await session.run.submit("go", recipients=["loop"])).wait())[0]
        self.assertEqual(run.data.error_code, "step_timeout")
