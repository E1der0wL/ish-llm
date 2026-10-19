"""Hub presets exercise llm's bounded, pre-output retry without replaying tools."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.project.defaults import provider_defaults
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.test_workspaces import HubTestWorkspaces
from llm.components.tools.builtin import TestWorkspaces
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.hub.support import eventually


class HTTPFailure(RuntimeError):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_creation_policy_is_editable_and_existing_projects_are_not_reseeded(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root))
            try:
                await runtime.start()
                record = await runtime.project.aget_data()
                for name in ("loop", "_hub_title"):
                    self.assertEqual(record.config.parameters["engines"][name]["policy"]["provider"], provider_defaults())
                values = deepcopy(record.config)
                values.parameters["engines"]["loop"]["policy"]["provider"] = {"max_attempts": 1}
                await runtime.project.asave(config=values)
            finally:
                await runtime.close()
            runtime = HubRuntime(HubConfig(root))
            try:
                await runtime.start()
                record = await runtime.project.aget_data()
                self.assertEqual(record.config.parameters["engines"]["loop"]["policy"]["provider"], {"max_attempts": 1})
            finally:
                await runtime.close()

    async def execute(self, provider, *, delay=0, interrupt=False):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                project_config={"parameters": {"engines": {"loop": {"policy": {"provider": {
                    "max_attempts": 4, "delay_seconds": delay, "max_delay_seconds": 60}}}}}},
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                session = runtime.sessions[identifier]
                request = await session.run.submit("Continue the task", engine="loop")
                if interrupt:
                    await eventually(lambda: len(provider.requests) == 1)
                    await session.run.interrupt()
                async with asyncio.timeout(10):
                    handle = await request.wait()
                return await handle.aresult(), await session.aconversation(), await handle.steps.alist()
            finally:
                await runtime.close()

    async def test_transient_failures_after_tool_retry_only_the_completion(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call("{}", name="skill_list")], finish="tool_calls")],
            [HTTPFailure(503)], [HTTPFailure(429)], [chunk("Finished", finish="stop")])
        result, messages, steps = await self.execute(provider)
        self.assertEqual(str(result.status), "completed")
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual(sum(s.kind == "tool" for s in steps), 1)
        self.assertEqual(sum(str(m.role) == "user" for m in messages), 1)
        self.assertEqual(provider.requests[1]["messages"], provider.requests[3]["messages"])

    async def test_retry_is_bounded_and_does_not_replay_partial_or_auth_failures(self):
        for chunks, count in (([HTTPFailure(503)], 4), ([HTTPFailure(401)], 1),
                              ([chunk("Partial"), HTTPFailure(503)], 1)):
            with self.subTest(count=count, partial=len(chunks) > 1):
                provider = ScriptedCompletion(*[chunks for _ in range(4)])
                result, messages, _ = await self.execute(provider)
                self.assertEqual(str(result.status), "failed")
                self.assertEqual(len(provider.requests), count)
                if len(chunks) > 1:
                    self.assertEqual(messages[-1].content, "Partial")

    async def test_interrupt_does_not_wait_for_backoff_or_retry(self):
        provider = ScriptedCompletion([HTTPFailure(429)], [chunk("Must not run", finish="stop")])
        result, _, _ = await self.execute(provider, delay=60, interrupt=True)
        self.assertEqual(str(result.status), "interrupted")
        self.assertEqual(len(provider.requests), 1)

    async def test_invalid_sandbox_argv_is_feedback_without_launch_or_error_masking(self):
        handler = object.__new__(HubTestWorkspaces)
        parent = AsyncMock(return_value={"returncode": 0})
        with patch.object(TestWorkspaces, "__call__", parent):
            result = await handler({"workspace_id": "w", "argv": ["python", "test.py"]})
            self.assertFalse(result["executed"])
            parent.assert_not_awaited()
            await handler({"workspace_id": "w", "argv": ["/bin/echo", ""]})
            parent.assert_awaited_once()
            parent.side_effect = RuntimeError("sandbox unavailable")
            with self.assertRaisesRegex(RuntimeError, "sandbox unavailable"):
                await handler({"workspace_id": "w", "argv": ["/bin/sh", "-c", "true"]})

    async def test_model_corrects_bad_sandbox_command_and_completes_the_same_task(self):
        requests = []
        def provider(**params):
            requests.append(deepcopy(params))
            tools = [json.loads(m["content"]) for m in params["messages"] if m["role"] == "tool"]
            if not tools:
                name, arguments = "test_workspace_create", {"source_paths": []}
            else:
                workspace = tools[0]["workspace_id"]
                if len(tools) == 1:
                    name, arguments = "test_workspace_execute", {"workspace_id": workspace, "argv": ["sh", "-c", "printf stable"]}
                elif len(tools) == 2:
                    self.assertFalse(tools[-1]["executed"])
                    name, arguments = "test_workspace_execute", {"workspace_id": workspace, "argv": ["/bin/sh", "-c", "printf stable"]}
                else:
                    self.assertEqual(tools[-1]["returncode"], 0)
                    self.assertEqual(tools[-1]["stdout"], "stable")
                    yield chunk("Verified", finish="stop")
                    return
            yield chunk(calls=[call(json.dumps(arguments), name=name, call_id=str(len(tools)))], finish="tool_calls")
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                session = runtime.sessions[identifier]
                handle = await (await session.run.submit("Run the sandbox test", engine="loop")).wait()
                approvals = 0
                while str(handle.data.status) == "paused" and approvals < 4:
                    interaction, = await handle.ainteractions()
                    await handle.arespond(interaction.respond("approve"))
                    approvals += 1
                    handle = await (await session.run.resume(handle.id, engine="loop")).wait()
                self.assertEqual(str(handle.data.status), "completed", handle.data.error)
                self.assertEqual(len(requests), 4)
                self.assertEqual(approvals, 3)
            finally:
                await runtime.close()
