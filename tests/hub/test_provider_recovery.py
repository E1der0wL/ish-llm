"""Real Loop recovery: no Tool replay, bounded analysis, preserved input and UI."""

from llm.providers.litellm import LiteLLMCompletionAdapter

from llm.llm import Diagnostic, CompletionRepair

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.execution.recovery_input import reductions
from hub.backend.execution.provider_recovery import CompletionRecoveryMixin, ProviderRecovery, RecoveryState
from hub.backend.execution.retries import HubLoopEngine
from hub.errors.adapters import from_diagnostic
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore
from hub.locales import Language
from hub.model import ChatMessage, RetrySummary
from hub.ui.presentation import present_message
from hub.widget.feedback.retry_status import retry_text
from llm.components.processing import CompletionSession, CompletionRecovery, CompletionPipeline
from llm.engines.loop import LoopEngine
from tests.llm.test_completion_processing import Feature, Processor
from tests.llm.test_large_data import SourceTools, answer
from tests.llm.test_loop import chunk, call


def bad_request(text="This model's maximum context length is 204800 tokens.", status=400):
    error = RuntimeError(text)
    error.status_code = status
    error.body = {"error": {"message": text}}
    return error


class Model:
    def __init__(self, *, always_fail=False, invalid_analysis=False, slow=False):
        self.requests = []
        self.main = 0
        self.always_fail, self.invalid_analysis = always_fail, invalid_analysis
        self.slow = slow
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, **request):
        self.requests.append(deepcopy(request))
        instruction = request["messages"][0]["content"]
        if instruction.startswith("Explain a failed model request"):
            self.entered.set()
            if self.slow:
                self.release.wait(5)
            yield from answer("not JSON" if self.invalid_analysis else json.dumps({
                "action": "compact", "explanation": "명령 결과가 입력 한도를 초과했습니다. 요약 후 이어갑니다."}))
        elif instruction.startswith("Summarize the supplied reference material"):
            yield from answer('{"summary":"The command completed: 120 records, no errors."}')
        else:
            self.main += 1
            if self.main == 1:
                yield chunk(calls=[call("{}", name="source_read")], finish="tool_calls")
            elif self.main == 2 or self.always_fail:
                raise bad_request()
            else:
                yield from answer("The operation succeeded.")


class ProviderRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_engine_callback_is_not_replaced_or_implicitly_installed(self):
        from hub.backend.bootstrap import create_backend
        async def chosen(failure):
            return None
        for engine in (LoopEngine(), HubLoopEngine(recover_completion=chosen)):
            with tempfile.TemporaryDirectory() as directory:
                backend = create_backend(HubConfig(directory, auto_title=False,
                    engine_factories={"loop": lambda: engine}))
                self.assertIs(engine.recover_completion, chosen if isinstance(engine, HubLoopEngine) else None)
                await backend.shutdown()

    async def runtime(self, model, *, components=(), attempts=2, **settings):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        preferences = PreferencesStore(temp.name)
        preferences.save("general", asdict(GeneralSettings(**settings)))
        source = SourceTools()
        source.content = "large command output; records=120; errors=0\n" * 200
        runtime = HubRuntime(HubConfig(temp.name, model="test/model", auto_title=False,
            engine_factories={"loop": lambda: HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
            component_factories=(lambda: source, *components),
            project_config={"parameters": {"engines": {"loop": {"policy": {"provider": {"max_attempts": attempts}}}}}}))
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        sid = await runtime.new_session()
        return runtime, runtime.sessions[sid], source

    async def execute(self, runtime, session):
        request_id = await runtime.submit(session.id, "Analyze the command output, preserve the actual result.")
        request = await session.run.arequest(request_id)
        return await request.wait(timeout=20)

    async def test_context_recovery_preserves_run_tool_receipt_and_original_output(self):
        model = Model()
        observations = []
        class Capture(CompletionSession):
            async def after_completion(self, observation):
                observations.append(observation)
                if False:
                    yield
        observer = Feature("observer", Processor("capture", lambda context: Capture()))
        runtime, session, source = await self.runtime(model, components=(lambda: observer,))
        run = await self.execute(runtime, session)
        result = await run.aget_data()
        self.assertEqual(str(result.status), "completed", result.error)
        self.assertEqual(source.calls, 1)
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertEqual(model.main, 3)
        requests = [r for r in model.requests if "tools" in r]
        original, corrected = requests[-2:]
        for old, new in zip(original["messages"], corrected["messages"]):
            if old["role"] == "tool":
                self.assertLess(len(new["content"]), len(old["content"]))
                self.assertEqual({k: v for k, v in new.items() if k != "content"},
                                 {k: v for k, v in old.items() if k != "content"})
            else:
                self.assertEqual(old, new)
        self.assertEqual(original["tools"], corrected["tools"])
        from llm.providers.wire_codec import encode_message
        self.assertEqual([encode_message(m) for m in observations[-1].request.messages], corrected["messages"])
        self.assertTrue(any("large command output" in m.text
                            for m in observations[-1].original_messages))
        steps = await run.steps.alist()
        tool = next(step for step in steps if step.kind == "tool")
        self.assertIn("large command output", json.dumps(tool.metadata, ensure_ascii=False))
        self.assertTrue(any(step.metadata.get("hub_recovery") == "error analysis" for step in steps))
        snapshot = await runtime.snapshot()
        self.assertIsNone(snapshot.run.retry)
        self.assertEqual((await run.aresponse()).content, "The operation succeeded.")

    async def test_invalid_model_choice_does_not_retry_or_hide_original_error(self):
        model = Model(invalid_analysis=True)
        runtime, session, source = await self.runtime(model)
        run = await self.execute(runtime, session)
        self.assertEqual(str((await run.aget_data()).status), "failed")
        self.assertEqual((model.main, source.calls), (2, 1))
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual(diagnosis.reason, "context")
        self.assertEqual(diagnosis.recovery, "invalid_analysis")
        text = present_message(ChatMessage("assistant", "", status="failed", failure=diagnosis), Language("ko")).text
        self.assertIn("204800", text)
        self.assertIn("분석 형식", text)

    async def test_exhausted_provider_budget_does_not_start_compaction(self):
        model = Model()
        runtime, session, source = await self.runtime(model, attempts=1)
        run = await self.execute(runtime, session)
        self.assertEqual(str((await run.aget_data()).status), "failed")
        self.assertEqual((model.main, source.calls, len(model.requests)), (2, 1, 2))
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual(diagnosis.reason, "context")
        self.assertEqual(diagnosis.recovery, "")

    async def test_rejected_repair_preserves_original_error_and_shows_rejection(self):
        model = Model()
        runtime, session, source = await self.runtime(model)
        with patch.object(ProviderRecovery, "_compact", new=AsyncMock(return_value=CompletionRepair({0: "bad"}))):
            run = await self.execute(runtime, session)
        self.assertEqual((model.main, source.calls), (2, 1))
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual((diagnosis.reason, diagnosis.recovery), ("context", "rejected"))
        self.assertIn("204800", diagnosis.detail)
        for language in ("ko", "en"):
            t = Language(language)
            text = present_message(ChatMessage("assistant", "", status="failed", failure=diagnosis), t).text
            self.assertIn(t("recovery_result_rejected"), text)

    async def test_summary_budget_does_not_apply_partial_result(self):
        model = Model()
        runtime, session, source = await self.runtime(model, recovery_input_chars=2200,
            recovery_summary_chars=1000, recovery_max_summary_calls=1)
        run = await self.execute(runtime, session)
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual(diagnosis.recovery, "summary_incomplete")
        self.assertEqual((model.main, source.calls), (2, 1))

    async def test_recovery_limit_and_disabled_setting(self):
        for enabled, expected in ((True, 3), (False, 2)):
            with self.subTest(enabled=enabled):
                model = Model(always_fail=True)
                runtime, session, source = await self.runtime(model, recovery_enabled=enabled)
                run = await self.execute(runtime, session)
                self.assertEqual(str((await run.aget_data()).status), "failed")
                self.assertEqual((model.main, source.calls), (expected, 1))
                if enabled:
                    diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
                    self.assertEqual(diagnosis.recovery, "exhausted")

    async def test_no_model_call_for_authentication_transient_or_partial_output(self):
        for status, partial in ((401, False), (403, False), (429, False), (503, False), (400, True)):
            with self.subTest(status=status, partial=partial):
                calls = []
                def provider(**request):
                    calls.append(request)
                    if partial:
                        yield chunk("Partial response")
                    raise bad_request(status=status)
                runtime, session, source = await self.runtime(provider, attempts=1)
                run = await self.execute(runtime, session)
                self.assertEqual(str((await run.aget_data()).status), "failed")
                self.assertEqual(len(calls), 1)
                self.assertEqual(source.calls, 0)
                if partial:
                    self.assertEqual((await run.aresponse()).content, "Partial response")

    async def test_model_recommends_stop_without_changing_parameters(self):
        calls = []
        def provider(**request):
            calls.append(deepcopy(request))
            if request["messages"][0]["content"].startswith("Explain a failed model request"):
                yield from answer('{"action":"stop","explanation":"The model does not accept this option; check settings."}')
            else:
                raise bad_request("Unsupported parameter: response_format")
        runtime, session, source = await self.runtime(provider)
        run = await self.execute(runtime, session)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("tools", calls[-1])
        self.assertNotIn("Analyze the command output", calls[-1]["messages"][-1]["content"])
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual(diagnosis.recovery, "no_correction")
        self.assertIn("check settings", diagnosis.explanation)

    async def test_known_context_can_recover_without_model_analysis(self):
        model = Model()
        runtime, session, source = await self.runtime(model, recovery_model_analysis=False)
        run = await self.execute(runtime, session)
        self.assertEqual(str((await run.aget_data()).status), "completed")
        self.assertFalse(model.entered.is_set())
        self.assertEqual(source.calls, 1)

    async def test_model_cannot_authorize_repair_of_an_unsupported_option(self):
        calls = []
        def provider(**request):
            calls.append(request)
            instruction = request["messages"][0]["content"]
            if instruction.startswith("Explain a failed model request"):
                yield from answer('{"action":"compact","explanation":"Guess: too many tokens"}')
            elif len(calls) == 1:
                yield chunk(calls=[call("{}", name="source_read")], finish="tool_calls")
            else:
                raise bad_request("Unsupported parameter: response_format")
        runtime, session, source = await self.runtime(provider)
        run = await self.execute(runtime, session)
        self.assertEqual(str((await run.aget_data()).status), "failed")
        self.assertEqual((len(calls), source.calls), (3, 1))
        diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
        self.assertEqual((diagnosis.reason, diagnosis.recovery), ("parameter", "no_correction"))
        self.assertIn("Guess:", diagnosis.explanation)

    async def test_history_recovery_keeps_role_pairs_and_current_request(self):
        calls = []
        def provider(**request):
            instruction = request["messages"][0]["content"]
            if instruction.startswith("Explain a failed model request"):
                yield from answer('{"action":"compact","explanation":"Summarize the old conversation."}')
            elif instruction.startswith("Summarize the supplied reference material"):
                yield from answer('{"summary":"Earlier conversation discussed the source data."}')
            else:
                messages = request["messages"]
                calls.append(deepcopy(messages))
                if len(calls) == 2:
                    raise bad_request()
                if len(calls) == 3:
                    roles = [m["role"] for m in messages if m["role"] != "system"]
                    if roles != ["user", "assistant", "user"]:
                        raise bad_request("Conversation roles must alternate user/assistant")
                yield from answer("Done")
        runtime, session, source = await self.runtime(provider)
        first = await (await session.run.submit("source " * 1000, agent_id="assistant")).wait(timeout=20)
        self.assertEqual(str((await first.aget_data()).status), "completed")
        second = await self.execute(runtime, session)
        self.assertEqual(str((await second.aget_data()).status), "completed")
        self.assertEqual(calls[-1][-1], calls[-2][-1])
        self.assertLess(len(json.dumps(calls[-1])), len(json.dumps(calls[-2])))
        self.assertEqual(source.calls, 0)

    async def test_recovery_deadline_stops_without_retry(self):
        model = Model(slow=True)
        runtime, session, source = await self.runtime(model, recovery_timeout_seconds=.1)
        try:
            run = await self.execute(runtime, session)
            diagnosis = (await runtime.diagnose_failures(runtime.project.id, session.id, [run.id]))[run.id]
            self.assertEqual(diagnosis.reason, "context")
            self.assertEqual(diagnosis.recovery, "analysis_failed")
            self.assertEqual((model.main, source.calls), (2, 1))
        finally:
            model.release.set()

    async def test_cancel_during_analysis_never_sends_corrected_request(self):
        model = Model(slow=True)
        runtime, session, source = await self.runtime(model)
        task = asyncio.create_task(self.execute(runtime, session))
        try:
            for _ in range(300):
                if model.entered.is_set():
                    break
                await asyncio.sleep(.01)
            self.assertTrue(model.entered.is_set())
            snapshot = await runtime.snapshot()
            self.assertEqual(snapshot.run.retry.kind, "recovery")
            self.assertIn("오류 원인 분석", retry_text(snapshot.run.retry, Language("ko")))
            await runtime.interrupt(session.id)
        finally:
            model.release.set()
        run = await task
        self.assertEqual(str((await run.aget_data()).status), "interrupted")
        self.assertEqual((model.main, source.calls), (2, 1))

    async def test_concurrent_sessions_have_independent_recovery_budgets(self):
        calls, barrier = {}, threading.Barrier(2)
        def provider(**request):
            instruction = request["messages"][0]["content"]
            if instruction.startswith("Explain a failed model request"):
                yield from answer('{"action":"compact","explanation":"Context is too long; reduce reference data."}')
            elif instruction.startswith("Summarize the supplied reference material"):
                yield from answer('{"summary":"120 records, no errors."}')
            else:
                text = next(m["content"] for m in request["messages"] if m["role"] == "user")
                calls[text] = calls.get(text, 0) + 1
                if calls[text] == 1:
                    yield chunk(calls=[call("{}", name="source_read")], finish="tool_calls")
                elif calls[text] == 2:
                    barrier.wait(timeout=10)
                    raise bad_request()
                else:
                    yield from answer(text + " completed")
        runtime, first, source = await self.runtime(provider)
        second_id = await runtime.new_session()
        second = runtime.sessions[second_id]
        requests = await asyncio.gather(first.run.submit("first", agent_id="assistant"),
                                        second.run.submit("second", agent_id="assistant"))
        runs = await asyncio.gather(*(request.wait(timeout=20) for request in requests))
        records = await asyncio.gather(*(run.aget_data() for run in runs))
        self.assertTrue(all(str(record.status) == "completed" for record in records), records)
        self.assertEqual(calls, {"first": 3, "second": 3})
        self.assertEqual(source.calls, 2)
        self.assertEqual(runtime.backend.services.observability_sink.states, {})

    async def test_nested_engine_does_not_borrow_parent_recovery_state(self):
        recovery = ProviderRecovery(GeneralSettings(), None)
        parent, child = object(), object()
        state = RecoveryState(SimpleNamespace(context=parent))
        class Engine:
            async def execute(self, context):
                yield recovery.current.get()
        class Observed(CompletionRecoveryMixin, Engine):
            pass
        engine = Observed()
        engine.provider_recovery = recovery
        token = recovery.current.set(state)
        try:
            self.assertEqual([item async for item in engine.execute(parent)], [state])
            self.assertEqual([item async for item in engine.execute(child)], [None])
            self.assertIs(recovery.current.get(), state)
        finally:
            recovery.current.reset(token)

    async def test_input_selection_preserves_current_request_steering_and_tool_arguments(self):
        from llm.core.model_protocol import ModelRequest, ModelMessage, ToolCall, ToolResult
        history = [ModelMessage("user", "old " * 100), ModelMessage("assistant", "answer " * 100)]
        tail = [ModelMessage("user", "current"),
                ModelMessage("assistant", "", calls=(ToolCall("c", "read", {}),)),
                ModelMessage("tool", result=ToolResult("c", "source " * 100)),
                ModelMessage("user", "steering")]
        client = threading.Lock()
        request = ModelRequest([ModelMessage("system", "rules"), *history, *tail], {"client": client})
        failure = CompletionRecovery(Diagnostic("provider_invalid_request", "too long"), request, 1, (1, 2, 5))
        items = reductions(failure, 100)
        from llm.llm import CompletionRepair
        result = await CompletionPipeline.recover(request, failure.diagnostic, 1, current_index=3,
            decide=lambda _: CompletionRepair({item.index: "summary" for item in items}))
        self.assertIs(result.parameters["client"], client)
        self.assertEqual(result.messages[0], request.messages[0])
        for value in (tail[0], tail[1], tail[3]):
            self.assertIn(value, result.messages)
        self.assertEqual(request.messages[-2].text, "source " * 100)

    def test_recovery_explanation_preserves_completion_validation_kind(self):
        diagnosis = from_diagnostic(Diagnostic("completion_invalid_response" or "execution_failed", "", details={"hub_recovery": {
            "explanation": "The server returned no response choices.", "outcome": "no_correction"}}))
        self.assertEqual(diagnosis.reason, "unknown")
        self.assertIn("no response choices", diagnosis.explanation)

    def test_defaults_validation_and_error_classification(self):
        settings = GeneralSettings()
        self.assertTrue(settings.provider_request_summary)
        for value in (0, -1, True):
            with self.assertRaises(ValueError):
                GeneralSettings(recovery_max_summary_calls=value)
        from llm.providers.runtime import diagnostic_failure
        for status, kind in ((400, "context"), (401, "authentication"), (403, "authentication"), (429, "rate_limit")):
            value = from_diagnostic(diagnostic_failure(bad_request(status=status), operation="completion", stage="invoke"))
            self.assertEqual(value.reason, kind)
        for language in ("ko", "en"):
            for phase in ("analyzing", "compacting", "retrying"):
                text = retry_text(RetrySummary(1, 1, kind="recovery", phase=phase), Language(language))
                self.assertIn("1/1", text)
