from tests.hub.test_mockup import minimize
from hub.ui.presentation import present
"""Real LLM persistence and runtime integration, with deterministic model output."""

import asyncio
from datetime import datetime
from functools import partial
import tempfile
import threading
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from tests.hub.test_mockup import SizedOutput

from llm.llm import LargeLanguageModel
from llm.components.tools import ToolComponent
from llm.engines import BaseEngine
from llm.engines.loop import LoopEngine


async def until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(0.02)


class ControlledEngine(BaseEngine):
    def configuration(self, config, registered_name=None, *, session_config=None):
        from llm.core.configuration import engine_configuration
        return engine_configuration(config, registered_name, session_config=session_config,
                                    schema=self.configuration_schema())

    def configuration_schema(self):
        from llm.core.schema import object_schema, implementation_schema
        from llm.providers.schema import completion_schema
        return implementation_schema(config=object_schema({"completion": completion_schema()}))

    def __init__(self, gate):
        super().__init__("Controlled")
        self.gate = gate

    async def run(self, context):
        text = context.messages[-1].content
        yield "**reply**: "
        if text.startswith("hold"):
            while not self.gate.is_set():
                await asyncio.sleep(0.02)
        yield text


def controlled_backend(config, gate):
    return LargeLanguageModel(config.workspace, components=[], engines={"loop": ControlledEngine(gate)})


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sessions_execute_concurrently_without_switch_interrupt(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, "loop", "test/model", auto_title=False),
                                 partial(controlled_backend, gate=gate))
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                first = runtime.selected_id
                await runtime.submit(first, "hold first")
                second = await runtime.new_session()
                await runtime.submit(second, "parallel")
                await runtime.sessions[second].run.wait_idle()
                self.assertEqual((await runtime.sessions[second].aconversation())[-1].content,
                                 "**reply**: parallel")
                self.assertIsNotNone((await runtime.sessions[first].run.astatus()).active_run_id)
                await runtime.select(first)
                gate.set()
                await runtime.sessions[first].run.wait_idle()
                self.assertEqual((await runtime.snapshot()).messages[-1].text, "**reply**: hold first")
            finally:
                await runtime.close()

    async def test_real_loop_project_history_reopen_and_model_settings(self):
        calls = []
        def completion(**kwargs):
            calls.append(kwargs)
            for text in ("Hello ", "**Hub**"):
                yield {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        def factory(config):
            return LargeLanguageModel(config.workspace, components=[ToolComponent()],
                                      engines={"loop": LoopEngine(completion_fn=completion)})
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, "loop", "openai/test", auto_title=False), factory)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                identifier = runtime.selected_id
                await runtime.submit(identifier, "hello")
                await runtime.sessions[identifier].run.wait_idle()
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.messages[-1].text, "Hello **Hub**")
                run = await (await runtime.sessions[identifier].run.alist())[0].aget_data()
                elapsed = (datetime.fromisoformat(run.ended_at) - datetime.fromisoformat(run.started_at)).total_seconds()
                self.assertEqual(snapshot.messages[-1].elapsed_seconds, elapsed)
                self.assertEqual(snapshot.messages[-1].time, "")
                self.assertEqual(calls[0]["model"], "openai/test")
                self.assertIn("완료", present(snapshot, runtime.t).detail)
                project_id = snapshot.project_id
            finally:
                await runtime.close()
            reopened = HubRuntime(HubConfig(directory, "loop", "openai/do-not-overwrite",
                                            project_id=project_id, session_id=identifier, auto_title=False), factory)
            try:
                await reopened.start()
                snapshot = await reopened.snapshot()
                self.assertEqual(snapshot.model, "openai/test")
                self.assertEqual(snapshot.messages[-1].text, "Hello **Hub**")
                self.assertEqual(snapshot.messages[-1].elapsed_seconds, elapsed)
            finally:
                await reopened.close()

    async def test_shutdown_preserves_queue_and_reopen_recovers_it(self):
        gate = threading.Event()
        factory = partial(controlled_backend, gate=gate)
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            runtime = HubRuntime(config, factory)
            await runtime.start()
            if not runtime.sessions:
                await runtime.new_session()
            identifier = runtime.selected_id
            await runtime.submit(identifier, "hold first")
            async with asyncio.timeout(10):
                while not any(m.role == "assistant" for m in await runtime.sessions[identifier].aconversation()):
                    await asyncio.sleep(0.02)
            await runtime.submit(identifier, "second")
            await runtime.close()
            gate.set()
            reopened = HubRuntime(config, factory)
            try:
                await reopened.start()
                await reopened.sessions[identifier].run.wait_idle()
                messages = await reopened.sessions[identifier].aconversation()
                self.assertTrue(any(str(m.status) == "interrupted" for m in messages))
                self.assertEqual(messages[-1].content, "**reply**: second")
                self.assertEqual(sum(m.content == "hold first" for m in messages), 1)
            finally:
                await reopened.close()


