"""Approval continuation shows logical calls and never repeats their effects."""
from llm.providers.litellm import LiteLLMCompletionAdapter

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.steps import display_steps
from hub.backend.execution.thinking import ThinkingPhases
from hub.backend.execution.trace import merge_trace
from hub.locales import Language
from hub.model import ChatMessage, HubSnapshot, RunSummary, SessionSummary, TraceItem
from hub.ui.presentation import present
from llm.components.tools.builtin import BuiltinToolRunner
from llm.engines.loop import LoopEngine
from tests.hub.test_live import until
from tests.llm.test_loop import call, chunk


class ToolHistoryTests(unittest.TestCase):
    def test_resumed_tool_updates_its_original_timeline_position(self):
        previous = (TraceItem("c1", "reasoning", "Before", started_at="01"),
                    TraceItem("tool", "tool", "shell_execute", "waiting_approval", "02"))
        resumed = (TraceItem("tool", "tool", "shell_execute", "completed", "05"),
                   TraceItem("c2", "reasoning", "After", started_at="06"))
        result = merge_trace(previous, resumed)
        self.assertEqual([item.id for item in result], ["c1", "tool", "c2"])
        self.assertEqual(result[1].started_at, "02")
        self.assertEqual(result[1].status, "completed")

    def test_reuse_is_not_new_work_and_waiting_keeps_its_identity(self):
        def step(identifier, status, **metadata):
            return NS(id=identifier, name="shell_execute", kind="tool", status=status, metadata=metadata)
        waiting = step("cancelled-step", "cancelled", phase="waiting_approval",
                       tool_call_id="call-1", iteration=2, checkpoint_name="loop")
        running = step("resumed-step", "running", phase="executing",
                       tool_call_id="call-1", iteration=2, checkpoint_name="loop")
        self.assertEqual(display_steps([waiting])[0].status, "waiting_approval")
        self.assertEqual(display_steps([waiting])[0].id, display_steps([running])[0].id)
        self.assertEqual(display_steps([step("reuse", "running", reused=True)]), ())
        self.assertEqual(display_steps([step("graph-reuse", "running", reused_step_id="original")]), ())
        # An ordinary cancellation and a genuinely repeated call must stay visible.
        cancelled = step("cancelled", "cancelled")
        self.assertEqual(display_steps([cancelled])[0].status, "cancelled")
        later = step("new-step", "running", tool_call_id="call-2", iteration=3, checkpoint_name="loop")
        self.assertNotEqual(display_steps([running])[0].id, display_steps([later])[0].id)
        branches = [step(str(i), "running", tool_call_id="call-1", iteration=1,
                         node_checkpoint_key=json.dumps(["parallel", i])) for i in range(2)]
        self.assertEqual(len({s.id for s in display_steps(branches)}), 2)

    def test_resume_has_its_own_activity_before_new_work(self):
        phase = ThinkingPhases().activity(NS(id="r", status="running", metadata={"resume": {"run_id": "old"}}))
        self.assertEqual(phase, "resuming")
        snapshot = HubSnapshot("p", "Project", "model", "file", (SessionSummary("s", "Session", "running"),), "s",
            (ChatMessage("assistant", "Previous answer", status="streaming", phase=phase),),
            run=RunSummary("r", "running", "loop", 0, True, (), ""))
        self.assertEqual(present(snapshot, Language()).messages[-1].phase, "resuming")


@unittest.skipUnless(shutil.which("bwrap"), "Bubblewrap required for real sandbox execution")
class ToolContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_approvals_and_restart_reuse_actual_tool_receipts(self):
        requests, executed = [], []
        workspace_id = ""

        def provider(**kwargs):
            nonlocal workspace_id
            requests.append(deepcopy(kwargs))
            for message in kwargs["messages"]:
                if message.get("role") == "tool" and message.get("name") == "test_workspace_create":
                    workspace_id = json.loads(message["content"])["workspace_id"]
            index = len(requests)
            names = {2: "test_workspace_execute", 3: "shell_execute", 4: "shell_execute", 5: "ask_user"}
            arguments = {
                2: {"workspace_id": workspace_id, "argv": ["/bin/sh", "-c", "printf 'sandbox\\n' >> receipt.txt"]},
                3: {"command": "printf 'host\\n' >> effects.txt"},
                4: {"command": "printf 'host\\n' >> effects.txt"},
                5: {"question": "Finished checking?"},
            }
            if index == 1:
                calls = [call("{}", name="skill_list", call_id="list"),
                         call('{"source_paths":[]}', name="test_workspace_create", call_id="create", index=1)]
            elif index in names:
                calls = [call(json.dumps(arguments[index]), name=names[index], call_id=f"call-{index}")]
            else:
                return iter([chunk("Done", finish="stop")])
            return iter([chunk(calls=calls, finish="tool_calls")])

        original = BuiltinToolRunner.__call__

        async def observed(runner, tool, invocation):
            executed.append(tool.name)
            return await original(runner, tool, invocation)

        def history(snapshot):
            return next(m.tools for m in snapshot.messages if m.role == "reasoning")

        with tempfile.TemporaryDirectory() as root, patch.object(BuiltinToolRunner, "__call__", observed):
            config = HubConfig(Path(root) / "data", file_root=root, auto_title=False, model="test/model",
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "Perform the checks")
                expected = ["skill_list", "test_workspace_create", "test_workspace_execute", "shell_execute", "shell_execute"]
                for approval in range(4):
                    await runtime.sessions[sid].run.wait_idle()
                    snapshot = await runtime.snapshot()
                    self.assertEqual(snapshot.status, "waiting_approval", snapshot.run.error)
                    tools = history(snapshot)
                    self.assertEqual([s.name for s in tools], expected[:approval + 2])
                    self.assertEqual([s.status for s in tools], ["completed"] * (approval + 1) + ["waiting_approval"])
                    self.assertEqual(len(requests), approval + 1)
                    runtime.snapshot_reader.clear()
                    self.assertEqual(history(await runtime.snapshot()), tools)
                    if approval == 1:
                        project_id = runtime.project.id
                        await runtime.close()
                        runtime = HubRuntime(replace(config, project_id=project_id, session_id=sid))
                        await runtime.start()
                        snapshot = await runtime.snapshot()
                        self.assertEqual(history(snapshot), tools)
                    item = snapshot.questions[0]
                    await runtime.answer_question(runtime.project.id, sid, item["run_id"], item["request"], "approve", None)
                await until(lambda: bool(runtime.builtin_tools.questions.pending))
                snapshot = await runtime.snapshot()
                tools = history(snapshot)
                self.assertEqual([s.name for s in tools], [*expected, "ask_user"])
                self.assertEqual([s.status for s in tools], ["completed"] * 5 + ["running"])
                self.assertEqual(Counter(executed), Counter([*expected, "ask_user"]))
                self.assertEqual(Path(root, "effects.txt").read_text(), "host\nhost\n")
                sandbox = config.workspace / ".hub/test-workspaces" / runtime.project.id / workspace_id / "files/receipt.txt"
                self.assertEqual(sandbox.read_text(), "sandbox\n")
                item = snapshot.questions[0]
                await runtime.answer_question(runtime.project.id, sid, item["run_id"], item["request"], "reply", "Yes")
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual((await runtime.snapshot()).run.status, "completed")
                self.assertEqual(len(requests), 6)
            finally:
                await runtime.close()
