"""정식 import/패치 경로와 별칭 제거 후 독립 import 순서를 검증한다."""

import importlib
import pickle
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class ServiceLayoutTests(unittest.TestCase):
    def test_only_grouped_service_modules_are_importable(self):
        groups = {
            "lifecycle": ("access", "projects", "sessions", "steps", "components"),
            "runtime": ("runs", "events", "tools"),
            "history": ("conversation", "context"),
            "infrastructure": ("storage", "locking", "logging"),
        }
        for group, names in groups.items():
            for name in names:
                with self.subTest(module=name):
                    current = importlib.import_module(f"llm.services.{group}.{name}")
                    self.assertEqual(Path(current.__file__).parent.name, group)
                    old_path = "llm.services." + name
                    with self.assertRaises(ModuleNotFoundError) as error:
                        importlib.import_module(old_path)
                    self.assertEqual(error.exception.name, old_path)

    def test_current_patch_target_affects_the_implementation(self):
        from llm.services.infrastructure.storage import atomic_json, read_json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with patch("llm.services.infrastructure.storage.sync_directory") as sync:
                atomic_json(path, {"value": "unchanged"})
                sync.assert_called_once_with(path.parent)
            self.assertEqual(read_json(path), {"value": "unchanged"})

    def test_pickle_uses_the_current_class_path(self):
        from llm.services.lifecycle.sessions import SessionRuntime
        self.assertEqual(SessionRuntime.__module__, "llm.services.lifecycle.sessions")
        self.assertIs(pickle.loads(pickle.dumps(SessionRuntime)), SessionRuntime)

    def test_imports_work_in_fresh_processes_without_order_dependencies(self):
        for first in ("llm.components.registry", "llm.services.lifecycle.projects", "llm.services.runtime.runs"):
            with self.subTest(first=first):
                code = (
                    "import importlib; "
                    f"importlib.import_module({first!r}); "
                    "from llm.llm import LargeLanguageModel; "
                    "from llm.services.runtime.runs import RunManager; "
                    "assert RunManager.__module__ == 'llm.services.runtime.runs'"
                )
                result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_definition_creation_has_no_register_synonym(self):
        from llm.services.lifecycle.components import ComponentData
        from llm.components.tools import ToolData
        from llm.components.workflows import WorkflowData
        for cls in (ComponentData, ToolData, WorkflowData):
            self.assertTrue(callable(cls.create))
            self.assertTrue(callable(cls.acreate))
            self.assertFalse(hasattr(cls, "register"))
            self.assertFalse(hasattr(cls, "aregister"))

    def test_removed_compatibility_entry_points_are_absent(self):
        for name in ("llm.services._compat", "examples.llm.markdown_rag",
                     "llm.components.subagents", "llm.components.rag.migration"):
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(name)
        rag = importlib.import_module("llm.components.rag")
        self.assertFalse(hasattr(rag, "migrate_legacy_graphrag"))
