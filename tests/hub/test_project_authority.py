"""Hub의 위험 표시·프리셋·의존성 안내가 Backend 정책을 대체하지 않는지 검사한다."""
import tempfile
import unittest
from pathlib import Path

from hub.backend.approval import RISK_SCHEME, approval_preset, risk_label
from llm.core.models import ProjectConfig
from llm.components.registry import ComponentDependencyError
from llm.llm import LargeLanguageModel
from tests.llm.test_component_dependencies import A, B


class ProjectAuthorityTests(unittest.TestCase):
    def test_risk_presentation_and_explicit_preset(self):
        self.assertEqual(risk_label(None, None), "unknown")
        self.assertEqual(risk_label("foreign", 7), "foreign: 7")
        self.assertEqual(risk_label(RISK_SCHEME, 20), "low (20)")
        self.assertEqual(risk_label(RISK_SCHEME, 61), "high (61)")
        policy = approval_preset(max_risk=20, categories=["file.read"])
        config = ProjectConfig(policies={"approval": policy})
        self.assertEqual(config.policies["approval"], policy)
        self.assertEqual(ProjectConfig().policies, {})
        with self.assertRaises(ValueError):
            approval_preset(max_risk=True, categories=[])

    def test_dependency_catalog_and_atomic_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LargeLanguageModel(Path(directory), components=[A(), B()])
            schema = app.describe_project_config()
            self.assertEqual(schema["x-components"]["a"]["required_components"], ["b"])
            project = app.projects.create(components=["a", "b"])
            with self.assertRaises(ComponentDependencyError) as error:
                project.save(components=["a"])
            self.assertEqual(error.exception.code, "component_dependency_missing")
            self.assertEqual(error.exception.missing, {"a": ["b"]})
            self.assertEqual(project.data.components, ("a", "b"))
