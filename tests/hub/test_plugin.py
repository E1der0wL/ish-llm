"""Both deployment plugin roots load without development examples or tests."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class PluginTests(unittest.TestCase):
    def test_real_loader_resolves_llm_dependency_from_deployment_tree(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "script"
            for name in ("hub", "llm"):
                shutil.copytree(root / name, scripts / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            code = '''
from pathlib import Path
import sys
from unittest.mock import patch, PropertyMock
from ish.plugin.manager import PluginManager
from ish.config import config
scripts = Path(sys.argv[1])
manager = PluginManager()
with patch.object(type(config), 'PLUGIN_SCRIPT_DIR', new_callable=PropertyMock, return_value=scripts), patch.object(manager, '_ensure_library', return_value=True):
    info = manager._resolve(scripts / 'hub')
assert info is not None
assert info.module.__name__ == 'hub.hub'
assert manager.get('llm') is not None
assert info.module.PLUGIN_META['requirements'] == ['llm']
assert callable(info.module.install)
settings = info.module.HubConfig('/tmp/hub-unused', engine='loop', model='openai/test')
assert settings.engine == 'loop'
assert not any(name.startswith(('examples.', 'tests.')) for name in sys.modules)
'''
            result = subprocess.run([sys.executable, "-c", code, str(scripts)], cwd=directory,
                                    capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                                        (str(scripts), str(root / "ish.platform/src")))))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
