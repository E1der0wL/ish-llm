"""Independent wire and missing-evidence checks for instruction channel Owners."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from llm.core.steering import SteeringMode, SteeringTarget, SteeringRoute, InstructionDataError
from llm.core.steering_channel import SteeringChannel
from llm.core.steering_events import SteeringEventData, SteeringNotification


class SteeringValueTests(unittest.TestCase):
    def run_value(self, *, resume=False):
        return SimpleNamespace(id="run", engine="loop", metadata={"resume": {}} if resume else {})

    def test_initial_channel_and_suspension_keep_exact_wire_and_no_new_authority(self):
        run = self.run_value()
        channel = SteeringChannel.initial(run, SteeringMode.CONSUME, "input")
        expected = {"accepting": True, "mode": "consume", "input_message_id": "input",
            "targets": {"root": {"id": "root", "run_id": "run", "engine": "loop", "mode": "consume",
                "scope": None, "node_id": None, "node_path": None, "accepting": True, "reason": None}},
            "root_opened": False, "routes": {}, "bindings": {}, "routes_declared": False}
        self.assertEqual(channel.to_dict(), expected)
        channel.store(run)
        expected["targets"]["root"]["engine"] = "changed"
        self.assertEqual(SteeringChannel.require(run).targets["root"].engine, "loop")
        run = self.run_value(resume=True)
        SteeringChannel.suspended(run).store(run)
        self.assertEqual(run.metadata["steering"], {"accepting": False})
        self.assertEqual(SteeringChannel.require(run).targets, {})
        self.assertFalse(SteeringChannel.require(run).accepting)
        run.metadata.pop("resume")
        with self.assertRaises(InstructionDataError):
            SteeringChannel.require(run).targets

    def test_missing_targets_or_bad_route_witness_is_rejected_without_repair(self):
        run = self.run_value()
        for value in ({}, {"accepting": True}, {"targets": [], "routes": {}, "bindings": {}},
                      {"targets": {}, "routes": {}, "bindings": {"scope": "missing"}}):
            run.metadata["steering"] = deepcopy(value)
            with self.subTest(value=value), self.assertRaises(InstructionDataError):
                SteeringChannel.require(run).targets
            self.assertEqual(run.metadata["steering"], value)

    def test_route_binding_cannot_be_repeated_or_cross_execution_ownership(self):
        run = self.run_value()
        channel = SteeringChannel.initial(run, SteeringMode.FORWARD, "input")
        route = SteeringRoute(("workflow",), "worker", "loop")
        channel.declare_routes((route,))
        channel.bind_route(route, "scope")
        original = channel.to_dict()
        with self.assertRaises(ValueError):
            channel.bind_route(route, "scope")
        with self.assertRaises(ValueError):
            channel.declare_routes((route,))
        with self.assertRaises(ValueError):
            channel.register("worker", SteeringTarget("worker", "other-run", "loop", SteeringMode.CONSUME,
                                                       scope="scope", accepting=True))
        self.assertEqual(channel.to_dict(), original)

    def test_event_scope_detaches_payload_and_preserves_missing_null_explicit_values(self):
        options = {"checkpoint": "loop", "boundary": "input-1", "final": False,
                   "allow_continue": True, "reason": None, "extension": {"tag": "explicit"}}
        event = SteeringEventData.request("select", "root", "request", options)
        options["extension"]["tag"] = "changed"
        original = event.to_dict()
        changed = event.scoped("graph", "nested-input", {"engine_scope": "scope"}).to_dict()
        self.assertEqual(changed, {**original, "checkpoint": "graph", "boundary": "nested-input",
                                  "envelope": {"engine_scope": "scope"}})
        self.assertEqual(event.to_dict(), original)
        self.assertIsNone(event.reason)
        self.assertNotIn("details", original)
        self.assertEqual(original["extension"], {"tag": "explicit"})
        with self.assertRaises(KeyError):
            SteeringEventData({"operation": "select"}).request_id

    def test_opening_and_partial_notification_do_not_fill_omitted_wire_fields(self):
        target = SteeringTarget("root", "run", "loop", SteeringMode.CONSUME, accepting=True)
        self.assertEqual(target.registration(), {"id": "root", "run_id": "run", "engine": "loop",
            "mode": "consume", "scope": None, "node_id": None, "node_path": None, "accepting": True})
        self.assertEqual(SteeringNotification(instructions=()).to_dict(), {"instructions": []})
        self.assertEqual(SteeringNotification(targets=(target,)).to_dict(), {"targets": [target.to_dict()]})
        self.assertEqual(SteeringNotification().to_dict(), {})


if __name__ == "__main__":
    unittest.main()
