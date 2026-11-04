"""Official plugin API isolation and backend contract integration (no providers)."""

import asyncio
import json
import tempfile
import unittest

from hub.api import PluginClient, PluginDefinition, PluginContext
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.plugins.requests import PluginRequests
from hub.backend.plugins.catalog import list_agents, manage_agents
from hub.backend.project.management_values import AgentLocation, EditorRecord, ManagementAction
from hub.config.environment import ShellEnvironment
from llm.engines.base import BaseEngine
from llm.core.agent_views import AgentSnapshot


class PluginAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def action(context):
            yield "first "
            self.entered.set()
            await self.release.wait()
            yield "last"

        self.runtime = HubRuntime(HubConfig(self.root.name, auto_title=False, prepare_provider=False,
            agent_definition={"engine": "host"}, engine_factories={"host": lambda: BaseEngine(action=action)}))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        self.api = self.runtime.plugins
        self.plugin = "sample.shell"
        self.definition = PluginDefinition("Shell helper", {"helper": {"engine": "host"}})

    async def registered(self):
        result = await self.api.register(self.plugin, self.definition)
        lane = await self.api.open_session(self.plugin, "suggestions", ["helper"])
        return result, lane

    async def test_idempotent_registration_preserves_user_configuration_and_home(self):
        home = self.runtime.project.id
        result, lane = await self.registered()
        project = await self.api.projects.project(self.plugin)
        snapshot = AgentSnapshot.read(await project.agents.asnapshot("helper"))
        metadata = snapshot.definition["metadata"]
        self.assertEqual(metadata["created_at"], metadata["updated_at"])
        self.assertTrue(metadata["created_at"])
        await project.agents.arevise("helper", {"engine": "host", "description": "User setting"},
                                     expected_revision=snapshot.revision)
        again = await self.api.register(self.plugin, self.definition)
        self.assertEqual(result["project_id"], again["project_id"])
        self.assertFalse(again["created"])
        self.assertEqual((await project.agents.aload("helper"))["description"], "User setting")
        self.assertEqual(lane, await self.api.open_session(self.plugin, "suggestions", ["helper"]))
        self.assertEqual(self.runtime.project.id, home)
        self.assertNotIn(lane["session_id"], self.runtime.sessions)
        self.assertNotIn(result["project_id"], [r["id"] for r in await self.runtime.settings_service.list_projects()])
        with self.assertRaisesRegex(ValueError, "Plugin Projects"):
            await self.runtime.activate_project(project)

    async def test_durable_public_stream_and_reconnect_do_not_reexecute(self):
        _, lane = await self.registered()
        reference = await self.api.submit(self.plugin, lane["session_id"], "hello", ["helper"], None, "unique-input")
        duplicate = await self.api.submit(self.plugin, lane["session_id"], "hello", ["helper"], None, "unique-input")
        self.assertEqual(reference, duplicate)
        await asyncio.wait_for(self.entered.wait(), 15)
        self.release.set()
        session = await self.api.projects.session(self.plugin, lane["session_id"])
        await session.run.wait_idle()
        batch = await self.api.poll(self.plugin, reference, {}, -1, False)
        self.assertTrue(batch["terminal"])
        self.assertEqual(batch["runs"][0]["status"], "completed")
        self.assertTrue(batch["outputs"])
        self.assertTrue(all(e["value"]["visibility"] == "user" for e in batch["outputs"]))
        new = PluginRequests(self.runtime.backend)
        with self.assertRaisesRegex(RuntimeError, "restarted"):
            await new.poll(self.plugin, reference, {}, -1, False)
        reopened = await new.reconnect(self.plugin, lane["session_id"], reference["request_id"])
        empty = await new.poll(self.plugin, reopened, batch["cursors"], -1, False)
        self.assertEqual(empty["outputs"], [])
        self.assertEqual(len(await session.run.alist()), 1)

    async def test_plugin_scope_rejects_other_sessions_and_runs(self):
        _, lane = await self.registered()
        await self.api.register("other", self.definition)
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.api.submit("other", lane["session_id"], "bad", ["helper"], None, "bad")
        reference = await self.api.submit(self.plugin, lane["session_id"], "hello", ["helper"], None, "ok")
        await asyncio.wait_for(self.entered.wait(), 15)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            await self.api.interrupt(self.plugin, reference, "foreign")
        self.release.set()

    async def test_bad_template_does_not_leave_visible_or_partially_registered_project(self):
        with self.assertRaises(Exception):
            await self.api.register(self.plugin, PluginDefinition("Bad", {"helper": {"engine": "missing"}}))
        self.assertEqual(await self.api.catalog(), [])

    async def test_stream_arrives_before_engine_finishes_and_queue_cancellation_is_scoped(self):
        _, lane = await self.registered()
        reference = await self.api.submit(self.plugin, lane["session_id"], "first", ["helper"], None, "first")
        await asyncio.wait_for(self.entered.wait(), 15)
        positions, revision, delivered = {}, -1, []
        async with asyncio.timeout(15):
            while not delivered:
                batch = await self.api.poll(self.plugin, reference, positions, revision, False)
                delivered.extend(batch["outputs"])
                positions, revision = batch["cursors"], batch["revision"]
        self.assertFalse(batch["terminal"])
        self.assertIn("first", "".join(e["value"]["text"] for e in delivered))
        queued = await self.api.submit(self.plugin, lane["session_id"], "queued", ["helper"], None, "queued")
        self.assertTrue(await self.api.cancel(self.plugin, queued))
        cancelled = await self.api.poll(self.plugin, queued, {}, -1, False)
        self.assertTrue(cancelled["terminal"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.release.set()

    async def test_registration_adds_new_agents_but_does_not_replace_project_policies(self):
        result, _ = await self.registered()
        changed = PluginDefinition("Changed title", {"helper": {"engine": "host"}, "new": {"engine": "host"}},
                                   config={"data": {"new": True}})
        await self.api.register(self.plugin, changed)
        project = await self.api.projects.project(self.plugin)
        self.assertEqual((await project.aget_data()).title, "Shell helper")
        self.assertNotIn("new", (await project.aget_data()).config.data)
        self.assertEqual(set(await project.agents.alist()), {"helper", "new"})

    async def test_unified_manager_keeps_same_named_agents_and_revisioned_edits_in_owner(self):
        result = await self.api.register(self.plugin, self.definition)
        home = self.runtime.project
        await home.agents.acreate({"engine": "host", "description": "Home"}, identifier="helper")
        rows = await list_agents(self.runtime.backend, home.id)
        local = next(row for row in rows if row["name"] == "helper")
        plugin = next(row for row in rows if row.get(AgentLocation.PLUGIN) == "Shell helper")
        self.assertNotEqual(local["name"], plugin["name"])
        self.assertEqual(plugin[AgentLocation.AGENT], "helper")
        owner = plugin[AgentLocation.PROJECT]
        self.assertEqual(owner, result["project_id"])
        record = await manage_agents(self.runtime.backend, home.id, owner, ManagementAction.READ, "helper", None)
        await manage_agents(self.runtime.backend, home.id, owner, ManagementAction.SAVE,
            EditorRecord.change("helper", json.dumps({"engine": "host", "description": "Plugin edited"})),
            record["version"])
        self.assertEqual((await home.agents.aload("helper"))["description"], "Home")
        with self.assertRaises(Exception):
            await manage_agents(self.runtime.backend, home.id, owner, ManagementAction.DELETE,
                                "helper", record["version"])
        current = await manage_agents(self.runtime.backend, home.id, owner, ManagementAction.READ, "helper", None)
        rows = await manage_agents(self.runtime.backend, home.id, owner, ManagementAction.DELETE,
                                  "helper", current["version"])
        self.assertIn("helper", [row["name"] for row in rows])
        self.assertNotIn(plugin["name"], [row["name"] for row in rows])
        self.assertEqual(self.runtime.project.id, home.id)

    async def test_unified_manager_excludes_other_home_projects_and_rejects_their_targets(self):
        await self.registered()
        home = self.runtime.project
        other = await self.runtime.backend.projects.acreate("Other", components=[], conversation_storage="file")
        await other.agents.acreate({"engine": "host"}, identifier="not-in-catalog")
        rows = await list_agents(self.runtime.backend, home.id)
        self.assertNotIn("not-in-catalog", [row["name"] for row in rows])
        with self.assertRaisesRegex(ValueError, "target must be"):
            await manage_agents(self.runtime.backend, home.id, other.id, ManagementAction.READ,
                                "not-in-catalog", None)


class PluginApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_question_stays_out_of_home_and_requires_explicit_resume(self):
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Which format?"}', name='ask_user')], finish='tool_calls')],
            [chunk('Markdown selected', finish='stop')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False, engine_factories={
                'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            api = runtime.plugins
            await api.register('questions', PluginDefinition('Questions', {'helper': {
                'engine': 'loop', 'completion': {'model': 'test/model'}, 'tools': ['ask_user']}},
                components=('builtin_tools',), config={'parameters': {'components': {
                    'builtin_tools': {'config': {'enabled': ['ask_user']}}}}}))
            lane = await api.open_session('questions', 'main', ['helper'])
            reference = await api.submit('questions', lane['session_id'], 'Create a report', ['helper'], None, 'question')
            session = await api.projects.session('questions', lane['session_id'])
            await session.run.wait_idle()
            batch = await api.poll('questions', reference, {}, -1, False)
            from llm.core.interactions import InteractionRequest
            run, = batch['runs']
            self.assertEqual(run['status'], 'paused')
            question = InteractionRequest.from_dict(run['interactions'][0]['request'])
            self.assertFalse(runtime.notifications.items())
            await api.respond('questions', reference, run['id'], question.respond('reply', value='Markdown').to_dict(), 'answer')
            self.assertEqual(len(provider.requests), 1)
            plan = await api.resume_plan('questions', reference, run['id'])
            self.assertTrue(plan['can_resume'], plan)
            resumed = await api.resume('questions', reference, run['id'], {'expected_revision': plan['revision']})
            await session.run.wait_idle()
            final = await api.poll('questions', resumed, {}, -1, False)
            self.assertEqual(len(final['runs']), 1)
            self.assertEqual(final['runs'][0]['status'], 'completed', final)
            self.assertEqual(len(await session.run.alist()), 2)
            self.assertIn('Markdown', str(provider.requests[-1]['messages']))

    async def test_durable_approval_requires_explicit_response_then_resume(self):
        from pathlib import Path
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from llm.core.interactions import InteractionRequest
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"path":"result.txt","content":"approved"}', name="file_create")], finish="tool_calls")],
            [chunk("Saved.", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(Path(root) / "storage", file_root=root, auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            api = runtime.plugins
            await api.register("files", PluginDefinition("Files", {"helper": {
                "engine": "loop", "completion": {"model": "test/model"}, "tools": ["file_create"]}},
                components=("builtin_tools",), config={"parameters": {"components": {
                    "builtin_tools": {"config": {"enabled": ["file_create"]}}}}}))
            lane = await api.open_session("files", "main", ["helper"])
            reference = await api.submit("files", lane["session_id"], "create result.txt", ["helper"],
                PluginContext(ShellEnvironment.from_dict({"cwd": root, "env": {}})), "create")
            session = await api.projects.session("files", lane["session_id"])
            await session.run.wait_idle()
            batch = await api.poll("files", reference, {}, -1, False)
            run, = batch["runs"]
            self.assertEqual(run["status"], "paused", run["error"])
            question = InteractionRequest.from_dict(run["interactions"][0]["request"])
            self.assertFalse(Path(root, "result.txt").exists())
            await api.respond("files", reference, run["id"], question.respond("approve").to_dict(), "approval")
            self.assertFalse(Path(root, "result.txt").exists())
            self.assertEqual(len(await session.run.alist()), 1)
            plan = await api.resume_plan("files", reference, run["id"])
            self.assertTrue(plan["can_resume"], plan)
            resumed = await api.resume("files", reference, run["id"], {"expected_revision": plan["revision"]})
            await session.run.wait_idle()
            final = await api.poll("files", resumed, {}, -1, False)
            self.assertEqual(final["runs"][0]["status"], "completed", final)
            self.assertEqual(Path(root, "result.txt").read_text(), "approved")
            self.assertEqual(len(provider.requests), 2)


class PluginOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_agent_uses_common_output_transport(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph import GraphEngine
        async def work(node):
            return {'result': 'graph answer'}
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False, prepare_provider=False,
                engine_factories={'graph': lambda: GraphEngine(handlers={'work': work})}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            api = runtime.plugins
            await api.register('graph-plugin', PluginDefinition('Graph', {'helper': {
                'engine': 'graph', 'engine_options': {'workflow': 'flow'}}}, components=('workflows',)))
            project = await api.projects.project('graph-plugin')
            flow = WorkflowGraph(entry='work').node('work', 'work').node('end', 'end').connect('work', 'end')
            await project.components.workflows.acreate(flow.to_dict(), identifier='flow')
            lane = await api.open_session('graph-plugin', 'main', ['helper'])
            reference = await api.submit('graph-plugin', lane['session_id'], 'run', ['helper'], None, 'graph')
            session = await api.projects.session('graph-plugin', lane['session_id'])
            await session.run.wait_idle()
            batch = await api.poll('graph-plugin', reference, {}, -1, True)
            self.assertTrue(batch['terminal'])
            self.assertEqual(batch['runs'][0]['status'], 'completed', batch)
            self.assertTrue(batch['outputs'])
            self.assertTrue(any(row['value'].get('data') for row in batch['outputs']))

    async def test_common_events_keep_replace_internal_final_and_page_cursor(self):
        from uuid import uuid4
        from llm.core.results import EngineDelta, EngineOutput
        from llm.engines.events import EngineEvent, EngineEventType
        from hub.backend.plugins.stream import PluginStream
        class Outputs(BaseEngine):
            async def execute(self, context):
                step = uuid4().hex
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step)
                yield EngineEvent(EngineEventType.TEXT_DELTA, step_id=step,
                                  delta=EngineDelta('draft', 'internal candidate', step_id=step, visibility='internal'))
                yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step,
                                  output=EngineOutput('draft', 'internal candidate', step_id=step, visibility='internal'))
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta('answer', 'old'))
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta('answer', 'new', operation='replace'))
                yield EngineEvent(EngineEventType.OUTPUT, output=EngineOutput('answer', 'new'))
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False, prepare_provider=False,
                agent_definition={'engine': 'host'}, engine_factories={'host': Outputs}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            api = runtime.plugins
            await api.register('outputs', PluginDefinition('Outputs', {'helper': {'engine': 'host'}}))
            lane = await api.open_session('outputs', 'main', ['helper'])
            ref = await api.submit('outputs', lane['session_id'], 'run', ['helper'], None, 'output')
            session = await api.projects.session('outputs', lane['session_id'])
            await session.run.wait_idle()
            stream = api.streams[session.id]
            stream.PAGE_SIZE = 2
            positions, revision, events = {}, -1, []
            async with asyncio.timeout(5):
                while True:
                    batch = await api.poll('outputs', ref, positions, revision, False)
                    positions, revision = batch['cursors'], batch['revision']
                    events.extend(batch['outputs'])
                    if batch['terminal']:
                        break
            self.assertEqual(batch['runs'][0]['status'], 'completed', batch)
            self.assertEqual([e['value']['text'] for e in events], ['old', 'new', 'new'])
            self.assertEqual(events[1]['value']['operation'], 'replace')
            self.assertTrue(events[2]['value']['final'])
            self.assertTrue(all(e['value']['visibility'] == 'user' for e in events))
            # Closing releases pending observers; it never schedules another Run.
            waiter = asyncio.create_task(api.poll('outputs', ref, positions, revision, False))
            await asyncio.sleep(0)
            api.close_observers()
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                await asyncio.wait_for(waiter, 1)
            self.assertEqual(len(await session.run.alist()), 1)


class PluginContextTests(unittest.TestCase):
    def test_context_is_bounded_explicit_and_does_not_mutate_environment(self):
        environment = ShellEnvironment.from_dict({"cwd": "/work", "env": {"LANG": "ko_KR.UTF-8"}})
        prompt, options = PluginContext(environment, "0123456789", "scrollback", 4).prepare("summarize")
        self.assertIn('"text": "6789"', prompt)
        self.assertIn('"truncated": true', prompt)
        self.assertEqual(options["_hub_environment"]["cwd"], "/work")
        options["_hub_environment"]["env"]["LANG"] = "en_US.UTF-8"
        self.assertEqual(environment.to_dict()["env"]["LANG"], "ko_KR.UTF-8")
        empty, _ = PluginContext(text="private context", max_chars=0).prepare("request")
        self.assertIn('"text": ""', empty)
        self.assertIn('"truncated": true', empty)
        for invalid in (-1, True, "4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PluginContext(max_chars=invalid).prepare("request")

    def test_client_creation_is_lazy(self):
        def unexpected():
            self.fail("Client construction must not start the backend")
        self.assertEqual(PluginClient("demo", unexpected).plugin_id, "demo")


class PluginClientLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_client_shares_worker_without_opening_home_or_recapturing_shell(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from prompt_toolkit.input import create_pipe_input
        from hub.hub import client, HubInstallation
        from hub.ui.application import create_application
        from tests.hub.support import SizedOutput, eventually
        async def action(context):
            yield 'plugin answer'
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False, prepare_provider=False,
                agent_definition={'engine': 'host'}, engine_factories={'host': lambda: BaseEngine(action=action)}),
                input=pipe, output=SizedOutput())
            prompt = SimpleNamespace(app=app)
            installation = SimpleNamespace(_closed=False, controller=controller)
            setattr(prompt, HubInstallation.INSTALLATION_ATTRIBUTE, installation)
            controller.environment_provider = Mock(return_value=ShellEnvironment.from_dict({'cwd': root, 'env': {}}))
            api = client(prompt, 'test')
            self.assertIsNone(controller.worker)
            focus = app.layout.current_control
            try:
                await api.register(PluginDefinition('Test', {'helper': {'engine': 'host'}}))
                await eventually(lambda: controller.view.connected)
                controller.environment_provider.reset_mock()
                worker = controller.worker
                controller.view.composer.text = 'unchanged Home draft'
                lane = await api.open_session('main', agents=['helper'])
                request = await api.submit(lane['session_id'], 'test', recipients=['helper'], request_key='test')
                async with asyncio.timeout(15):
                    batches = [batch async for batch in request.events()]
                self.assertEqual(batches[-1]['runs'][0]['status'], 'completed')
                self.assertFalse(controller.view.visible)
                self.assertIs(app.layout.current_control, focus)
                self.assertIs(controller.worker, worker)
                self.assertEqual(controller.view.composer.text, 'unchanged Home draft')
                controller.environment_provider.assert_not_called()
                # A request handle cannot silently cross a restarted backend.
                saved = request.reference
                await controller.aclose()
                with self.assertRaises(RuntimeError):
                    await api.receipt(lane['session_id'], 'test')
                self.assertEqual(request.reference, saved)
            finally:
                await controller.aclose()
