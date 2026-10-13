"""Request-scoped workflow selection through the real backend and PTK input."""

import asyncio
import tempfile
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from prompt_toolkit.application.current import set_app
from hub.backend.runtime import HubConfig, HubRuntime
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.engines.graph import GraphEngine
from tests.hub.test_live import until
from tests.hub.support import SizedOutput
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

    async def test_removed_engine_key_and_direct_command_keep_request_options(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.engines = ("loop", "graph")
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                pipe.send_text("\x05")
                await asyncio.sleep(.05)
                self.assertIsNone(view._dialog)
                self.assertFalse(view.navigation_keys.get_bindings_for_keys(("c-e",)))
                with set_app(app):
                    view.set_execution("graph", {"workflow": "alpha"})
                    view.selected = 1
                    self.assertEqual(view.engine_options, {})
                    view.selected = 0
                    self.assertEqual(view.engine_options, {"workflow": "alpha"})
                pipe.send_text("/engine loop\r")
                await until(lambda: view.engine == "loop")
                self.assertEqual(view.engine_options, {})
                pipe.send_text("/engine graph\r")
                await until(lambda: view.engine == "graph")
                self.assertEqual(view.engine_options, {"workflow": "alpha"})
                pipe.send_text("/engine\r")
                await until(lambda: "loop, graph" in view.notice)
                self.assertIsNone(view._dialog)
            finally:
                app.exit()
                await task
                view.output_renderers.close()
