"""Deploy only dok and verify the real ish loader accepts the entry point."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class PluginTests(unittest.TestCase):
    def test_standalone_deployment_loads_without_tests_or_other_plugins(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "script"
            shutil.copytree(root / "dok", scripts / "dok", ignore=shutil.ignore_patterns("__pycache__"))
            code = '''
from pathlib import Path
import sys
from unittest.mock import patch, PropertyMock
from ish.plugin.manager import PluginManager
from ish.config import config
scripts = Path(sys.argv[1])
manager = PluginManager()
with patch.object(type(config), "PLUGIN_SCRIPT_DIR", new_callable=PropertyMock, return_value=scripts):
    info = manager._resolve(scripts / "dok")
assert info is not None
assert info.module.__name__ == "dok.dok"
assert callable(info.module.install)
assert info.module.PLUGIN_META["requirements"] == []
assert not any(name.startswith(("tests.", "hub.", "llm.")) for name in sys.modules)
from dok import DockConfig
assert DockConfig().width.resolve(120, 120) == 80
'''
            result = subprocess.run([sys.executable, "-c", code, str(scripts)], cwd=directory,
                                    capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, PYTHONPATH=os.pathsep.join((str(scripts), str(root / "ish.platform/src")))))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
