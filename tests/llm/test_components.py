"""Generic component data, lifecycle, path safety and runtime tool adaptation."""

import asyncio
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

from llm.components.base import Component, ProjectComponent
from llm.components.registry import ComponentRegistry
from llm.components.agents import AgentComponent
from llm.components.tools import Tool, ToolComponent, ToolData, ToolRegistry
from llm.components.tools.resolver import ComponentToolResolver
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import ProjectConfig, RunStatus
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import WorkspaceBusyError
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.lifecycle.sessions import SessionManager
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class Notes(Component):
    name = "notes"
    directory = "knowledge"

    def configuration_schema(self):
        from llm.core.schema import implementation_schema, open_schema
        return implementation_schema(config=open_schema("test Notes implementation", category="implementation"))


class ComponentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = ComponentRegistry((Notes(), AgentComponent(), WorkflowComponent()))
        self.sessions = SessionManager()
        self.projects = ProjectManager(ProjectRepository(self.root / "projects"), self.sessions,
                                       components=self.registry)
        self.project = self.projects.create("Example", components=("notes",))
        self.data = self.projects.component(self.project, "notes")

    def test_declaration_and_selection_own_directory_creation(self):
        root = self.project.paths.root
        self.assertTrue((root / "knowledge" / "records").is_dir())
        self.assertEqual(self.data.configuration(), {})
        self.assertFalse((root / "agents").exists())
        self.projects.set_components(self.project, ("notes", "agents", "workflows"))
        for directory in ("knowledge", "agents", "workflows"):
            self.assertTrue((root / directory / "records").is_dir())
            self.assertFalse((root / directory / "component.json").exists())
        self.assertFalse(hasattr(self.project.paths, "knowledge"))
        self.assertEqual(self.projects.load(self.project.id).components, self.project.components)

    def test_crud_open_keys_serialization_and_detached_values(self):
        value = {"title": "문서", "future": {"options": [1, True, None]}, "version": 7}
        self.assertEqual(Component.deserialize(Component.serialize(value)), value)
        identifier = self.data.create(value, identifier="document")
        value["future"]["options"].append("caller")
        loaded = self.data.load(identifier)
        self.assertEqual(loaded["future"]["options"], [1, True, None])
        loaded["future"]["options"].append("reader")
        self.assertNotEqual(loaded, self.data.load(identifier))
        updated = self.data.update(identifier, {"future": {"new": 1}, "new_key": ["x"]})
        self.assertEqual(updated["future"], {"new": 1})
        self.assertEqual(updated["title"], "문서")
        self.assertEqual(self.data.list(), {identifier: updated})
        self.data.save(identifier, {"replacement": True})
        self.assertEqual(self.data.load(identifier), {"replacement": True})
        with self.assertRaises(FileExistsError):
            self.data.create({}, identifier=identifier)
        self.data.delete(identifier)
        self.assertEqual(self.data.list(), {})
        with self.assertRaises(FileNotFoundError):
            self.data.save(identifier, {})
        self.data.configure({'config': {'future_setting': {'label': 'anything'}}})
        self.assertEqual(self.data.configuration(), {"config": {"future_setting": {"label": "anything"}}})

    def test_invalid_json_and_failed_atomic_replacement_keep_original_data(self):
        identifier = self.data.create({"kept": True})
        invalid = [{"value": object()}, {1: "lossy"}, {"value": float("nan")},
                   [1], {"tuple": (1, 2)}]
        for value in invalid:
            with self.subTest(value=type(value)):
                with self.assertRaises((TypeError, ValueError)):
                    self.data.save(identifier, value)
                self.assertEqual(self.data.load(identifier), {"kept": True})
        with patch("llm.services.infrastructure.storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.data.update(identifier, {"new": 1})
        self.assertEqual(self.data.load(identifier), {"kept": True})
        with self.assertRaises(TypeError):
            Component.deserialize("[]")
        with self.assertRaises(ValueError):
            Component.deserialize('{"number": NaN}')

    def test_directory_conflicts_and_path_traversal_are_rejected(self):
        for directory in ("../escape", "sessions", "state", "logs", "cache", "a/b", "a:b", "KNOWLEDGE"):
            with self.subTest(directory=directory):
                candidate = type("Other", (Component,), {"name": "other", "directory": directory})()
                with self.assertRaises(ValueError):
                    self.registry.register(candidate)
        with self.assertRaises(ValueError):
            self.registry.register(type("Missing", (Component,), {"name": "missing"})())
        for identifier in ("../project", "a/b", "a:b", "x.json", ""):
            with self.assertRaises(ValueError):
                self.data.create({}, identifier=identifier)
        self.assertTrue((self.project.paths.root / "project.json").is_file())

    def test_handles_reload_selection_deletion_and_project_ownership(self):
        self.data.create({"kept": True}, identifier="note")
        self.projects.remove_component(self.project, "notes")
        with self.assertRaises(ValueError):
            self.data.load("note")
        self.assertTrue((self.project.paths.root / "knowledge" / "records" / "note.json").exists())
        self.projects.set_components(self.project, ("notes",))
        self.assertEqual(self.data.load("note"), {"kept": True})
        wrong = deepcopy(self.project)
        wrong.paths = type(wrong.paths)(self.root / "outside")
        with self.assertRaises(ValueError):
            self.projects.component(wrong, "notes")
        self.projects.delete(self.project)
        with self.assertRaises(ValueError):
            self.data.create({})

    def test_permanent_component_removal_and_reenable(self):
        self.data.create({"kept": False}, identifier="note")
        sessions_root = self.project.paths.sessions
        self.projects.remove_component(self.project, "notes", permanent=True)
        self.assertFalse((self.project.paths.root / "knowledge").exists())
        self.assertTrue(sessions_root.exists())
        self.projects.set_components(self.project, ("notes",))
        self.assertEqual(self.data.list(), {})
        self.assertEqual(self.data.configuration(), {})
        with self.assertRaises(TypeError):
            self.projects.remove_component(self.project, "notes", permanent="yes")

    def test_clone_copies_definitions_and_config_but_not_arbitrary_artifacts(self):
        self.projects.set_components(self.project, ("notes", "agents", "workflows"))
        agents = self.projects.component(self.project, "agents")
        graphs = self.projects.component(self.project, "workflows")
        agents.create({"engine": "loop", "purpose": "Review code", "completion": {"model": "openai/test", "temperature": 0.2},
                       "system_prompt": "Review code", "metadata": {"custom": [1]}}, identifier="reviewer")
        graphs.create(WorkflowGraph(entry="review", metadata={"future_graph_option": True})
                      .node("review", "agent", agent="reviewer").node("end", "end")
                      .connect("review", "end").to_dict(), identifier="review")
        self.data.configure({'config': {'format_version': 3}})
        self.data.create({"body": "text"}, identifier="note")
        (self.project.paths.root / "knowledge" / "artifact.bin").write_bytes(b"not a definition")
        clone = self.projects.clone(self.project)
        for name in self.project.components:
            source = self.projects.component(self.project, name)
            target = self.projects.component(clone, name)
            self.assertEqual(source.configuration(), target.configuration())
            self.assertEqual(source.list(), target.list())
        self.projects.component(clone, "agents").update("reviewer", {"metadata": {"custom": []}})
        self.assertEqual(agents.load("reviewer")["metadata"]["custom"], [1])
        self.assertFalse((clone.paths.root / "knowledge" / "artifact.bin").exists())

    def test_component_initialization_preserves_unmanaged_files(self):
        root = self.project.paths.root / "workflows"
        root.mkdir()
        (root / "notes.txt").write_text('user notes', encoding="utf-8")
        self.projects.set_components(self.project, ("notes", "workflows"))
        graphs = self.projects.component(self.project, "workflows")
        self.assertEqual(graphs.list(), {})
        self.assertTrue((root / "notes.txt").exists())

    def test_legacy_component_configuration_is_rejected_without_changes(self):
        self.projects.set_components(self.project, ("notes", "workflows"))
        path = self.project.paths.root / "workflows" / "component.json"
        path.write_text('{"legacy": true}', encoding="utf-8")
        graphs = self.projects.component(self.projects.load(self.project.id), "workflows")
        with self.assertRaisesRegex(ValueError, "Legacy component.json"):
            graphs.configuration()
        with self.assertRaisesRegex(ValueError, "Legacy component.json"):
            self.projects.clone(self.project)
        self.assertEqual(path.read_text(encoding="utf-8"), '{"legacy": true}')

    def test_generic_exports_have_no_tool_dependency(self):
        class Search(Component):
            name = "search"
            directory = "search_index"
            capabilities = ("retriever",)
            def resolve(self, project, capability):
                return {"project": project.id}
        self.registry.register(Search())
        self.projects.set_components(self.project, ("notes", "search"))
        self.assertEqual(self.registry.resolve(self.project, "retriever"), ({"project": self.project.id},))
        self.assertEqual(ComponentToolResolver(self.registry).resolve_tools(self.project).names(), ())
        code = ("import sys; import llm.components.base; import llm.components.registry; "
                "assert not any(k.startswith('llm.components.tools') for k in sys.modules)")
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)

    def test_component_handle_obeys_workspace_lock_and_serializes_updates(self):
        self.data.create({}, identifier="record")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.data.update("record", {str(i): i}), range(12)))
        self.assertEqual(self.data.load("record"), {str(i): i for i in range(12)})
        competing = ProjectRepository(self.projects.repository.root)
        with competing.ownership.scope():
            with self.assertRaises(WorkspaceBusyError):
                self.data.update("record", {"must_not_write": True})
        self.assertNotIn("must_not_write", self.data.load("record"))

    def test_linked_directory_and_contents_cannot_escape_component_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        root = self.project.paths.root / "knowledge"
        link = root / "records"
        link.rmdir()
        link.symlink_to(outside, target_is_directory=True)
        try:
            for action in (lambda: self.data.create({}, identifier="escape"), self.data.list,
                           lambda: self.projects.remove_component(self.project, "notes", permanent=True)):
                with self.assertRaises(ValueError):
                    action()
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse((outside / "escape.json").exists())
        finally:
            link.unlink()

    def test_public_type_hints_remain_resolvable(self):
        for cls in (Component, ComponentData, ProjectComponent, ToolComponent, ToolData):
            for name, member in inspect.getmembers(cls, inspect.isfunction):
                if not name.startswith("_"):
                    get_type_hints(member)


class ToolDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tool_definition_reaches_loop_and_uses_registered_handler(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = ToolComponent()
            registry = ComponentRegistry((tools, AgentComponent()))
            sessions = SessionManager()
            projects = ProjectManager(ProjectRepository(Path(temporary)), sessions, components=registry)
            project = projects.create("Test", components=("tools", "agents"),
                                      config=ProjectConfig(parameters={"engines": {"loop": {'config': {'completion': {'model': 'openai/test'}}}}}))
            data = projects.component(project, "tools")
            definition = {"type": "function", "function": {"name": "add", "description": "new",
                          "parameters": {"type": "object", "properties": {"a": {"type": "number"}},
                                         "required": ["a"], "additionalProperties": False},
                          "strict": True}}
            source = {"source": 'from llm.components.tools import tool\n@tool(strict=True)\nasync def main(a: float):\n    """new"""\n    return "result"\n'}
            self.assertEqual(data.create(source, identifier="add"), "add")
            data.configure({'config': {'enabled': []}})
            data.enable("add")
            resolved = ComponentToolResolver(registry).resolve_tools(projects.load(project.id))
            self.assertEqual(resolved.definitions(), [definition])
            self.assertEqual(await resolved.get("add").handler({"a": 2}), "result")
            with self.assertRaises(ValueError):
                resolved.prepare("add", '{"b": 1}')
            completion = ScriptedCompletion([chunk(calls=[call('{"a":2}')], finish="tool_calls")],
                                            [chunk("done", finish="stop")])
            engines = EngineRegistry()
            engines.register("loop", LoopEngine(completion_fn=completion))
            session = sessions.create(project, "Test")
            manager = RunManager(sessions, engines, session=session, capabilities=registry)
            try:
                await manager.submit("request", engine="loop")
                await manager.wait_idle()
                self.assertEqual(completion.requests[1]["messages"][-1]["content"], "result")
                self.assertEqual(completion.requests[0]["tools"], [definition])
                self.assertEqual(manager.repository.list(session)[0].status, RunStatus.COMPLETED)
                with self.assertRaises(ValueError):
                    projects.remove_component(project, "tools", permanent=True)
            finally:
                await manager.shutdown()
            with self.assertRaises(ValueError):
                data.delete("add")
            clone = projects.clone(project)
            self.assertEqual(projects.component(clone, "tools").list(), {"add": source})
            data.disable("add")
            data.delete("add")
            self.assertEqual(data.list(), {})
            self.assertEqual(projects.component(clone, "tools").load("add"), source)

    async def test_invalid_tool_record_does_not_replace_valid_definition(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = ComponentRegistry((ToolComponent(),))
            projects = ProjectManager(ProjectRepository(Path(temporary)), SessionManager(), components=registry)
            project = projects.create("Test", components=("tools",))
            data = projects.component(project, "tools")
            valid = {"source": "# incomplete"}
            data.create(valid, identifier="add")
            for invalid in ({"type": "function", "function": {"name": "other", "parameters": {}}},
                            {"type": "function", "function": {"name": "add", "parameters": {"type": "string"}}},
                            {"type": "function", "function": {"name": "add", "parameters": {"type": "object", "$ref": "https://invalid.test"}}}):
                with self.assertRaises(ValueError):
                    data.save("add", invalid)
                self.assertEqual(data.load("add"), valid)
