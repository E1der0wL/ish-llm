"""Provenance and quota scope isolation, reset and observable preparation effects."""

import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest

from llm.core.policies import UsagePolicy
from llm.services.runtime.tools import ToolCall, current_tool_call
from llm.services.runtime.tool_context import ToolCallContext
from llm.services.runtime.usage import UsageScope, current_usage
from tests.llm.support.runtime_tools import TEST_CONTRACT


def call(name):
    return ToolCall(name, {"nested": [name]}, "step", contract=TEST_CONTRACT.to_dict())


class ExecutionContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_scope_restores_and_public_snapshot_is_detached(self):
        first, second = call("first"), call("second")
        self.assertFalse(ToolCallContext.is_bound())
        with ToolCallContext.scope(first):
            self.assertTrue(ToolCallContext.is_bound())
            snapshot = current_tool_call()
            snapshot.arguments["nested"].append("changed")
            snapshot.contract["revision"] = "changed"
            self.assertEqual(current_tool_call(), first)
            with ToolCallContext.scope(second):
                self.assertEqual(current_tool_call(), second)
            self.assertEqual(current_tool_call(), first)
            with ToolCallContext.scope(None):
                self.assertFalse(ToolCallContext.is_bound())
                self.assertIsNone(current_tool_call())
            self.assertEqual(current_tool_call(), first)
        self.assertFalse(ToolCallContext.is_bound())
        self.assertIsNone(current_tool_call())

    async def test_parallel_tasks_do_not_share_provenance_or_usage_scope(self):
        ready = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        async def invoke(index):
            selected = call(str(index))
            counter = object()
            usage = UsageScope(UsagePolicy.from_policies({}), counter)
            with ToolCallContext.scope(selected), usage.scope():
                ready[index].set()
                await release.wait()
                self.assertEqual(current_tool_call(), selected)
                self.assertIs(current_usage(), usage)
                self.assertIs(current_usage().counter, counter)
                self.assertTrue(UsageScope.is_bound())
            self.assertFalse(ToolCallContext.is_bound())
            self.assertFalse(UsageScope.is_bound())
        tasks = [asyncio.create_task(invoke(index)) for index in range(2)]
        await asyncio.gather(*(value.wait() for value in ready))
        self.assertFalse(ToolCallContext.is_bound())
        self.assertFalse(UsageScope.is_bound())
        release.set()
        await asyncio.gather(*tasks)

    async def test_cancellation_restores_inherited_outer_scopes(self):
        outer, inner = call("outer"), call("inner")
        parent_usage = UsageScope(UsagePolicy.from_policies({}), object())
        child_usage = UsageScope(UsagePolicy.from_policies({}), object())
        ready = asyncio.Event()
        async def invoke():
            try:
                with ToolCallContext.scope(inner), child_usage.scope():
                    ready.set()
                    await asyncio.Future()
            finally:
                self.assertEqual(current_tool_call(), outer)
                self.assertIs(current_usage(), parent_usage)
        with ToolCallContext.scope(outer), parent_usage.scope():
            task = asyncio.create_task(invoke())
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(current_tool_call(), outer)
            self.assertIs(current_usage(), parent_usage)
        self.assertIsNone(current_tool_call())
        self.assertIsNone(current_usage())


class PreparationContextTests(unittest.TestCase):
    def test_active_contexts_block_actual_source_import_before_effect(self):
        from llm.components.tools import ToolComponent
        from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "import-effect"
            manager = ProjectManager(ProjectRepository(root / "workspace"), components=[ToolComponent()])
            project = manager.create("tools", components=["tools"])
            data = manager.component(project, "tools")
            source = f'''from pathlib import Path
from llm.components.tools import tool, ToolContract, ToolEffect, ToolIsolation
Path({str(marker)!r}).write_text("imported", encoding="utf-8")
@tool(ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
                   approval_required=False, operation_key_required=False, retry_fields=()))
async def main():
    """Preparation context fixture."""
    return None
'''
            data.create({"source": source}, identifier="blocked")
            for tool_bound, usage_bound in ((True, False), (False, True), (True, True)):
                with self.subTest(tool=tool_bound, usage=usage_bound), ExitStack() as stack:
                    if tool_bound:
                        stack.enter_context(ToolCallContext.scope(call("blocked")))
                    if usage_bound:
                        stack.enter_context(UsageScope(UsagePolicy.from_policies({}), None).scope())
                    with self.assertRaisesRegex(RuntimeError, "cannot be prepared from a running execution"):
                        data.prepare("blocked")
                    self.assertFalse(marker.exists())
            self.assertEqual(data.prepare("blocked").name, "blocked")
            self.assertEqual(marker.read_text(), "imported")
