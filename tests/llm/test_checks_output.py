import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from tests.llm.checks_output import ANNOTATION_BYTES, emit_annotation


class ChecksOutputTests(unittest.TestCase):
    def emit(self, payload):
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(output):
            emit_annotation("Fixture", payload, level="notice")
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("::notice title=Fixture::"))
        message = lines[0].split("::", 2)[2].replace("%0A", "\n").replace("%0D", "\r").replace("%25", "%")
        self.assertLessEqual(len(message.encode("utf-8")), ANNOTATION_BYTES)
        return json.loads(message)

    def test_counts_and_command_like_trace_preserve_json(self):
        payload = {"passed": 1, "failed": 0, "trace": "50%\n::error::not another command"}
        self.assertEqual(self.emit(payload), payload)

    def test_long_unicode_trace_is_explicitly_truncated_without_breaking_json(self):
        payload = self.emit({"trace": "실패 증거\n" * 5000})
        self.assertTrue(payload["truncated"])
        self.assertIn("실패", payload["excerpt"])
