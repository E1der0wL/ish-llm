"""Linux 실행 계약과 플랫폼에 따른 부작용 발생 전 거부를 검증한다."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.base import validate_name
from llm.components.tools.builtin.files import FileTools
from llm.components.tools.builtin.processes import ProcessTools
from llm.llm import LargeLanguageModel, main
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.runtime.processes import ProcessToolRunner


class LinuxPlatformTests(unittest.TestCase):
    def test_unsupported_platform_rejected_before_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for platform in ("win32", "darwin"):
                with self.subTest(platform=platform), patch("llm._platform.sys.platform", platform):
                    for operation in (
                        lambda: LargeLanguageModel(root / "workspace"),
                        lambda: main(),
                        lambda: WorkspaceOwnership(root / "projects"),
                        lambda: ProcessToolRunner({}, cwd=root, isolation="sandbox", allow_network=False),
                        lambda: ProcessTools(None),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "Linux only"):
                            operation()
                    self.assertEqual(list(root.iterdir()), [])

    def test_linux_file_names_are_not_windows_device_names(self):
        with tempfile.TemporaryDirectory() as directory:
            files = FileTools(Path(directory))
            for name in ("CON", "NUL", "x:stream", "trailing.", "trailing "):
                with self.subTest(name=name):
                    path = files.path(name)
                    path.write_text("linux", encoding="utf-8")
                    self.assertEqual(path.read_text(encoding="utf-8"), "linux")
            self.assertEqual(validate_name("CON"), "CON")
