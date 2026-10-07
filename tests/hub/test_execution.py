"""Request-scoped workflow selection through the real backend and PTK input."""

import asyncio
import tempfile
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.widget.execution import ExecutionChooser
from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.engines.graph import GraphEngine
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput
from tests.hub.test_overlays import screen_text


def config(directory, handlers=None):
    return HubConfig(directory, "graph", "test/model", auto_title=False, language="en",
        engine_factories={"graph": lambda: GraphEngine(handlers=handlers or {})},
        component_factories=(WorkflowComponent,))


async def prepare(runtime):
    workflows = await runtime.project.components.aget("workflows")
    for name in ("alpha", "beta"):
        await workflows.acreate(WorkflowGraph(entry="finish", metadata={"description": name + " description"})
                                .node("finish", "end").to_dict(), identifier=name)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_workflows_are_independent_and_validation_preserves_history(self):
        gate, started = asyncio.Event(), asyncio.Event()
        async def hold(context):
            started.set()
            await gate.wait()
            return {}
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(config(directory, {"hold": hold}))
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                workflows = await runtime.project.components.aget("workflows")
                await workflows.acreate(WorkflowGraph(entry="hold").node("hold", "hold")
                    .node("finish", "end").connect("hold", "finish").to_dict(), identifier="alpha")
                await workflows.acreate(WorkflowGraph(entry="finish").node("finish", "end").to_dict(), identifier="beta")
                session = runtime.sessions[runtime.selected_id]
                catalog = await runtime.execution_catalog(session.id)
                self.assertEqual(catalog["engines"]["graph"]["kind"], "graph")
                self.assertEqual(set(catalog["workflows"]), {"alpha", "beta"})
                with self.assertRaises(ValueError):
                    await runtime.submit(session.id, "missing workflow", "graph")
                self.assertEqual(await session.aconversation(), [])
                await runtime.submit(session.id, "first", "graph", {"workflow": "alpha"})
                await asyncio.wait_for(started.wait(), 10)
                options = {"workflow": "beta"}
                second = await runtime.submit(session.id, "second", "graph", options)
                options["workflow"] = "alpha"
                queued = next(m for m in await session.aconversation() if m.id == second)
                self.assertEqual(queued.metadata["engine_options"], {"workflow": "beta"})
                gate.set()
                await session.run.wait_idle()
                runs = [await run.aget_data() for run in await session.run.alist()]
                self.assertEqual([r.metadata["engine_options"]["workflow"] for r in runs], ["alpha", "beta"])
                self.assertTrue(all(str(r.status) == "completed" for r in runs))
                self.assertIsNone(runtime.backend.engines.get("graph").workflow)
            finally:
                gate.set()
                await runtime.close()

    async def test_live_dialog_select_cancel_execute_and_switch_engine(self):
        # Seed only definitions; the live controller opens a fresh backend instance.
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            settings = config(directory)
            runtime = HubRuntime(settings)
            await runtime.start()
            if not runtime.sessions:
                await runtime.new_session()
            await prepare(runtime)
            await runtime.close()
            output = SizedOutput(80, 24)
            app, controller = create_application(settings, input=pipe, output=output)
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            rendered = lambda: screen_text(app, output.columns, output.rows)
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("\x05")
                await until(lambda: "alpha description" in rendered())
                self.assertIn("Workflow", rendered())
                output.columns, output.rows = 60, 14
                app._on_resize()
                await until(lambda: "Workflow" in rendered() and "Confirm" in rendered())
                self.assertNotIn("Window too small", rendered())
                output.columns, output.rows = 80, 24
                app._on_resize()
                await until(lambda: "alpha description" in rendered())
                pipe.send_text("\t\x1b[B\t\r")
                await until(lambda: view._dialog is None)
                self.assertEqual(view.engine_options, {"workflow": "beta"})
                self.assertEqual(view.execution_label, "graph · beta")
                pipe.send_text("\x05")
                await until(lambda: "beta description" in rendered())
                pipe.send_text("\t\x1b[A\x1b")
                await until(lambda: view._dialog is None)
                self.assertEqual(view.engine_options, {"workflow": "beta"})
                pipe.send_text("run beta\r")
                await until(lambda: any(m.role == "user" and m.text == "run beta"
                                        for m in view.transcript.control.messages))
                def persisted():
                    async def read():
                        session = controller.worker.runtime.sessions[view.sessions[view.selected].id]
                        await session.run.wait_idle()
                        return await (await session.run.alist())[-1].aget_data()
                    return asyncio.run_coroutine_threadsafe(read(), controller.worker.loop)
                run = await asyncio.wrap_future(persisted())
                self.assertEqual(run.metadata["engine_options"], {"workflow": "beta"})
                self.assertEqual(str(run.status), "completed")
                pipe.send_text("/engine loop\r")
                await until(lambda: view.engine == "loop")
                self.assertEqual(view.engine_options, {})
                pipe.send_text("\x05")
                await until(lambda: "additional request options" in rendered())
                self.assertNotIn("alpha description", rendered())
                pipe.send_text("\x1b[B")
                await until(lambda: "beta description" in rendered())
            finally:
                app.exit()
                await task
                controller.close()

    async def test_empty_workflows_cannot_apply_and_options_are_session_scoped(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async())
            try:
                await until(lambda: app.is_running)
                pipe.send_text("\x11")
                await until(lambda: view.visible)
                chooser = ExecutionChooser(view, {"engines": {"graph": {"kind": "graph"}}, "workflows": {}})
                chooser.open()
                await until(lambda: view.t("execution_no_workflows") in screen_text(app, 132, 40))
                pipe.send_text("\t\t\r")
                await until(lambda: bool(chooser.error))
                self.assertIsNotNone(view._dialog)
                self.assertEqual(view.engine, "loop")
                self.assertTrue(chooser.error)
                view.close_dialog()
                view.set_execution("graph", {"workflow": "alpha"})
                view.selected = 1
                self.assertEqual(view.engine_options, {})
                view.selected = 0
                self.assertEqual(view.engine_options, {"workflow": "alpha"})
            finally:
                app.exit()
                await task
