"""Independent persisted selection/usage evidence and fail-before-effect checks."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from llm.components.tools import Tool, ToolRegistry
from llm.core.tool_selection_records import ToolBindingRecord, ToolInvocationSelection
from llm.core.tool_usage_records import ToolUsageCheckpoint, ToolUsageError, ToolUsageRecord
from llm.engines.base import EngineContext
from llm.services.runtime.tools import ToolExecutionScope, ToolExecutor, ToolRuntime
from tests.llm.support.continuation import resume_record
from tests.llm.support.runtime_tools import TEST_CONTRACT


USAGE = {"calls": 2, "completed": 1, "revision": 4,
         "pending_approvals": ["pending"], "invocations": ["previous"]}
SELECTION = {"tool": "selected", "arguments": {"value": "input"},
             "binding": {"revision": "original"}}
PAYLOAD = {"node_type": "tool_binding", "status": "completed", "container": True,
           "selection": SELECTION, "request_key": "call", "checkpoint_name": "loop"}
STORED = {**PAYLOAD, "runtime_tool_usage": USAGE}


class SelectionRecordTests(unittest.TestCase):
    def test_wire_and_detached_owner_projections(self):
        source = deepcopy(STORED)
        value = ToolBindingRecord.from_checkpoint(source)
        source["selection"]["binding"]["revision"] = "changed"
        self.assertEqual(value.to_dict(), PAYLOAD)
        self.assertEqual(ToolBindingRecord.create(ToolInvocationSelection.read(SELECTION),
                                                 "call", "loop"), value)
        value.selection.binding["revision"] = "changed"
        value.selection.arguments["value"] = "changed"
        self.assertEqual(value.selection.to_dict(), SELECTION)
        restored = ToolUsageCheckpoint.attach(value.to_dict(), USAGE)
        self.assertEqual(restored, STORED)
        restored["runtime_tool_usage"]["invocations"].append("changed")
        self.assertEqual(USAGE["invocations"], ["previous"])

    def test_all_selection_fields_required_and_unknown_fields_rejected(self):
        for field in PAYLOAD:
            damaged = deepcopy(STORED)
            del damaged[field]
            with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                ToolBindingRecord.from_checkpoint(damaged)
        for field, invalid in (("node_type", "other"), ("status", "waiting"),
                               ("container", False), ("request_key", ""),
                               ("checkpoint_name", ""), ("unknown", "value")):
            with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                ToolBindingRecord.from_checkpoint({**deepcopy(STORED), field: invalid})
        for field in SELECTION:
            selected = deepcopy(SELECTION)
            del selected[field]
            with self.subTest(selection=field), self.assertRaises((TypeError, ValueError)):
                ToolInvocationSelection.read(selected)
        for invalid in ((1, 2), object(), float("nan")):
            with self.subTest(binding=invalid), self.assertRaises((TypeError, ValueError)):
                ToolInvocationSelection("selected", {}, invalid)

    def test_common_usage_required_and_complete_before_projection(self):
        for field in USAGE:
            damaged = deepcopy(STORED)
            del damaged["runtime_tool_usage"][field]
            with self.subTest(field=field), self.assertRaises(ToolUsageError):
                ToolBindingRecord.from_checkpoint(damaged)
        with self.assertRaises(ToolUsageError):
            ToolBindingRecord.from_checkpoint(PAYLOAD)
        for field, invalid in (("calls", True), ("completed", -1), ("revision", "4"),
                               ("pending_approvals", [""]), ("invocations", ["same", "same"]),
                               ("invocations", ("tuple",)), ("extra", 0)):
            with self.subTest(field=field), self.assertRaises(ToolUsageError):
                ToolUsageRecord.from_dict({**deepcopy(USAGE), field: invalid})

    def test_scope_preserves_latest_evidence_and_failed_restore_is_atomic(self):
        scope = ToolExecutionScope(ToolRuntime(), policy={"max_calls": 3})
        scope.restore([deepcopy(USAGE)])
        self.assertEqual((scope.calls, scope.completed, scope.revision), (2, 1, 4))
        self.assertEqual(scope.invocations, {"previous"})
        self.assertEqual(scope.pending_approvals, ["pending"])
        with self.assertRaises(ToolUsageError):
            scope.restore([ToolUsageRecord.initial().to_dict(), {"calls": 0}])
        self.assertEqual((scope.calls, scope.completed, scope.revision, scope.max_calls), (2, 1, 4, 3))
        self.assertEqual(scope.checkpoint_record(PAYLOAD),
                         {**PAYLOAD, "runtime_tool_usage": {**USAGE, "revision": 5}})

    def test_registry_requires_binding_key_and_common_records(self):
        class Handler:
            def bind_arguments(self, arguments, context):
                return {"revision": "original"}

        registry = ToolRegistry([Tool("selected", "test", {"type": "object"}, Handler(),
                                      contract=TEST_CONTRACT)])
        def context(checkpoint):
            return EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
                                 SimpleNamespace(id="r"), (), checkpoint=checkpoint)
        registry.validate_selections(context(None))
        registry.validate_selections(context({"records": {"@tool-binding:call": STORED}}))
        for checkpoint in ({}, {"records": {"@tool-binding:other": STORED}},
                           {"records": {"@tool-binding:call": {k: v for k, v in STORED.items()
                                                                 if k != "node_type"}}}):
            with self.subTest(checkpoint=checkpoint), self.assertRaises((KeyError, TypeError, ValueError)):
                registry.validate_selections(context(checkpoint))

    def test_nested_payload_uses_outer_usage_without_duplicate_storage(self):
        root = EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"), SimpleNamespace(id="r"), ())
        nested = EngineContext(root.project, root.session, root.run, (), checkpoint_scope="child")
        self.assertEqual(nested.read_tool_binding(PAYLOAD).to_dict(), PAYLOAD)
        with self.assertRaises(ToolUsageError):
            root.read_tool_binding(PAYLOAD)
        with self.assertRaises(ToolUsageError):
            ToolUsageCheckpoint.read_records({"envelope": PAYLOAD})
        with self.assertRaises((TypeError, ValueError)):
            nested.read_tool_binding({k: v for k, v in PAYLOAD.items() if k != "selection"})


class SelectionEffectTests(unittest.IsolatedAsyncioTestCase):
    async def test_damaged_selection_cannot_rewrite_evidence_authorize_or_run(self):
        with TemporaryDirectory() as directory:
            effect = Path(directory, "effects")
            class Handler:
                def bind_arguments(self, arguments, context):
                    return {"revision": "original"}

                async def __call__(self, arguments):
                    effect.write_text(effect.read_text() + "effect\n" if effect.exists() else "effect\n")
                    return "done"

            tool = Tool("selected", "test", {"type": "object"}, Handler(), contract=TEST_CONTRACT)
            authorize = AsyncMock(return_value=True)
            cases = []
            for field in (*PAYLOAD, "runtime_tool_usage"):
                damaged = deepcopy(STORED)
                del damaged[field]
                cases.append(damaged)
            cases.extend({**deepcopy(STORED), field: value} for field, value in (
                ("request_key", "other"), ("checkpoint_name", "other"),
                ("status", "waiting"), ("container", False)))
            for damaged in cases:
                checkpoint = {"records": {"@tool-binding:call": damaged}}
                original = deepcopy(checkpoint)
                scope = ToolExecutionScope(ToolRuntime(authorize=authorize))
                context = EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
                    SimpleNamespace(id="r", input_message_id="m", metadata={"resume": resume_record(decisions={})}),
                    (), tool_scope=scope, checkpoint=checkpoint)
                events = []
                with self.subTest(record=damaged), self.assertRaises((TypeError, ValueError)):
                    async for event in ToolExecutor().execute(tool, {"value": "input"}, result={},
                            context=context, metadata={"checkpoint_name": "loop"}, request_key="call"):
                        events.append(event)
                self.assertEqual(events, [])
                self.assertEqual(checkpoint, original)
                self.assertFalse(effect.exists())
                authorize.assert_not_awaited()
                self.assertEqual(scope.calls, 0)

            context = EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
                SimpleNamespace(id="r", input_message_id="m", metadata={"resume": resume_record(decisions={})}),
                (), tool_scope=ToolExecutionScope(ToolRuntime(authorize=authorize)))
            async for _ in ToolExecutor().execute(tool, {"value": "input"}, result={}, context=context,
                    metadata={"checkpoint_name": "loop"}, request_key="call"):
                pass
            self.assertEqual(effect.read_text(), "effect\n")
            authorize.assert_awaited_once()
