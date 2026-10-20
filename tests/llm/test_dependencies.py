import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.llm.dependencies import ROOT, check, metadata, write_plugin


class DependencyContractTests(unittest.TestCase):
    def copy_inputs(self, directory):
        root = Path(directory)
        for name in ("pyproject.toml", "llm/llm.py", "hub/hub.py",
                     "tests/llm/dependencies-linux.lock"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        (root / "ish.platform").symlink_to(ROOT / "ish.platform", target_is_directory=True)
        return root

    def test_declarations_lock_and_host_projection_agree(self):
        self.assertEqual(check(), [])

    def test_changed_requirement_invalidates_both_projections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_inputs(directory)
            path = root / "pyproject.toml"
            path.write_text(path.read_text().replace("litellm[mcp]>=", "litellm[mcp]=="))
            self.assertEqual(len(check(root)), 2)
            write_plugin(root)
            self.assertEqual(len(check(root)), 1)
            self.assertIn("lock inputs changed", check(root)[0])
            self.assertTrue(any(spec.startswith("litellm[mcp]==")
                                for spec in metadata(root / "llm/llm.py")[0]["dependencies"]))

    def test_host_requirement_change_invalidates_lock(self):
        from tests.llm.dependencies import lock_inputs
        inputs = lock_inputs()
        inputs["requirements"].append("unreviewed-host-dependency")
        with patch("tests.llm.dependencies.lock_inputs", return_value=inputs):
            self.assertIn("lock inputs changed", check()[0])

    def test_installed_version_drift_is_not_success(self):
        with patch("tests.llm.dependencies.version", return_value="0.0.0"):
            errors = check(installed=True)
        self.assertTrue(errors)
        self.assertTrue(any("lock requires" in error for error in errors))
        self.assertTrue(any("declaration requires" in error for error in errors))
