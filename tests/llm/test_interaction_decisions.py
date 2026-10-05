"""승인/재개 값의 공통 해석. 형식 해석은 권한이나 새 응답 영수증을 만들지 않는다."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from jsonschema import ValidationError

from llm.core.interactions import (
    InteractionOption, InteractionRequest, approval_request, same_interaction_value,
)
from llm.components.tools import Tool
from llm.engines.base import EngineContext, EngineEvent, EngineEventType
from llm.engines.graph import GraphEngine
from llm.engines.graph.checkpoints import EngineCheckpointScope
from llm.engines.loop import LoopEngine
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.tools import ToolExecutor, ToolInvocationError


class DecisionValueTests(unittest.TestCase):
    def confirmation(self):
        return replace(approval_request("Review"), kind="confirmation", input_schema={
            "type": "object", "properties": {"value": {"type": "integer"}}, "additionalProperties": False,
        }).bind("graph", "review", decision_key="approved", input_key="state")

    def test_json_comparison_does_not_coerce_nested_values(self):
        for left, right in ((True, 1), (False, 0), (1, 1.0),
                            ({"x": [True]}, {"x": [1]}), ([{"ok": False}], [{"ok": 0}])):
            with self.subTest(left=left, right=right):
                self.assertFalse(same_interaction_value(left, right))
        self.assertTrue(same_interaction_value({"b": None, "a": [1]}, {"a": [1], "b": None}))
        for value in (float("nan"), float("inf"), object(), (1,)):
            with self.subTest(value=type(value)), self.assertRaises((ValueError, TypeError)):
                same_interaction_value(value, value)

    def test_loop_and_graph_approval_select_same_effect(self):
        request = approval_request("Tool")
        for graph in (False, True):
            bound = request.bind("graph" if graph else "loop", "key", decision_key="approved" if graph else None)
            for decision, effect in ((True, "approve"), (False, "deny")):
                value = {"approved": decision} if graph else decision
                option, supplied = bound.select_decision(value)
                self.assertEqual(option.effect, effect)
                self.assertIsNone(supplied)
                self.assertEqual(bound.decision_for(option.id), value)
            for invalid in ({}, None, {"approved": 1} if graph else 1):
                with self.subTest(graph=graph, invalid=invalid), self.assertRaises(ValueError):
                    bound.select_decision(invalid)

    def test_nested_option_values_are_compared_without_boolean_number_aliases(self):
        request = InteractionRequest("Pick", (
            InteractionOption("boolean", "Boolean", value={"data": [{"flag": True}]}),
            InteractionOption("number", "Number", value={"data": [{"flag": 1}]})))
        self.assertEqual(request.select_decision({"data": [{"flag": True}]})[0].id, "boolean")
        self.assertEqual(request.select_decision({"data": [{"flag": 1}]})[0].id, "number")

    def test_confirmation_requires_explicit_empty_handling_and_validates_input(self):
        request = self.confirmation()
        for decision in ({}, {"state": {"value": 2}}):
            with self.assertRaises(ValueError):
                request.select_decision(decision)
            option, supplied = request.select_decision(decision, confirm_empty=True)
            self.assertEqual(option.effect, "approve")
            self.assertEqual(supplied, decision.get("state"))
        self.assertEqual(request.select_decision({"approved": False}, confirm_empty=True)[0].effect, "deny")
        for invalid in ({"approved": True, "state": {"value": "two"}},
                        {"approved": False, "state": {}}, {"state": {"other": 1}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                request.select_decision(invalid, confirm_empty=True)
        for kind in ("approval", "choice"):
            with self.assertRaises(ValueError):
                replace(request, kind=kind).select_decision({}, confirm_empty=True)

    def test_forward_and_reverse_preserve_custom_choices_and_inputs(self):
        request = replace(self.confirmation(), kind="choice", options=(
            InteractionOption("later", "Later", value={"mode": "defer"}),
            InteractionOption("now", "Now", value={"mode": "run"})))
        supplied = {"value": 3}
        response = request.respond("later", value=supplied)
        decision = response.decision(request)
        before = deepcopy((request.to_dict(), decision))
        option, value = request.select_decision(decision)
        self.assertEqual(option.id, "later")
        self.assertEqual(value, supplied)
        option.value["mode"] = "changed"
        value["value"] = 99
        self.assertEqual((request.to_dict(), decision), before)

    def test_ambiguous_raw_choice_rejected_but_option_id_remains_valid(self):
        request = InteractionRequest("Pick", (
            InteractionOption("a", "First", value=True), InteractionOption("b", "Second", value=True)))
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            request.select_decision(True)
        self.assertIs(request.respond("a").decision(request), True)
        request = replace(self.confirmation(), options=(
            InteractionOption("a", "First", effect="approve", value={"approved": True}),
            InteractionOption("b", "Second", effect="approve", value={"approved": False})))
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            request.select_decision({}, confirm_empty=True)

    def test_payload_decoding_does_not_replace_lifecycle_validation(self):
        pending = approval_request("Tool")
        response = pending.respond("approve")
        for request in (replace(pending, expires_at="2000-01-01T00:00:00+00:00"),
                        replace(pending, status="cancelled"), replace(pending, revision=2)):
            self.assertEqual(request.select_decision(True)[0].effect, "approve")
            with self.assertRaises(ValueError):
                response.decision(request)
            if request.revision == 2:
                self.assertEqual(request.respond("approve").request_revision, 2)
            else:
                with self.assertRaises(ValueError):
                    request.respond("approve")

    def test_decision_data_cannot_replace_selected_option_or_add_undeclared_input(self):
        request = replace(self.confirmation(), binding={"input_key": "approved"})
        with self.assertRaisesRegex(ValueError, "replace"):
            request.decision_for("approve", value={"value": 3})
        with self.assertRaisesRegex(ValueError, "does not accept"):
            approval_request("Tool").decision_for("approve", value={})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            approval_request("Tool").decision_for("missing")


class DecisionRepositoryTests(unittest.TestCase):
    def test_renewal_has_no_product_chain_limit_but_rejects_cycles(self):
        repository = InteractionRepository()
        requests = [approval_request("Review").bind("loop", "key") for _ in range(140)]
        envelopes = {request.id: {"replacement": requests[i + 1].to_dict()}
                     for i, request in enumerate(requests[:-1])}
        with patch.object(repository, "envelope", side_effect=lambda run, request: envelopes.get(request.id, {})):
            self.assertEqual(repository.effective(None, [requests[0]]), [requests[-1]])
            envelopes[requests[-1].id] = {"replacement": requests[0].to_dict()}
            with self.assertRaisesRegex(ValueError, "renewal chain"):
                repository.effective(None, [requests[0]])

    def test_missing_denial_and_confirmation_empty_remain_distinct(self):
        repository = InteractionRepository()
        request = approval_request("Tool").bind("loop", "key")
        self.assertEqual(repository.decisions([request], [], {}, confirm=True), ({}, [], ()))
        decisions, receipts, retries = repository.decisions([request], [], {"key": False})
        self.assertEqual(decisions, {"key": False})
        self.assertEqual(receipts[0].option_id, "deny")
        self.assertEqual(retries, ())
        with self.assertRaises(ValueError):
            repository.decisions([request], [], {"key": {}})
        confirmation = DecisionValueTests().confirmation()
        decision, receipts, _ = repository.decisions([confirmation], [], {"review": {}})
        self.assertEqual(decision, {"review": {"approved": True}})
        self.assertEqual(receipts[0].option_id, "approve")

    def test_stored_response_conflict_uses_strict_nested_comparison(self):
        request = approval_request("Tool").bind("graph", "key", decision_key="approved")
        response = request.respond("approve")
        for conflicting in ({"approved": 1}, {"approved": False}):
            with self.assertRaisesRegex(ValueError, "conflicts"):
                InteractionRepository().decisions([request], [response], {"key": conflicting})
        decisions, receipts, _ = InteractionRepository().decisions([request], [response], {})
        self.assertEqual(decisions, {"key": {"approved": True}})
        self.assertEqual(receipts, [])

    def test_uncertain_retry_still_requires_its_own_response(self):
        request = replace(approval_request("Retry", category="execution.retry_uncertain"),
                          binding={"key": "key", "target": "retry_nodes"})
        repository = InteractionRepository()
        self.assertEqual(repository.decisions([request], [], {}), ({}, [], ()))
        _, receipts, retries = repository.decisions([request], [], {}, retry_nodes=("key",))
        self.assertEqual(retries, ("key",))
        self.assertIs(receipts[0].decision(request), True)
        with self.assertRaisesRegex(ValueError, "denied"):
            repository.decisions([request], [request.respond("deny")], {}, retry_nodes=("key",))


class ToolDecisionTests(unittest.IsolatedAsyncioTestCase):
    def context(self, request, decision):
        return EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
            SimpleNamespace(id="r", input_message_id="m", metadata={"resume": {"decisions": {"key": decision}}}),
            (), checkpoint={"records": {"key": {"status": "waiting", "interaction": request.to_dict()}}})

    async def test_invalid_raw_graph_decision_fails_before_step_or_worker(self):
        request = approval_request("Tool", action={"tool": "act", "arguments": {}}).bind(
            "graph", "key", decision_key="approved")
        handler = AsyncMock()
        tool = Tool("act", "fixture", {"type": "object"}, handler)
        for value in ({"approved": 1}, {"approved": 0}, {"approved": "true"}, {}, None):
            with self.subTest(value=value), self.assertRaises(ToolInvocationError), \
                    patch("llm.components.tools.process.invoke_worker") as worker:
                async for _ in self.context(request, value).execute_tool(tool, {}, checkpoint_key="key", result={}):
                    self.fail("Invalid decision must fail before Step creation")
            worker.assert_not_called()
        handler.assert_not_awaited()

    async def test_ambiguous_option_and_boolean_number_arguments_cannot_reuse_approval(self):
        request = approval_request("Tool", action={"tool": "act", "arguments": {"flag": True}}).bind("loop", "key")
        ambiguous = replace(request, options=(*request.options, InteractionOption("other", "Other", effect="approve", value=True)))
        handler = AsyncMock()
        tool = Tool("act", "fixture", {"type": "object"}, handler)
        for current, arguments in ((ambiguous, {"flag": True}), (request, {"flag": 1})):
            with self.assertRaises(ToolInvocationError):
                async for _ in ToolExecutor().execute(tool, arguments, result={},
                        context=self.context(current, True), request_key="key"):
                    self.fail("Invalid binding must fail before Step creation")
        handler.assert_not_awaited()


class EngineDecisionTests(unittest.TestCase):
    def scope(self, owner='["agent"]', records=None, request=None):
        scope = EngineCheckpointScope(owner, "loop", {} if records is None else records)
        scope.accepted(scope.wrap(EngineEvent(EngineEventType.CHECKPOINT, metadata={
            "name": "loop", "operation": "initialize", "header": {"format": "loop-iterations-v1"}, "records": {}})))
        request = (request or approval_request("Review")).bind("loop", "tool:1:call")
        scope.accepted(scope.wrap(EngineEvent(EngineEventType.CHECKPOINT, metadata={
            "name": "loop", "operation": "record", "key": "tool:1:call",
            "value": {"status": "waiting", "interaction": request.to_dict()}})))
        return scope, request

    def context(self, decisions, retry=()):
        return EngineContext(SimpleNamespace(id="p"), SimpleNamespace(id="s"),
            SimpleNamespace(id="r", metadata={"resume": {"decisions": decisions, "retry_nodes": list(retry)}}), ())

    def test_nested_choice_id_restores_original_value_without_new_receipt(self):
        request = replace(approval_request("Review"), options=(
            InteractionOption("yes", "Yes", effect="approve", value=True),
            InteractionOption("no", "No", effect="deny", value=False)),
            expires_at="2000-01-01T00:00:00+00:00")
        scope, child = self.scope(request=request)
        for value in (True, False):
            context = self.context({scope.key("tool:1:call"): {"approved": value}})
            before = deepcopy((context.run.metadata, scope.records))
            with patch.object(InteractionRequest, "respond", side_effect=AssertionError("No new receipt")):
                nested = scope.context(context)
            self.assertIs(nested.run.metadata["resume"]["decisions"]["tool:1:call"], value)
            self.assertEqual(nested.checkpoint["records"]["tool:1:call"]["interaction"], child.to_dict())
            self.assertEqual(nested.checkpoint_scope, scope.owner)
            self.assertEqual((context.run.metadata, scope.records), before)

    def test_nested_scope_isolates_siblings_retry_keys_and_missing_decisions(self):
        scope, _ = self.scope()
        sibling, _ = self.scope('["other"]', scope.records)
        context = self.context({sibling.key("tool:1:call"): {"approved": False}},
                               (scope.key("tool:1:call"), sibling.key("tool:1:call")))
        nested = scope.context(context)
        self.assertEqual(nested.run.metadata["resume"]["decisions"], {})
        self.assertEqual(nested.run.metadata["resume"]["retry_nodes"], ["tool:1:call"])
        self.assertFalse(sibling.context(context).run.metadata["resume"]["decisions"]["tool:1:call"])

    def test_nested_scope_rejects_invalid_choice_or_mismatched_mapping(self):
        scope, _ = self.scope()
        key = scope.key("tool:1:call")
        for decision in ({"approved": 1}, {}, None, {"approved": True, "state": {}}):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                scope.context(self.context({key: decision}))
        for part in ("binding", "effect", "path"):
            broken = EngineCheckpointScope(scope.owner, scope.name, deepcopy(scope.records))
            if part == "binding":
                broken.records[key]["interaction"]["binding"]["key"] = "wrong"
            elif part == "effect":
                broken.records[key]["interaction"]["options"][0]["effect"] = "deny"
            else:
                broken.records["wrong"] = broken.records.pop(key)
            with self.subTest(part=part), self.assertRaisesRegex(ValueError, "checkpoint scope"):
                broken.context(self.context({"wrong" if part == "path" else key: {"approved": True}}))

    def test_loop_retains_boolean_shape_and_validates_selected_option(self):
        request = replace(approval_request("Review"), options=(
            InteractionOption("no", "No", effect="deny", value=False),)).bind("loop", "key")
        checkpoint = {"header": {"format": "loop-iterations-v1"}, "records": {
            "key": {"status": "waiting", "interaction": request.to_dict()}}}
        engine = LoopEngine()
        engine.validate_resume(checkpoint, decisions={"key": False})
        for decisions in ({}, {"key": True}, {"key": {"approved": False}}, {"key": 0}):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                engine.validate_resume(checkpoint, decisions=decisions)

    def test_graph_retains_state_schema_and_confirmation_shape(self):
        engine = GraphEngine(handlers={}).for_request({"workflow": "flow"})
        request = DecisionValueTests().confirmation()
        checkpoint = {"header": {"format": "workflow-nodes-v1", "binding": {
            "workflow_id": "flow", "revision": engine.revision}}, "records": {"review": {
                "status": "waiting", "node_type": "work", "resume_schema": request.input_schema,
                "interaction": request.to_dict()}}}
        engine.validate_resume(checkpoint, decisions={"review": {}})
        engine.validate_resume(checkpoint, decisions={"review": {"approved": True, "state": {"value": 2}}})
        for value, error in (({"approved": 1}, ValueError),
                             ({"state": {"value": "bad"}}, ValidationError),
                             ({"approved": False, "state": {}}, ValueError)):
            with self.subTest(value=value), self.assertRaises(error):
                engine.validate_resume(checkpoint, decisions={"review": value})
