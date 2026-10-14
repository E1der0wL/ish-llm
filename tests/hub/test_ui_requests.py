"""UI delivery remains ordered, explicit about failure and inert after close."""

from concurrent.futures import Future
from types import SimpleNamespace
import threading
import unittest

from hub.ui.requests import UIRequests


class UIRequestTests(unittest.TestCase):
    def setUp(self):
        self.active = self.connected = True
        self.queue, self.events = [], []
        self.future = Future()
        self.worker = SimpleNamespace(call=self.call)
        self.options = {}
        self.requests = UIRequests(lambda: self.worker,
            lambda callback, *args: self.queue.append((callback, args)),
            active=lambda: self.active, connected=lambda: self.connected, unavailable=lambda: "offline")

    def call(self, operation, *args, **kwargs):
        self.options = kwargs
        return self.future

    def drain(self):
        while self.queue:
            callback, args = self.queue.pop(0)
            callback(*args)

    def test_worker_observations_and_completion_are_delivered_in_order(self):
        self.requests.call("answer", completed=lambda result: self.events.append(("result", result.value)),
            acknowledged=lambda: self.events.append(("ack", None)),
            progress=lambda done, total: self.events.append(("progress", (done, total))))
        def work():
            self.options["acknowledged"]()
            self.options["progress"](1, 2)
            self.future.set_result(True)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertFalse(self.events)
        self.drain()
        self.assertEqual(self.events, [("ack", None), ("progress", (1, 2)), ("result", True)])

    def test_successful_none_and_failure_are_distinguishable(self):
        self.requests.call("save", completed=self.events.append)
        self.future.set_result(None)
        self.drain()
        self.assertIsNone(self.events[0].value)
        self.assertIsNone(self.events[0].error)
        self.future = Future()
        error = ValueError("conflict")
        self.requests.call("save", completed=self.events.append)
        self.future.set_exception(error)
        self.drain()
        self.assertIs(self.events[-1].error, error)

    def test_dispatch_failure_and_disconnected_catalog(self):
        self.connected = False
        self.requests.call("load", completed=self.events.append)
        self.assertEqual(self.events[-1].error, "offline")
        self.requests.call("settings", "catalog", allow_disconnected=True, completed=self.events.append)
        self.future.set_result({"catalog": True})
        self.drain()
        self.assertEqual(self.events[-1].value, {"catalog": True})
        self.worker.call = lambda *a, **k: (_ for _ in ()).throw(ValueError("stopped"))
        self.requests.call("settings", "catalog", allow_disconnected=True, completed=self.events.append)
        self.assertEqual(str(self.events[-1].error), "stopped")

    def test_queued_callbacks_do_not_touch_closed_ui(self):
        self.requests.call("answer", completed=self.events.append,
                           acknowledged=lambda: self.events.append("ack"))
        self.options["acknowledged"]()
        self.future.set_result(True)
        self.active = False
        self.drain()
        self.assertFalse(self.events)
