"""Tool selection stays handler-independent, locked and Project-scoped."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components import Component, ComponentRegistry
from llm.components.tools import ToolComponent, ToolData
from llm.components.workflows import WorkflowComponent, WorkflowData
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import WorkspaceBusyError
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository


class ToolSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = ToolComponent()
        self.projects = ProjectManager(ProjectRepository(self.root),
                                       components=[self.component, WorkflowComponent()])
        self.project = self.projects.create("tools", components=["tools", "workflows"])
        self.tools = self.projects.component(self.project, "tools")

    def test_specialized_and_default_handles_share_crud(self):
        self.assertIsInstance(self.tools, ToolData)
        self.assertIsInstance(self.tools, ComponentData)
        self.assertIsInstance(self.projects.component(self.project, "workflows"), WorkflowData)
        # Workflow도 이제 편의 핸들이 있다. 별도 data_class가 없는 컴포넌트의 기본 계약을 확인한다.
        class Notes(Component):
            name = directory = "notes"
        self.projects.components.register(Notes())
        self.projects.set_components(self.project, ("tools", "workflows", "notes"))
        self.assertIs(type(self.projects.component(self.project, "notes")), ComponentData)
        definition = {"source": "# editable source"}
        self.tools.create(definition, identifier="add")
        self.assertEqual(self.tools.load("add"), definition)
        self.assertEqual(self.tools.enabled(), [])  # Creating a definition never selects a tool.
        self.tools.enable("add")
        with self.assertRaises(ValueError):
            self.tools.delete("add")
        self.tools.disable("add")
        self.tools.delete("add")
        self.assertEqual(self.tools.list(), {})

    def test_selection_is_ordered_idempotent_and_preserves_extensions(self):
        for name in ("first", "add", "search"):
            self.tools.create({"source": "# editable"}, identifier=name)
        self.tools.configure({"enabled": ["first"], "future_policy": {"timeout": 30}})
        self.tools.enable("add", "search", "add")
        self.tools.enable("first", "add")
        self.assertEqual(self.tools.enabled(), ["first", "add", "search"])
        self.tools.disable("first", "missing", "first")
        self.tools.disable("first")
        self.assertEqual(self.tools.enabled(), ["add", "search"])
        self.tools.enable()
        self.tools.disable()
        names = ["search", "add"]
        self.tools.set_enabled(names)
        names.append("caller")
        snapshot = self.tools.enabled()
        snapshot.append("reader")
        self.assertEqual(self.tools.configuration(), {
            "enabled": ["search", "add"], "future_policy": {"timeout": 30}})
        self.tools.set_enabled(())
        self.assertEqual(self.tools.enabled(), [])
        self.assertFalse(hasattr(self.component, "catalog"))

    def test_invalid_selection_never_changes_persisted_configuration(self):
        for name in ("duplicate", "new"):
            self.tools.create({"source": "# editable"}, identifier=name)
        self.tools.configure({"enabled": ["kept"], "extra": 1})
        before = self.tools.configuration()
        for names in ("add", None, ["duplicate", "duplicate"], [1], [""], ["  "], [None]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.tools.set_enabled(names)
            self.assertEqual(self.tools.configuration(), before)
        for method in (self.tools.enable, self.tools.disable):
            for name in (None, 1, [], "", "  "):
                with self.subTest(method=method.__name__, name=name), self.assertRaises(ValueError):
                    method("valid", name)
                self.assertEqual(self.tools.configuration(), before)
        with patch("llm.services.infrastructure.storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.tools.enable("new")
        self.assertEqual(self.tools.configuration(), before)

    def test_selection_survives_reload_and_clone_without_cross_project_changes(self):
        self.tools.create({"source": "# editable"}, identifier="offline_definition")
        self.tools.enable("offline_definition")  # Selection does not import code.
        clone = self.projects.clone(self.project)
        cloned_tools = self.projects.component(clone, "tools")
        cloned_tools.create({"source": "# editable"}, identifier="clone_only")
        cloned_tools.enable("clone_only")
        self.assertEqual(self.tools.enabled(), ["offline_definition"])
        reopened = ProjectManager(ProjectRepository(self.root),
                                  components=[ToolComponent(), WorkflowComponent()])
        loaded = reopened.component(reopened.load(clone.id), "tools")
        self.assertEqual(loaded.enabled(), ["offline_definition", "clone_only"])

    def test_stale_handles_check_selection_and_deleted_project_for_every_method(self):
        actions = (self.tools.enabled, lambda: self.tools.enable("add"),
                   lambda: self.tools.disable("add"), lambda: self.tools.set_enabled([]))
        self.tools.create({"source": "# editable"}, identifier="kept")
        self.tools.enable("kept")
        self.projects.remove_component(self.project, "tools")
        for action in actions:
            with self.assertRaises(ValueError):
                action()
        self.projects.set_components(self.project, ["tools", "workflows"])
        self.assertEqual(self.tools.enabled(), ["kept"])
        self.projects.delete(self.project)
        for action in actions:
            with self.assertRaises(ValueError):
                action()

    def test_complete_selection_update_is_locked(self):
        self.tools.configure({"enabled": [], "extra": {"kept": True}})
        for i in range(12):
            self.tools.create({"source": "# editable"}, identifier="tool_" + str(i))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.tools.enable("tool_" + str(i)), range(12)))
        self.assertEqual(set(self.tools.enabled()), {"tool_" + str(i) for i in range(12)})
        competing = ProjectRepository(self.root)
        with competing.ownership.scope():
            for action in (self.tools.enabled, lambda: self.tools.enable("blocked"),
                           lambda: self.tools.disable("tool_0"),
                           lambda: self.tools.set_enabled([])):
                with self.assertRaises(WorkspaceBusyError):
                    action()
        self.assertEqual(len(self.tools.enabled()), 12)
        self.assertEqual(self.tools.configuration()["extra"], {"kept": True})

    def test_other_components_can_opt_in_and_invalid_handle_registration_is_rejected(self):
        class NotesData(ComponentData):
            pass

        class Notes(Component):
            name = "notes"
            directory = "notes"
            data_class = NotesData

        self.projects.components.register(Notes())
        self.projects.set_components(self.project, ["tools", "notes"])
        notes = self.projects.component(self.project, "notes")
        self.assertIsInstance(notes, NotesData)
        identifier = notes.create({"text": "kept"})
        self.assertEqual(notes.load(identifier), {"text": "kept"})
        for value in (object, "invalid", lambda: None):
            invalid = Notes()
            invalid.data_class = value
            registry = ComponentRegistry()
            with self.assertRaisesRegex(ValueError, "ComponentData"):
                registry.register(invalid)
            with self.assertRaises(ValueError):
                registry.get("notes")
