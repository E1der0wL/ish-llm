"""Stored Agent selection through the real backend and PTK input."""

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
    return HubConfig(directory, agent_id="alpha", agent_definition={"engine": "graph", "engine_options": {"workflow": "alpha"}},
        model="test/model", auto_title=False, language="en",
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
                options = {"workflow": "beta"}
                await runtime.project.agents.acreate({"engine": "graph", "engine_options": options}, identifier="beta")
                session = runtime.sessions[runtime.selected_id]
                await session.run.shutdown()
                await session.asave(agent_ids=["alpha", "beta"])
                catalog = await runtime.execution_catalog(session.id)
                self.assertEqual(catalog["agents"]["alpha"]["engine"], "graph")
                self.assertEqual(set(catalog["agents"]), {"alpha", "beta"})
                with self.assertRaises(FileNotFoundError):
                    await runtime.submit(session.id, "missing Agent", "graph")
                self.assertEqual(await session.aconversation(), [])
                await runtime.submit(session.id, "first", "alpha")
                await asyncio.wait_for(started.wait(), 10)
                second = await runtime.submit(session.id, "second", "beta")
                options["workflow"] = "alpha"
                queued = next(m for m in await session.aconversation() if m.id == second)
                self.assertEqual(queued.metadata["admission"]["bindings"]["beta"]["definition"]["engine_options"], {"workflow": "beta"})
                gate.set()
                await session.run.wait_idle()
                runs = [await run.aget_data() for run in await session.run.alist()]
                self.assertEqual([r.metadata["agent_binding"]["definition"]["engine_options"]["workflow"] for r in runs], ["alpha", "beta"])
                self.assertTrue(all(str(r.status) == "completed" for r in runs))
                self.assertIsNone(runtime.backend.engines.get("graph").workflow)
            finally:
                gate.set()
                await runtime.close()

    async def test_removed_engine_key_and_direct_command_keep_request_options(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.agents = ("assistant", "alpha")
            view.agent_engines = {"assistant": "loop", "alpha": "graph"}
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                pipe.send_text("\x05")
                await asyncio.sleep(.05)
                self.assertIsNone(view._dialog)
                # Selection replacement uses Keys.Any, not a restored engine shortcut.
                self.assertFalse(any(binding.keys == ("c-e",) for binding in
                    view.navigation_keys.get_bindings_for_keys(("c-e",))))
                with set_app(app):
                    view.choose_agent("alpha")
                    view.selected = 1
                    self.assertEqual(view.agent_id, "assistant")
                    view.selected = 0
                    self.assertEqual(view.agent_id, "alpha")
                pipe.send_text("/agent assistant\r")
                await until(lambda: view.engine == "loop")
                self.assertEqual(view.agent_id, "assistant")
                pipe.send_text("/agent alpha\r")
                await until(lambda: view.engine == "graph")
                self.assertEqual(view.agent_id, "alpha")
                pipe.send_text("/agent\r")
                await until(lambda: "assistant, alpha" in view.notice)
                self.assertIsNone(view._dialog)
            finally:
                app.exit()
                await task
                view.close()
