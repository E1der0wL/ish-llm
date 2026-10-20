"""Model risk is persisted as classification without granting permission."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from hub.backend.interactions.approval import approval_preset, review_lines
from hub.backend.runtime import HubConfig, HubRuntime, create_backend
from hub.backend.tools.advice import FIELD, MODEL_RISK_SCHEME, bind_advice, classify_model_risk
from hub.locales import Language
from llm.components.tools import Tool, ToolClassification, ToolContract, ToolRegistry
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from llm.components.tools import ToolEffect, ToolIsolation


class ToolAdviceTests(unittest.IsolatedAsyncioTestCase):
    def test_model_classification_levels_and_invalid_assessments(self):
        for level, score in (("low", 20), ("medium", 60), ("high", 100), ("unknown", None)):
            with self.subTest(level=level):
                result = classify_model_risk(NS(arguments={FIELD: {"level": level, "reason": "reason"}}))
                self.assertEqual(result, ToolClassification(risk_scheme=MODEL_RISK_SCHEME, risk=score))
        for value in (None, {}, "low", {"level": "safe", "reason": "x"},
                      {"level": [], "reason": "x"}, {"level": "low", "reason": " "}):
            with self.subTest(value=value):
                self.assertIsNone(classify_model_risk(NS(arguments={FIELD: value})).risk)

    async def test_explicit_host_classifier_keeps_precedence(self):
        classifier = lambda call: ToolClassification(category="host.action", risk_scheme="host", risk=90)
        with tempfile.TemporaryDirectory() as root:
            backend = create_backend(HubConfig(root, auto_title=False, tool_classifier=classifier))
            self.assertIs(backend.services.tool_runtime.classify, classifier)

    async def test_advice_is_validated_and_removed_only_at_execution(self):
        operation = AsyncMock(return_value="done")
        toolkit = NS(registry=ToolRegistry((Tool("file_create", "Create", {
            "type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"], "additionalProperties": False}, operation,
            contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=())),)))
        bind_advice(toolkit)
        arguments = {"path": "file.txt", FIELD: {"level": "low", "reason": "Creates one new local file."}}
        toolkit.registry.prepare("file_create", json.dumps(arguments))
        await toolkit.registry.get("file_create").handler(arguments)
        operation.assert_awaited_once_with({"path": "file.txt"})
        self.assertIn(FIELD, arguments)
        with self.assertRaises(ValueError):
            toolkit.registry.prepare("file_create", json.dumps({**arguments, FIELD: {"level": "safe", "reason": "x"}}))

    async def test_low_model_advice_still_requires_approval_and_uses_no_extra_completion(self):
        advice = {"level": "low", "reason": "새 파일 한 개를 만들며 기존 파일을 덮어쓰지 않습니다."}
        args = {"path": "reviewed.txt", "content": "created", FIELD: advice}
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps(args), name="file_create")], finish="tool_calls")],
            [chunk("Done", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(Path(root) / "data", file_root=root, auto_title=False,
                model="test/model", engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)},
                project_config={"policies": {"approval": approval_preset(max_risk=100, categories=["tool.execute"])}}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "Create a file")
                await runtime.sessions[sid].run.wait_idle()
                waiting = await runtime.snapshot()
                self.assertEqual(waiting.status, "waiting_approval")
                item = waiting.questions[0]
                self.assertEqual(item["request"]["risk"], 20)
                self.assertEqual(item["request"]["risk_scheme"], MODEL_RISK_SCHEME)
                self.assertEqual(item["request"]["action"]["classification"]["risk"], 20)
                text = "\n".join(review_lines(item["request"], Language()))
                self.assertIn("위험도: low (20)", text)
                self.assertIn("모델 평가 근거", text)
                self.assertNotIn("참고", text)
                self.assertIn(advice["reason"], text)
                self.assertFalse(Path(root, "reviewed.txt").exists())
                self.assertEqual(len(provider.requests), 1)
                self.assertIn("_hub_risk", json.dumps(provider.requests[0]["messages"]))
                await runtime.answer_question(runtime.project.id, sid, item["run_id"], item["request"], "approve", None)
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(Path(root, "reviewed.txt").read_text(), "created")
            finally:
                await runtime.close()
