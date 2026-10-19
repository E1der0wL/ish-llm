"""Hub selects generic repairs while llm retains budgets, effects and persistence."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.environment import ENVIRONMENT_OPTION
from llm.components.base import Component
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.engines.loop import LoopEngine
from llm.policies.retry import RetryDecision
from llm.services.runtime.tools import ToolExecutionError
from tests.llm.test_loop import ScriptedCompletion, call, chunk


class ExampleTools(Component):
    name = directory = "repair_examples"
    capabilities = ("tools",)

    def __init__(self, handler, contract=None):
        self.handler = handler
        self.contract = contract or ToolContract(effect="read_only", retry_fields=("format",))

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        schema = {"type": "object", "properties": {"format": {"type": "string", "enum": ["old", "new"]},
            "count": {"type": "integer", "minimum": 1}}, "required": ["format", "count"], "additionalProperties": False}
        return ToolRegistry((Tool("convert", "Use the new format after a format error.", schema, self.handler,
                                  contract=self.contract),))


class ToolRetryTests(unittest.IsolatedAsyncioTestCase):
    async def runtime(self, root, provider, **kwargs):
        runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
            engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}, **kwargs))
        await runtime.start()
        self.addAsyncCleanup(runtime.close)
        return runtime

    async def run_request(self, runtime, *, options=None):
        session = await runtime.project.sessions.acreate("repair")
        return await (await session.run.submit("Read and process the requested data", engine="loop",
                                                **({"engine_options": options} if options else {}))).wait()

    async def test_file_repair_uses_captured_root_and_records_completion_in_same_run(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"path":"missing/report.txt"}', name="file_read")], finish="tool_calls")],
            [chunk('{"changes":{"path":"src/report.txt"}}', finish="stop")],
            [chunk("Read successfully", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            working = Path(root) / "work"
            (working / "src").mkdir(parents=True)
            (working / "src/report.txt").write_text("confirmed content")
            runtime = await self.runtime(Path(root) / "data", provider)
            result = await self.run_request(runtime, options={ENVIRONMENT_OPTION: {
                "cwd": str(working), "env": {"PWD": str(working), "DO_NOT_SEND_ENV": "private-value"}}})
            self.assertEqual(str(result.data.status), "completed", result.data.error)
            self.assertEqual(len(provider.requests), 3)
            correction = json.loads(provider.requests[1]["messages"][-1]["content"])
            self.assertEqual(correction["failure"]["changeable"], ["path"])
            self.assertEqual(correction["evidence"]["existing_path_candidates"], ["src/report.txt"])
            self.assertEqual(correction["working_directory"], str(working))
            self.assertNotIn("private-value", json.dumps(provider.requests[1]))
            self.assertNotIn("tools", provider.requests[1])
            self.assertEqual(len(result.data.metadata["completions"]), 3)
            steps = await result.steps.alist()
            read, = [step for step in steps if step.kind == "tool"]
            self.assertEqual(read.output.data["text"], "confirmed content")
            self.assertEqual(len(read.metadata["retry_failures"]), 1)
            self.assertEqual(len([step for step in steps if step.metadata.get("hub_tool_repair")]), 1)
            self.assertEqual((await result.aresponse()).content, "Read successfully")
            await runtime.close()

    async def test_generic_non_path_tool_and_custom_evidence(self):
        calls = []
        async def handler(args):
            calls.append(deepcopy(args))
            if args["format"] == "old":
                raise ToolExecutionError("Use a supported format", effect="none", recoverable=True)
            return {"converted": args["count"]}
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"format":"old","count":3}', name="convert")], finish="tool_calls")],
            [chunk('{"changes":{"format":"new"}}', finish="stop")], [chunk("done", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),),
                tool_retry_evidence={"convert": lambda request: {"supported_format": "new"}})
            result = await self.run_request(runtime)
            self.assertEqual(str(result.data.status), "completed", result.data.error)
            self.assertEqual(calls, [{"format": "old", "count": 3}, {"format": "new", "count": 3}])
            payload = json.loads(provider.requests[1]["messages"][-1]["content"])
            self.assertEqual(payload["evidence"], {"supported_format": "new"})
            await runtime.close()

    async def test_invalid_declined_and_unchanged_proposals_preserve_original_failure(self):
        for proposal in ('{"changes":{"count":99}}', '{"changes":{"format":7}}',
                         '{"changes":{"format":"old"}}', '{"changes":null}', 'not json'):
            with self.subTest(proposal=proposal), tempfile.TemporaryDirectory() as root:
                calls = []
                async def handler(args):
                    calls.append(args)
                    raise ToolExecutionError("original format error", effect="none", recoverable=True)
                provider = ScriptedCompletion(
                    [chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")],
                    [chunk(proposal, finish="stop")])
                runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),))
                try:
                    result = await self.run_request(runtime)
                    self.assertEqual(str(result.data.status), "failed")
                    self.assertIn("original format error", result.data.error)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(len(provider.requests), 2)
                finally:
                    await runtime.close()

    async def test_custom_chooser_cancellation_stops_without_reexecution(self):
        entered = asyncio.Event()
        calls = []
        async def handler(args):
            calls.append(args)
            raise ToolExecutionError("original", effect="none", recoverable=True)
        async def choose(request):
            entered.set()
            await asyncio.Event().wait()
        provider = ScriptedCompletion([chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),),
                                         tool_retry_chooser=choose)
            session = await runtime.project.sessions.acreate("cancel repair")
            request = await session.run.submit("go", engine="loop")
            await asyncio.wait_for(entered.wait(), 5)
            await session.run.interrupt()
            result = await asyncio.wait_for(request.wait(), 5)
            self.assertEqual(str(result.data.status), "interrupted")
            self.assertEqual(len(calls), 1)
            await runtime.close()

    async def test_disabled_and_unsafe_failures_do_not_call_chooser(self):
        for policy, error in (({"max_retries": 0}, ToolExecutionError("missing", effect="none", recoverable=True)),
                              ({"max_retries": 2}, RuntimeError("unclassified")),
                              ({"max_retries": 2}, ToolExecutionError("uncertain", effect="uncertain"))):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as root:
                async def handler(args):
                    raise error
                chosen = []
                def choose(request):
                    chosen.append(request)
                    return RetryDecision({"format": "new"})
                provider = ScriptedCompletion([chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")])
                runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),),
                    tool_retry_chooser=choose, project_config={"policies": {"tool_retry": policy}})
                try:
                    result = await self.run_request(runtime)
                    self.assertEqual(str(result.data.status), "failed")
                    self.assertEqual(chosen, [])
                finally:
                    await runtime.close()

    async def test_repair_model_usage_admission_precedes_provider_call(self):
        async def handler(args):
            raise ToolExecutionError("missing", effect="none", recoverable=True)
        provider = ScriptedCompletion([chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),),
                project_config={"policies": {"usage": {"max_calls": 1}}})
            result = await self.run_request(runtime)
            self.assertEqual(str(result.data.status), "failed")
            self.assertEqual(result.data.error_code, "usage_limit")
            self.assertEqual(len(provider.requests), 1)
            await runtime.close()

    async def test_attempt_limit_and_successful_earlier_tool_are_preserved(self):
        calls, decisions = [], []
        async def handler(args):
            calls.append(deepcopy(args))
            if args["count"] == 1:
                return "first success"
            raise ToolExecutionError("unsupported format", effect="none", recoverable=True)
        def choose(request):
            decisions.append(request.failure.attempt)
            return RetryDecision({"format": "new"})
        provider = ScriptedCompletion([chunk(calls=[
            call('{"format":"old","count":1}', name="convert", call_id="first"),
            call('{"format":"old","count":2}', name="convert", call_id="second", index=1)], finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler),),
                tool_retry_chooser=choose, project_config={"policies": {"tool_retry": {"max_retries": 1}}})
            result = await self.run_request(runtime)
            self.assertEqual(str(result.data.status), "failed")
            self.assertEqual(calls, [{"format": "old", "count": 1}, {"format": "old", "count": 2},
                                    {"format": "new", "count": 2}])
            self.assertEqual(decisions, [1])
            await runtime.close()

    async def test_changed_arguments_require_fresh_approval_and_resume_only_once(self):
        calls, decisions = [], []
        async def handler(args):
            calls.append(args["format"])
            if args["format"] == "old":
                raise ToolExecutionError("unsupported", effect="none", recoverable=True)
            return "success"
        def choose(request):
            decisions.append(request.failure.attempt)
            return RetryDecision({"format": "new"})
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")],
            [chunk("done", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(root, provider, component_factories=(lambda: ExampleTools(handler,
                ToolContract(approval_required=True, retry_fields=("format",))),), tool_retry_chooser=choose)
            session = await runtime.project.sessions.acreate("approval")
            initial = await (await session.run.submit("go", engine="loop")).wait()
            self.assertEqual(str(initial.data.status), "paused", initial.data.error)
            request, = await initial.ainteractions(pending_only=True)
            await initial.arespond(request.respond("approve"))
            repair = await (await session.run.resume(initial.id, engine="loop")).wait()
            self.assertEqual(str(repair.data.status), "paused", repair.data.error)
            request, = await repair.ainteractions(pending_only=True)
            self.assertEqual(request.action["arguments"]["format"], "new")
            self.assertEqual(calls, ["old"])
            await repair.arespond(request.respond("approve"))
            complete = await (await session.run.resume(repair.id, engine="loop")).wait()
            self.assertEqual(str(complete.data.status), "completed", complete.data.error)
            self.assertEqual(calls, ["old", "new"])
            self.assertEqual(decisions, [1])
            await runtime.close()

    async def test_concurrent_sessions_keep_chooser_and_roots_separate(self):
        from tests.hub.test_shell_environment import snapshot
        entered, seen = asyncio.Event(), []
        async def choose(request):
            seen.append(request.working_directory)
            if len(seen) == 2:
                entered.set()
            await entered.wait()
            return RetryDecision({"path": request.evidence["existing_path_candidates"][0]})
        def provider(**kwargs):
            if kwargs["messages"][-1]["role"] == "tool":
                yield chunk("done", finish="stop")
            else:
                yield chunk(calls=[call('{"path":"missing/data.txt"}', name="file_read")], finish="tool_calls")
        with tempfile.TemporaryDirectory() as root:
            runtime = await self.runtime(Path(root) / "storage", provider, tool_retry_chooser=choose)
            pending = []
            for name in ("first", "second"):
                working = Path(root) / name
                working.mkdir()
                (working / "data.txt").write_text(name)
                session = await runtime.project.sessions.acreate(name)
                pending.append(await session.run.submit(name, engine="loop", engine_options={
                    ENVIRONMENT_OPTION: snapshot(working, name).to_dict()}))
            results = await asyncio.wait_for(asyncio.gather(*(request.wait() for request in pending)), 10)
            for result, name in zip(results, ("first", "second")):
                self.assertEqual(str(result.data.status), "completed", result.data.error)
                read, = [step for step in await result.steps.alist() if step.kind == "tool"]
                self.assertEqual(read.output.data["text"], name)
            self.assertEqual(set(seen), {str(Path(root) / "first"), str(Path(root) / "second")})
            await runtime.close()

    async def test_graph_tool_uses_generic_selector_and_loop_model(self):
        from llm.components.workflows import WorkflowGraph
        provider = ScriptedCompletion([chunk('{"changes":{"path":"data.txt"}}', finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "data.txt").write_text("graph data")
            runtime = await self.runtime(Path(root) / "storage", provider, file_root=root)
            graph = (WorkflowGraph(entry="read").node("read", "tool", tool="file_read", arguments={"path": "lost/data.txt"})
                     .node("end", "end").connect("read", "end"))
            await (await runtime.project.components.aget("workflows")).acreate(graph.to_dict(), identifier="reader")
            session = await runtime.project.sessions.acreate("graph")
            result = await (await session.run.submit("read", engine="graph", engine_options={"workflow": "reader"})).wait()
            self.assertEqual(str(result.data.status), "completed", result.data.error)
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(len(result.data.metadata["completions"]), 1)
            await runtime.close()