class LiveUITests(unittest.IsolatedAsyncioTestCase):
    async def test_live_stream_queue_interrupt_hide_and_new_session(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            factory = partial(BackendWorker, backend_factory=partial(controlled_backend, gate=gate))
            app, controller = create_application(config, input=pipe, output=SizedOutput(), worker_factory=factory)
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                first_id = view.sessions[view.selected].id
                pipe.send_text("hold first\r")
                await until(lambda: view.composer.text == "")
                await until(lambda: any("reply" in m.text for m in view.transcript.control.messages))
                pipe.send_text("second\r")
                await until(lambda: view._dialog is not None)
                pipe.send_text("\r")  # Default: follow-up request.
                await until(lambda: view.composer.text == "")
                await until(lambda: "예약 1" in view.sessions[view.selected].status)
                await minimize(pipe, view)
                await until(lambda: not view.visible)
                self.assertTrue(controller.worker._thread.is_alive())
                pipe.send_text("\x11\x18")  # return; Ctrl+X interrupts only the active Run
                await until(lambda: any(m.text == "**reply**: second" for m in view.transcript.control.messages))
                self.assertTrue(any(m.status == "interrupted" for m in view.transcript.control.messages))
                pipe.send_text("\x1bOS")  # F4 new Session
                await until(lambda: view._dialog is not None)
                pipe.send_text("Other session\r")
                await until(lambda: view.sessions[view.selected].id != first_id)
                self.assertEqual(len(view.sessions), 2)
                pipe.send_text("other\r")
                await until(lambda: any(m.text == "**reply**: other" for m in view.transcript.control.messages))
                pipe.send_text("\x1bOQ")  # F2 returns to the first Session
                await until(lambda: view.sessions[view.selected].id == first_id)
                await until(lambda: any(m.text == "**reply**: second" for m in view.transcript.control.messages))
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            self.assertFalse(controller.worker._thread.is_alive())

    async def test_admission_error_preserves_draft(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, "missing", "test/model", auto_title=False)
            factory = partial(BackendWorker, backend_factory=partial(controlled_backend, gate=threading.Event()))
            app, controller = create_application(config, input=pipe, output=SizedOutput(), worker_factory=factory)
            task = asyncio.create_task(app.run_async(pre_run=lambda: controller.view.show(app)))
            try:
                await until(lambda: controller.view.connected)
                if controller.view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not controller.view.no_sessions and controller.view._dialog is None)
                # Snapshots select an available engine; force an invalid choice at admission.
                controller.view.composer.text = "keep this"
                controller.view.engine = "missing"
                controller.submit(controller.view.sessions[controller.view.selected].id, "keep this")
                await until(lambda: controller.view.notice.startswith("오류:"))
                self.assertEqual(controller.view.composer.text, "keep this")
                self.assertFalse(controller._submitting)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)


class HostLifecycleTests(unittest.TestCase):
    def test_backend_survives_prompt_cycles_and_host_run_finally_closes_it(self):
        from tests.hub.test_ish_integration import Prompt
        from hub.hub import install
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = None

            async def interact():
                view = installation.view
                first = asyncio.create_task(prompt.prompt_async())
                await until(lambda: prompt.app.is_running)
                pipe.send_text("\x11")
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("hold request\r")
                await until(lambda: any("reply" in m.text for m in view.transcript.control.messages))
                await minimize(pipe, view)
                await until(lambda: not view.visible)
                prompt.app.exit(result="pwd")
                await first
                self.assertTrue(installation.controller.worker._thread.is_alive())
                gate.set()
                await asyncio.sleep(0.2)
                second = asyncio.create_task(prompt.prompt_async())
                await until(lambda: prompt.app.is_running)
                pipe.send_text("\x11")
                await until(lambda: any(m.text == "**reply**: hold request"
                                       for m in view.transcript.control.messages))
                prompt.app.exit(result="")
                await second

            def host_run():
                asyncio.run(interact())
                raise SystemExit(0)

            prompt.run = host_run
            installation = install(prompt, config=HubConfig(directory, "loop", "test/model", auto_title=False))
            installation.controller.worker_factory = partial(
                BackendWorker, backend_factory=partial(controlled_backend, gate=gate))
            with self.assertRaises(SystemExit):
                prompt.run()
            self.assertTrue(installation._closed)
            self.assertFalse(installation.controller.worker._thread.is_alive())
