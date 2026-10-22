"""Session-bound public runtime API, notifications, and open component settings."""

import asyncio
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path

from llm.components import Component, ComponentRegistry
from llm.components.tools import Tool, ToolComponent, ToolRegistry
from llm.components.tools.resolver import ComponentToolResolver
from llm.core.agents import AgentBinding, AgentDefinition
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import ProjectConfig, Run, RunStatus, MessageRole, MessageStatus, new_id
from llm.engines import BaseEngine, EngineRegistry
from llm.services.history.conversation import ConversationStore
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager, RunRequestError, RunErrorCode, RunEventType
from llm.services.lifecycle.sessions import SessionManager
from tests.llm.support.fake_engine import FakeStreamingEngine


class PublicRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sessions = SessionManager()
        self.components = ComponentRegistry()
        self.engines = EngineRegistry()
        self.engine = FakeStreamingEngine()
        self.engines.register("fake", self.engine)
        self.projects = ProjectManager(ProjectRepository(Path(temp.name)), self.sessions,
                                       components=self.components, engines=self.engines)
        self.project = self.projects.create("Project", config=ProjectConfig())
        self.session = self.sessions.create(self.project, "Session")
        self.projects.agents.create(self.project, {"engine": "fake"}, identifier="fake")
        self.binding = AgentBinding.create(self.project.id, "fake", {"engine": "fake"})

    def manager(self, session=None, **kwargs):
        manager = RunManager(self.sessions, self.engines, session=session or self.session, **kwargs)
        self.addAsyncCleanup(manager.shutdown)
        return manager

    async def test_engine_is_required_before_admission_even_with_project_defaults(self):
        self.assertNotIn("default_engine", ProjectConfig())
        loaded = self.sessions.load(self.project, self.session.id)
        self.assertNotIn("default_engine", asdict(loaded))
        manager = self.manager(loaded)
        with self.assertRaises(TypeError):
            await manager.submit("missing engine")
        for invalid in (None, "", "   ", 123):
            with self.subTest(agent_id=invalid), self.assertRaises(RunRequestError) as raised:
                await manager.submit("invalid engine", agent_id=invalid)
            self.assertEqual(raised.exception.code, RunErrorCode.AGENT_REQUIRED)
        self.assertEqual(ConversationStore(self.session.paths.conversation).list(), [])
        self.assertEqual(manager.repository.list(self.session), [])
        self.assertFalse(self.sessions.ownership.session_attached((self.project.id, self.session.id)))
        await manager.submit("explicit choice", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual(manager.repository.list(self.session)[0].engine, "fake")

    async def test_removed_session_field_is_rejected_without_rewriting_metadata(self):
        path = self.session.paths.root / "session.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["default_engine"] = "fake"
        encoded = json.dumps(data)
        path.write_text(encoded, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected=\\['default_engine'\\]"):
            self.sessions.load(self.project, self.session.id)
        self.assertEqual(path.read_text(encoding="utf-8"), encoded)

    async def test_capability_resolver_requires_current_contract(self):
        class ToolOnly:
            def resolve_tools(self, project):
                raise AssertionError("removed resolver contract must not be called")
        with self.assertRaisesRegex(TypeError, "resolve\\(project, names\\)"):
            self.manager(capabilities=ToolOnly())
        calls = []
        class Current:
            def resolve(self, project, names):
                calls.append(names)
                return {"custom": {"ok": True}}
        self.engine.required_capabilities = ("custom",)
        manager = self.manager(capabilities=Current())
        await manager.submit("go", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual(calls, [("custom",)])
        self.assertEqual(self.engine.contexts[0].capabilities["custom"], {"ok": True})

    async def test_restored_request_without_engine_fails_without_guessing_and_queue_continues(self):
        store = ConversationStore(self.session.paths.conversation)
        store.create(MessageRole.USER, "missing admission evidence", MessageStatus.QUEUED)
        store.create(MessageRole.USER, "explicit", MessageStatus.QUEUED,
                     metadata={"agent_binding": self.binding})
        before = store.path.read_bytes()
        events = []
        manager = self.manager()
        manager.subscriptions.subscribe(events.append, channel="run")
        # C requires the original identity on every Run: no fabricated failed
        # Run or guessed Agent can replace missing admission evidence.
        with self.assertRaisesRegex(ValueError, "requires all current evidence"):
            await manager.start()
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(manager.repository.list(self.session), [])
        self.assertEqual(self.engine.contexts, [])
        self.assertEqual(events, [])
        self.assertFalse(self.sessions.ownership.session_attached((self.project.id, self.session.id)))

    async def test_session_is_required_and_binding_is_detached_from_caller_mutations(self):
        with self.assertRaises(TypeError):
            RunManager(self.sessions, self.engines)
        with self.assertRaises(TypeError):
            RunManager(self.sessions, self.engines, session=None)
        manager = self.manager()
        exposed = manager.session
        exposed.id = new_id()
        self.assertEqual(manager.session.id, self.session.id)
        with self.assertRaises(TypeError):
            await manager.submit(self.project, self.session, "old signature", agent_id="fake")
        await manager.submit("new signature", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual(manager.repository.list(self.session)[0].status, RunStatus.COMPLETED)

    async def test_registration_rejections_do_not_admit_messages_or_attach_runtime(self):
        manager = self.manager()
        with self.assertRaises(FileNotFoundError):
            await manager.submit("request", agent_id="typo")
        self.assertEqual(ConversationStore(self.session.paths.conversation).list(), [])
        self.assertEqual(manager.repository.list(self.session), [])
        self.sessions.save(self.session)  # No runtime lease was acquired by the rejection.
        for name, engine in (("", self.engine), ("invalid", object())):
            with self.assertRaises((TypeError, ValueError)):
                self.engines.register(name, engine)

    async def test_missing_component_registration_is_rejected_before_queue_admission(self):
        self.components.register(WorkflowComponent())
        self.projects.set_components(self.project, ("workflows",))
        manager = self.manager()  # Application omitted the Project component registry.
        with self.assertRaises(RunRequestError) as caught:
            await manager.submit("request", agent_id="fake")
        self.assertEqual(caught.exception.code, RunErrorCode.COMPONENT_NOT_REGISTERED)
        self.assertEqual(manager.repository.list(self.session), [])
        self.assertEqual(ConversationStore(self.session.paths.conversation).list(), [])

    async def test_started_and_terminal_notifications_follow_durable_state_on_event_loop(self):
        loop_thread = threading.get_ident()
        events = []
        manager = None
        def observe(event):
            self.assertEqual(threading.get_ident(), loop_thread)
            persisted = manager.repository.load(self.session, event.run.id)
            self.assertEqual(persisted.status, event.run.status)
            events.append(event)
        manager = self.manager()
        manager.subscriptions.subscribe(observe, channel="run")
        await manager.submit("request", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual([event.type for event in events], [RunEventType.STARTED, RunEventType.COMPLETED])
        self.assertEqual(events[0].run.status, RunStatus.RUNNING)
        events[1].run.metadata["changed_by_observer"] = True
        self.assertNotIn("changed_by_observer", manager.repository.list(self.session)[0].metadata)

    async def test_early_failure_reports_code_detail_and_failed_event_then_continues_queue(self):
        class Broken(BaseEngine):
            async def execute(self, context):
                raise RuntimeError("action failure detail")
                yield
        self.engines.register("broken", Broken())
        self.projects.agents.create(self.project, {"engine": "broken"}, identifier="broken")
        events = []
        manager = self.manager()
        manager.subscriptions.subscribe(events.append, channel="run")
        await manager.submit("bad", agent_id="broken")
        await manager.submit("good", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual([e.type for e in events], [RunEventType.STARTED, RunEventType.FAILED,
                                                  RunEventType.STARTED, RunEventType.COMPLETED])
        failed = events[1].run
        self.assertEqual(failed.error_code, RunErrorCode.ENGINE_FAILED)
        self.assertEqual(failed.error, "action failure detail")
        result = self.projects.results.load(self.project, failed.id)
        self.assertEqual(result.error_code, "engine_failed")
        self.assertEqual(result.error, failed.error)

    async def test_runtime_binding_failure_is_a_failed_run_not_a_data_crud_failure(self):
        self.engine.required_capabilities = ("tools",)
        tools = ToolComponent()
        self.components.register(tools)
        self.projects.set_components(self.project, ("tools",))
        data = self.projects.component(self.project, "tools")
        data.create({"source": "# incomplete"}, identifier="offline")
        data.configure({'config': {'enabled': ['offline']}})
        events = []
        manager = self.manager(capabilities=self.components)
        manager.subscriptions.subscribe(events.append, channel="run")
        await manager.submit("request", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual(events[-1].type, RunEventType.FAILED)
        self.assertEqual(events[-1].run.error_code, RunErrorCode.CAPABILITY_FAILED)
        self.assertEqual(self.engine.contexts, [])
        self.assertIn("offline", data.list())

    async def test_restored_queue_with_unregistered_engine_has_a_terminal_run_and_notification(self):
        store = ConversationStore(self.session.paths.conversation)
        self.projects.agents.repository.create(self.project, {"engine": "removed_engine"}, identifier="retained")
        request = store.create(MessageRole.USER, "old request", MessageStatus.QUEUED,
                               metadata={"agent_binding": AgentBinding.create(self.project.id, "retained", {"engine": "removed_engine"})})
        events = []
        manager = self.manager()
        manager.subscriptions.subscribe(events.append, channel="run")
        await manager.start()
        await manager.wait_idle()
        run = manager.repository.list(self.session)[0]
        self.assertEqual(run.error_code, RunErrorCode.ENGINE_NOT_REGISTERED)
        self.assertEqual(events[-1].type, RunEventType.FAILED)
        self.assertEqual(store.get(request.id).status, MessageStatus.COMMITTED)
        self.assertEqual(self.engine.contexts, [])

    async def test_failing_observer_does_not_drop_requests(self):
        called = []
        def observe(event):
            called.append(event.type)
            raise RuntimeError("display failure")
        manager = self.manager()
        manager.subscriptions.subscribe(observe, channel="run")
        await manager.submit("one", agent_id="fake")
        await manager.submit("two", agent_id="fake")
        await manager.wait_idle()
        self.assertEqual(called.count(RunEventType.COMPLETED), 2)
        self.assertTrue(all(r.status == RunStatus.COMPLETED for r in manager.repository.list(self.session)))

    async def test_shutdown_one_session_preserves_other_session_and_releases_only_its_attachment(self):
        self.engine.gate = asyncio.Event()
        other = self.sessions.create(self.project, "Other")
        events = []
        first = self.manager()
        first.subscriptions.subscribe(events.append, channel="run")
        second = self.manager(other)
        await first.submit("first", agent_id="fake")
        await second.submit("other", agent_id="fake")
        async def both_running():
            while self.engine.active != 2:
                await asyncio.sleep(0.005)
        await asyncio.wait_for(both_running(), 10)
        queued = await first.submit("later", agent_id="fake")
        await first.shutdown()
        self.assertEqual(events[-1].type, RunEventType.INTERRUPTED)
        self.assertEqual(events[-1].run.error_code, RunErrorCode.INTERRUPTED)
        self.assertEqual(ConversationStore(self.session.paths.conversation).get(queued.id).status, MessageStatus.QUEUED)
        self.assertEqual(second.repository.list(other)[0].status, RunStatus.RUNNING)
        edited = self.sessions.load(self.project, self.session.id)
        edited.title = "Edited without stopping Other"
        self.sessions.save(edited)
        self.engine.gate.set()
        await second.wait_idle()
        resumed = self.manager()
        await resumed.start()
        await resumed.wait_idle()
        self.assertEqual(resumed.repository.list(self.session)[-1].status, RunStatus.COMPLETED)

    async def test_recovery_emits_interrupted_with_restart_code_without_replaying(self):
        manager = self.manager()
        store = ConversationStore(self.session.paths.conversation)
        request = store.create(MessageRole.USER, "claimed", MessageStatus.COMMITTED)
        run_id = new_id()
        run = Run(run_id, self.session.id, request.id, new_id(), "fake",
                  manager.repository.paths(self.session, run_id), status=RunStatus.RUNNING,
                  agent_id="fake", metadata={"agent_binding": self.binding})
        run.started_at = run.created_at
        with self.projects.ownership.scope():
            manager.repository.create(run)
            store.create(MessageRole.ASSISTANT, "partial", MessageStatus.STREAMING,
                         message_id=run.assistant_message_id, run_id=run.id)
        events = []
        manager.subscriptions.subscribe(events.append, channel="run")
        await manager.start()
        await manager.wait_idle()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, RunEventType.INTERRUPTED)
        self.assertEqual(events[0].run.error_code, RunErrorCode.PROCESS_RESTART)
        self.assertEqual(self.engine.contexts, [])


class OpenComponentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sessions = SessionManager()
        self.tool = ToolComponent()
        self.components = ComponentRegistry((self.tool, WorkflowComponent()))
        from llm.engines.loop import LoopEngine
        engines = EngineRegistry()
        engines.register("loop", LoopEngine())
        self.projects = ProjectManager(ProjectRepository(Path(temp.name)), self.sessions,
                                       components=self.components, engines=engines)
        self.project = self.projects.create("Project", components=('tools', 'workflows'))

    def test_open_config_roundtrip_clone_session_overrides_and_field_names(self):
        config = ProjectConfig(data={"editor": {"font_size": 14}, "theme": "dark"})
        self.assertFalse(is_dataclass(config))
        config.data["password"] = "ordinary-example-value"
        config.parameters["engines"] = {"loop": {"config": {"completion": {"model": "example", "api_key": "example-value"}}}}
        config.data["new_section"] = {"custom": [1, True]}
        config.data["model"] = {"workspace_setting": True}
        config.data["__deepcopy__"] = "ordinary mapping key"
        self.project.config = config
        self.assertEqual(asdict(self.project)["config"], config)
        self.projects.save(self.project)
        loaded = self.projects.load(self.project.id).config
        self.assertEqual(loaded, config)
        self.assertEqual(ProjectConfig.deserialize(config.serialize()), config)
        self.assertEqual(self.projects.clone(self.project).config, config)
        settings = config.resolve_engine_config("loop", {"data": {"editor": {"font_size": 18}}})
        self.assertEqual(settings["data"]["editor"]["font_size"], 18)
        self.assertEqual(config.data["editor"]["font_size"], 14)
        self.assertEqual(settings["data"]["theme"], "dark")
        encoded = Component.serialize({"properties": {"password": {"type": "string"}}})
        self.assertIn("password", Component.deserialize(encoded)["properties"])

    def test_all_definition_crud_and_clone_work_without_runtime_handlers(self):
        data = self.projects.component(self.project, "tools")
        definition = {"source": "# incomplete"}
        data.create(definition, identifier="offline")
        data.configure({'config': {'enabled': ['offline']}})
        self.assertEqual(data.load("offline"), definition)
        data.update("offline", {"requirements": "# none"})
        definitions = {
            "workflows": WorkflowGraph(entry="end").node("end", "end").to_dict(),
        }
        for name, definition in definitions.items():
            records = self.projects.component(self.project, name)
            records.create({**definition, "metadata": {"unknown": {"future": [1]}}}, identifier="example")
            records.update("example", {"metadata": {"new": True}})
            self.assertTrue(records.load("example")["metadata"]["new"])
        agents = self.projects.agents
        agents.create(self.project, {"engine": "loop", "purpose": "Test", "completion": {"model": "test/model"},
            "metadata": {"unknown": {"future": [1]}}}, identifier="example")
        original = agents.snapshot(self.project, "example")
        agents.revise(self.project, "example", {**original["definition"], "metadata": {"new": True}},
                      expected_revision=original["revision"])
        self.assertTrue(agents.load(self.project, "example")["metadata"]["new"])
        clone = self.projects.clone(self.project)
        self.assertEqual(agents.list(clone), agents.list(self.project))
        for name in self.project.components:
            self.assertEqual(self.projects.component(clone, name).list(), self.projects.component(self.project, name).list())
        with self.assertRaises(ValueError):
            ComponentToolResolver(self.components).resolve_tools(self.projects.load(self.project.id))
        data.save("offline", {"source": '''from llm.components.tools import tool, ToolContract, ToolEffect, ToolIsolation
@tool(ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=()))
async def main():
    """Offline tool."""
    return "ok"
'''})
        self.assertEqual(ComponentToolResolver(self.components).resolve_tools(self.projects.load(self.project.id)).names(), ("offline",))
        data.configure({'config': {'enabled': []}})
        data.delete("offline")
        self.assertEqual(data.list(), {})

    def test_only_requested_capability_is_built(self):
        requested = []
        class Multi(Component):
            name = "multi"
            directory = "multi"
            capabilities = ("retriever", "reranker")
            def resolve(self, project, capability):
                requested.append(capability)
                if capability == "reranker":
                    raise RuntimeError("backend unavailable")
                return "retriever instance"
        self.components.register(Multi())
        self.projects.set_components(self.project, ('tools', 'workflows', 'multi'))
        self.assertEqual(self.components.resolve(self.project, "retriever"), ("retriever instance",))
        self.assertEqual(requested, ["retriever"])
        self.assertEqual(ComponentToolResolver(self.components).resolve_tools(self.projects.load(self.project.id)).names(), ())
        self.assertEqual(requested, ["retriever"])
