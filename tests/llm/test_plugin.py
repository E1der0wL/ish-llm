"""Offline entry point and real ish plugin-loader contract checks."""

import ast
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from llm import llm as entry
from llm.core.results import EngineDelta
from llm.services.infrastructure.locking import WorkspaceOwnership
from tests.llm.support.fake_engine import FakeStreamingEngine


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "ish.platform" / "src"


class PluginEntryTests(unittest.TestCase):
    def test_plugin_import_and_cli_without_development_directories(self):
        """배포할 llm만 있어도 새 worker가 테스트/예제 없이 진입점을 불러온다."""
        for name in ("tests", "examples"):
            self.assertFalse((ROOT / "llm" / name).exists())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "llm", target / "llm",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import llm
from llm.llm import main, LargeLanguageModel, LoopEngine, GraphEngine
assert Path(llm.__file__).resolve().parent == Path(sys.argv[1]) / 'llm'
assert not any(n == 'tests' or n.startswith('tests.') or n == 'examples'
               or n.startswith('examples.') for n in sys.modules)
main('--help')
'''
            result = subprocess.run([sys.executable, "-I", "-c", code, str(target)],
                                    cwd=target, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--engine", result.stdout)

    def test_metadata_is_a_literal_at_entry_point(self):
        tree = ast.parse(Path(entry.__file__).read_text(encoding="utf-8"))
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "PLUGIN_META"
                                  for t in n.targets))
        meta = ast.literal_eval(assignment.value)
        self.assertEqual(meta, entry.PLUGIN_META)
        self.assertEqual(meta["name"], "llm")
        self.assertEqual(meta["requirements"], [])
        self.assertIn("rank-bm25|rank_bm25", meta["dependencies"])
        self.assertIn("chromadb", meta["dependencies"])
        self.assertIn("kuzu", meta["dependencies"])
        self.assertIn("langgraph>=1.2.12,<2", meta["dependencies"])

    def test_explicit_worker_arguments_and_exit_status(self):
        for result in (0, 1, 130):
            with self.subTest(result=result), \
                    patch.object(sys, "argv", ["ish", "bash", "--host-option"]), \
                    patch.object(entry, "run_request", AsyncMock(return_value=result)) as run:
                with self.assertRaises(SystemExit) as exited:
                    entry.main("--engine", "loop", "--model", "test/model", "--prompt", "hello world")
                self.assertEqual(exited.exception.code, result)
                self.assertEqual(run.await_args.args[0].prompt, "hello world")
                self.assertEqual(run.await_args.args[0].model, "test/model")

    def test_missing_arguments_do_not_consume_host_argv(self):
        with patch.object(sys, "argv", ["ish", "--model", "test", "--prompt", "host"]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exited:
            entry.main()
        self.assertEqual(exited.exception.code, 2)

    def test_cli_requires_engine_and_does_not_start_a_request_when_omitted(self):
        with patch.object(entry, "run_request", AsyncMock()) as run, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exited:
            entry.main("--model", "test/model", "--prompt", "hello")
        self.assertEqual(exited.exception.code, 2)
        run.assert_not_called()

    def test_cli_streaming_persistence_failure_and_shutdown(self):
        for fail_after in (None, 1):
            with self.subTest(fail_after=fail_after), tempfile.TemporaryDirectory() as tmp:
                output, errors = io.StringIO(), io.StringIO()
                engine = FakeStreamingEngine(chunks=("hello", " world"), fail_after=fail_after)
                with patch.object(entry, "LoopEngine", return_value=engine) as build_engine, \
                        redirect_stdout(output), redirect_stderr(errors), \
                        self.assertRaises(SystemExit) as exited:
                    entry.main("--engine", "loop", "--model", "test/model", "--prompt", "hello",
                               "--workspace", tmp)
                self.assertEqual(exited.exception.code, 0 if fail_after is None else 1)
                build_engine.assert_called_once_with()  # 생략된 CLI 옵션을 명시적 None으로 만들지 않는다.
                self.assertIn("hello", output.getvalue())
                runs = list(Path(tmp).rglob("run.json"))
                self.assertEqual(len(runs), 1)
                run = json.loads(runs[0].read_text(encoding="utf-8"))
                self.assertEqual(run["status"], "completed" if fail_after is None else "failed")
                # Shutdown releases the workspace even when the Engine fails.
                with WorkspaceOwnership(Path(tmp) / "projects").scope():
                    pass

    @unittest.skipUnless((HOST / "ish" / "plugin" / "manager.py").is_file(),
                         "local ish.platform checkout is unavailable")
    def test_real_platform_loader_and_fresh_worker_import(self):
        # Use a copied deployment tree outside the repository so imports cannot
        # accidentally succeed only because the source checkout is the cwd.
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "script"
            shutil.copytree(ROOT / "llm", scripts / "llm",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            code = '''
import os, pickle, subprocess, sys
from pathlib import Path
from unittest.mock import patch
import ish
from ish.plugin.manager import PluginManager
manager = PluginManager()
directory = Path(sys.argv[1]) / 'llm'
assert manager._get_plugin_entry_point(directory).name == 'llm.py'
meta = manager._get_plugin_meta(directory / 'llm.py')
# 현재 호스트는 Python 의존성과 ish 플러그인 요구 사항을 별도로 파싱한다.
with patch('ish.plugin.manager.has_duplicate_metadata', return_value=False), patch('ish.plugin.manager.has_import', return_value=True) as has_import:
    assert manager._library_available('rank-bm25|rank_bm25', import_parents=False)
has_import.assert_called_once_with('rank_bm25')
# No package installs or network requests in a contract test.
with patch.object(manager, '_ensure_library', return_value=True) as ensure:
    info = manager._resolve(directory)
assert info is not None
assert info.module.__name__ == 'llm.llm'
assert manager.get('llm') is info.module
LargeLanguageModel = manager.get('llm').LargeLanguageModel
from llm.engines.base import EngineEvent, EngineEventType
from llm.core.results import EngineDelta
import asyncio
class Echo:
    async def execute(self, context):
        yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("test-output", 'plugin backend'))
async def exercise_backend():
    async with LargeLanguageModel('backend-workspace', engines={'loop': Echo()}) as backend:
        project = backend.projects.create()
        session = project.sessions.create()
        await project.sessions.load(session.id).run.submit('hello', engine='loop')
        await session.run.wait_idle()
        assert session.run.list()[0].data.status.value == 'completed'
        assert session.conversation()[-1].content == 'plugin backend'
asyncio.run(exercise_backend())
assert set(c.args[0] for c in ensure.call_args_list) == set(meta['dependencies'])
assert Path(ish.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
payload = pickle.dumps(info.module.main).hex()
worker = subprocess.run([sys.executable, '-c',
    'import pickle, sys; pickle.loads(bytes.fromhex(sys.argv[1]))("--help")', payload],
    capture_output=True, text=True, timeout=30)
assert worker.returncode == 0, worker.stderr
assert '--model' in worker.stdout, worker.stdout
# Missing dependencies must prevent registration.
other = PluginManager()
with patch.object(other, '_ensure_library', return_value=False):
    assert other._resolve(directory) is None
assert other.get('llm') is None
'''
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(scripts), str(HOST))))
            result = subprocess.run([sys.executable, "-c", code, str(scripts), str(HOST)],
                                    cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
