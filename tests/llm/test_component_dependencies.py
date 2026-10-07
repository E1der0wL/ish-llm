"""정확한 Component 의존성과 정의/모델 검증의 저장 전 경계."""

import tempfile
import unittest
from pathlib import Path

from llm.components.base import Component
from llm.components.definitions import DefinitionComponent
from llm.components.registry import ComponentRegistry, ComponentDependencyError
from llm.components.rag import RAGComponent
from llm.core.schema import open_schema
from llm.llm import LargeLanguageModel


class A(Component):
    name = directory = "a"
    required_components = ("b",)


class B(Component):
    name = directory = "b"


class ComponentDependencyTests(unittest.TestCase):
    def test_definition_default_and_explicit_open(self):
        class Closed(DefinitionComponent):
            name = directory = "closed"
        class Open(Closed):
            schema = open_schema("test plugin owned record", category="implementation")
        with self.assertRaisesRegex(ValueError, "future"):
            Closed().validate_record("record", {"future": 1})
        Open().validate_record("record", {"future": 1})
        B().validate_record("record", {"future": 1})

    def test_declarations_and_registration_order(self):
        self.assertEqual(B.required_components, ())
        registry = ComponentRegistry((A(), B()))
        self.assertEqual(registry.validate(("a", "b")), ("a", "b"))
        for declaration in (["b"], ("b", "b"), ("a",), ("../b",), (1,)):
            with self.subTest(value=declaration), self.assertRaises(ValueError):
                component = A()
                component.required_components = declaration
                ComponentRegistry((component,))
        b = B()
        b.required_components = ("a",)
        self.assertEqual(ComponentRegistry((A(), b)).validate(("b", "a")), ("b", "a"))

    def test_missing_unregistered_and_discovery(self):
        with self.assertRaises(ComponentDependencyError) as raised:
            ComponentRegistry((A(),)).validate(("a",))
        self.assertEqual(raised.exception.missing, {"a": ["b"]})
        self.assertEqual(raised.exception.unavailable, ("b",))
        with tempfile.TemporaryDirectory() as directory:
            backend = LargeLanguageModel(Path(directory), components=[A(), B()])
            with self.assertRaises(ComponentDependencyError):
                backend.projects.create("invalid", components=["a"])
            self.assertEqual(backend.projects.list(), [])
            project = backend.projects.create("valid", components=["a", "b"])
            with self.assertRaises(ComponentDependencyError):
                project.components.select(["a"])
            with self.assertRaises(ComponentDependencyError):
                project.save(components=["a"])
            for permanent in (False, True):
                with self.assertRaises(ComponentDependencyError):
                    project.components.remove("b", permanent=permanent)
                self.assertTrue((project.paths.root / "b").is_dir())
            self.assertEqual(project.data.components, ("a", "b"))
            self.assertEqual(backend.project_schema()["x-components"]["a"]["required_components"], ["b"])

    def test_backup_restore_revalidates_dependencies_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = LargeLanguageModel(root / "source", components=[A(), B()])
            project = source.projects.create(components=["a", "b"])
            backup = project.backup(root / "backup")
            changed = B()
            changed.required_components = ("missing",)
            target = LargeLanguageModel(root / "target", components=[A(), changed])
            with self.assertRaises(ComponentDependencyError) as raised:
                target.projects.restore_backup(backup)
            self.assertEqual(raised.exception.missing, {"b": ["missing"]})
            self.assertEqual(target.projects.list(), [])
            source.project_manager.components.get("b").required_components = ("missing",)
            with self.assertRaises(ComponentDependencyError):
                project.backup(root / "invalid-backup")
            self.assertFalse((root / "invalid-backup").exists())

    def test_default_rag_children_validate_semantics_without_invocation(self):
        for key in ("embedding_params", "extraction_params", "rerank_params"):
            for params in ({"model": "   "}, {"api_base": "file:///private"}):
                with self.subTest(child=key, params=params), self.assertRaises(ValueError):
                    RAGComponent().validate_configuration({"config": {key: params}})
