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
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / 'script'
            shutil.copytree(root / 'dok', scripts / 'dok', ignore=shutil.ignore_patterns('__pycache__'))
            code = '\nfrom pathlib import Path\nimport sys\nfrom unittest.mock import patch, PropertyMock\nfrom ish.plugin.manager import PluginManager\nfrom ish.config import config\nscripts = Path(sys.argv[1])\nmanager = PluginManager()\nwith patch.object(type(config), "PLUGIN_SCRIPT_DIR", new_callable=PropertyMock, return_value=scripts):\n    info = manager._resolve(scripts / "dok")\nassert info is not None\nassert info.module.__name__ == "dok.dok"\nassert callable(info.module.install)\nassert info.module.PLUGIN_META["requirements"] == []\nassert not any(name.startswith(("tests.", "hub.", "llm.")) for name in sys.modules)\nimport dok\nfrom dok import DockConfig\nassert DockConfig().dock.width.resolve(120, 120) == 80\nassert DockConfig is info.module.DockConfig\nassert all(hasattr(dok, name) for name in dok.__all__)\nassert info.module.AudioConfig is dok.AudioConfig\nassert info.module.ClockConfig is dok.ClockConfig\nassert info.module.ConfigStore is dok.ConfigStore\n'
            result = subprocess.run(
                [sys.executable, '-c', code, str(scripts)],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=30,
                env=dict(
                    os.environ,
                    PYTHONPATH=os.pathsep.join((str(scripts), str(root / 'ish.platform/src'), os.environ.get('PYTHONPATH', '')))
                )
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
