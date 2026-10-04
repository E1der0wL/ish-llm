"""ish 등록 계약과 기본 Project의 실제 Loop/대화/결과 저장 연결을 검증한다."""

from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import pickle
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from llm.engines.loop import LoopEngine
from examples.llm import ish_loop
from tests.llm.test_loop import ScriptedCompletion, chunk


class IshrcLoopTests(unittest.TestCase):
    def invoke(self, root, provider, *args, key="test-api-key"):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(ish_loop, "LoopEngine", partial(LoopEngine, completion_fn=provider)), \
                patch.dict(os.environ, {"ISH_LLM_API_KEY": key}), \
                redirect_stdout(output), redirect_stderr(errors), \
                self.assertRaises(SystemExit) as exited:
            ish_loop.main(*args, workspace=root, completion={"model": "openai/test-model"})
        return exited.exception.code, output.getvalue(), errors.getvalue()

    def test_default_project_reuse_and_session_history_with_real_loop(self):
        with tempfile.TemporaryDirectory() as root:
            provider = ScriptedCompletion([chunk("안녕하세요"), chunk(finish="stop")])
            code, output, errors = self.invoke(root, provider, "인사해줘")
            self.assertEqual(code, 0, errors)
            self.assertIn("안녕하세요", output)
            self.assertIn("종료 이유: ['stop']", errors)
            session_file = next(Path(root).rglob("session.json"))
            session_id = json.loads(session_file.read_text())["id"]
            provider = ScriptedCompletion([chunk("이전 대화입니다"), chunk(finish="stop")])
            project_id = json.loads(next(Path(root).rglob("project.json")).read_text())["id"]
            code, output, errors = self.invoke(root, provider, "--project-id", project_id, "--session-id", session_id,
                                               "방금", "무슨 말을 했지?", key="updated-key")
            self.assertEqual(code, 0, errors)
            self.assertEqual(len(list(Path(root).rglob("project.json"))), 1)
            self.assertEqual(len(list(Path(root).rglob("session.json"))), 1)
            runs = [json.loads(path.read_text()) for path in Path(root).rglob("run.json")]
            self.assertEqual(len(runs), 2)
            self.assertTrue(all(run["status"] == "completed" for run in runs))
            self.assertTrue(list(Path(root).rglob("step.json")))
            request = provider.requests[0]
            self.assertTrue(request["stream"])
            self.assertEqual(request["api_key"], "updated-key")
            self.assertIn("안녕하세요", [message.get("content") for message in request["messages"]])
            self.assertEqual(request["messages"][-1]["content"], "방금 무슨 말을 했지?")
            project = json.loads(next(Path(root).rglob("project.json")).read_text())
            self.assertEqual(project["conversation_storage"], "file")
            self.assertEqual(project["components"], [])
            self.assertNotIn("api_key", project["config"].get("completion", {}))

    def test_failed_provider_returns_failure_and_persists_partial_response(self):
        with tempfile.TemporaryDirectory() as root:
            provider = ScriptedCompletion([chunk("부분 응답"), RuntimeError("test failure")])
            code, output, errors = self.invoke(root, provider, "테스트")
            self.assertEqual(code, 1)
            self.assertIn("부분 응답", output)
            self.assertIn("test failure", errors)
            run = json.loads(next(Path(root).rglob("run.json")).read_text())
            self.assertEqual(run["status"], "failed")

    def test_rc_registers_callable_importable_in_fresh_worker(self):
        root = Path(__file__).resolve().parents[2]
        prompt, plugin = Mock(), Mock()
        plugin.get.return_value = object()
        fixture = root / "examples/llm/ishrc.example.py"
        runpy.run_path(str(fixture), init_globals={"prompt": prompt, "plugin": plugin})
        prompt.set_tool.assert_called_once()
        self.assertEqual(prompt.set_tool.call_args.args, ("llm-test",))
        function = prompt.set_tool.call_args.kwargs["function"]
        payload = pickle.dumps(function).hex()
        result = subprocess.run([sys.executable, "-c",
            'import pickle,sys; pickle.loads(bytes.fromhex(sys.argv[1]))("--help")', payload],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--session-id", result.stdout)
        plugin.get.return_value = None
        prompt.reset_mock()
        runpy.run_path(str(fixture), init_globals={"prompt": prompt, "plugin": plugin})
        prompt.set_tool.assert_not_called()
