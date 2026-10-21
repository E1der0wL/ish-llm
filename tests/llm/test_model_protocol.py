"""Common invocation contracts reject lost evidence before any SDK or Tool call."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from llm.core.model_protocol import (
    ContentKind, ContentPart, ModelMessage, ModelRequest, ModelResponse, ModelRole,
    StopReason, ToolCall, ToolDefinition, ToolMode, ToolResult, copy_model, json_copy,
)


class ModelProtocolTests(unittest.TestCase):
    def exchange(self):
        return [ModelMessage(ModelRole.USER, "original task"),
                ModelMessage(ModelRole.ASSISTANT, calls=(ToolCall("c1", "write", {"text": "value"}),),
                             opaque={"fake": {"signature": "signed"}}),
                ModelMessage(ModelRole.TOOL, result=ToolResult("c1", "stored", "write"))]

    def test_current_records_roundtrip_preserves_falsey_null_and_opaque(self):
        definition = ToolDefinition("write", "", {"type": "object", "properties": {}}, strict=False)
        request = ModelRequest(self.exchange(), {"flag": False, "zero": 0, "nullable": None, "empty": []},
                               (definition,), ToolMode.AUTO)
        restored = ModelRequest.from_dict(json.loads(json.dumps(request.to_dict())))
        self.assertEqual(restored, request)
        restored.validate_exchanges()
        self.assertEqual(restored.messages[1].opaque, {"fake": {"signature": "signed"}})
        self.assertFalse(restored.tools[0].strict)
        response = ModelResponse(ModelMessage(ModelRole.ASSISTANT, ""), StopReason.COMPLETE,
                                 {"total_tokens": 0, "details": {"cached": 0}}, "id", "model", "vendor-end")
        self.assertEqual(ModelResponse.from_dict(json.loads(json.dumps(response.to_dict()))), response)

    def test_missing_extra_or_mistyped_version_is_rejected_without_input_mutation(self):
        values = [ModelMessage(ModelRole.USER, "x"), ModelRequest(self.exchange()),
                  ModelResponse(ModelMessage(ModelRole.ASSISTANT, "done"))]
        for value in values:
            record = value.to_dict()
            invalid = [{k: v for k, v in record.items() if k != field} for field in record]
            invalid += [{**record, "extra": None}]
            invalid += [{**record, "version": version} for version in (True, False, 1.0, "1", None, 0, 2)]
            for damaged in invalid:
                original = deepcopy(damaged)
                with self.subTest(type=type(value).__name__, damaged=damaged), self.assertRaises((TypeError, ValueError)):
                    type(value).from_dict(damaged)
                self.assertEqual(damaged, original)

    def test_nested_optional_fields_cannot_be_recreated_by_reader_defaults(self):
        records = [ContentPart(ContentKind.IMAGE, "image-ref", opaque={"signed": "image"}),
                   ToolCall("c", "write", {}), ToolResult("c", ""),
                   ToolDefinition("write", "", {"type": "object"})]
        for value in records:
            record = value.to_dict()
            for name in record:
                damaged = {k: v for k, v in record.items() if k != name}
                with self.subTest(type=type(value).__name__, field=name), self.assertRaises((TypeError, ValueError)):
                    type(value).from_dict(damaged)
        message = ModelMessage(ModelRole.ASSISTANT, calls=(ToolCall("c", "write", {}),)).to_dict()
        del message["calls"][0]["opaque"]
        with self.assertRaises(ValueError):
            ModelMessage.from_dict(message)
        request = ModelRequest(tools=(ToolDefinition("write", "", {"type": "object"}),)).to_dict()
        del request["tools"][0]["strict"]
        with self.assertRaises(ValueError):
            ModelRequest.from_dict(request)

    def test_old_wire_messages_and_invalid_container_shapes_are_rejected(self):
        for value in ({"role": "assistant", "content": "old", "tool_calls": []}, [], None, False):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                ModelMessage.from_dict(value)
        message = ModelMessage(ModelRole.USER, "x").to_dict()
        for calls in (None, {}, "", False):
            with self.subTest(calls=calls), self.assertRaises((TypeError, ValueError)):
                ModelMessage.from_dict({**message, "calls": calls})
        request = ModelRequest().to_dict()
        for key in ("messages", "tools"):
            with self.subTest(key=key), self.assertRaises(TypeError):
                ModelRequest.from_dict({**request, key: {}})

    def test_exchange_validation_rejects_missing_duplicate_and_interleaved_results(self):
        user, call, result = self.exchange()
        for messages in ([user, call], [user, result], [user, call, result, result],
                         [user, call, user, result], [user, call, call, result],
                         [user, call, result, call, result],
                         [user, call, replace(result, result=ToolResult("other", "value"))]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                ModelRequest(messages).validate_exchanges()
        ModelRequest([user, call]).validate_exchanges(complete=False)
        ModelRequest([user, call, result]).validate_exchanges()

    def test_completed_call_arguments_and_roles_cannot_be_fabricated(self):
        for arguments in ('{"partial":', '{}', [], None, False):
            with self.subTest(arguments=arguments), self.assertRaises((TypeError, ValueError)):
                ToolCall("c", "write", arguments)
        call = ToolCall("c", "write", {})
        for factory in (
            lambda: ModelMessage(ModelRole.USER, calls=(call,)),
            lambda: ModelMessage(ModelRole.ASSISTANT, calls=(call, call)),
            lambda: ModelMessage(ModelRole.TOOL),
            lambda: ModelMessage(ModelRole.TOOL, "duplicate", result=ToolResult("c", "result")),
            lambda: ModelMessage(ModelRole.USER, result=ToolResult("c", "result")),
            lambda: ModelResponse(ModelMessage(ModelRole.USER, "not an answer")),
            lambda: ModelRequest(tools=(ToolDefinition("write", "", {"type": "object"}),) * 2),
        ):
            with self.subTest(factory=factory), self.assertRaises((TypeError, ValueError)):
                factory()

    def test_unknown_usage_stays_unknown_and_false_counts_are_rejected(self):
        message = ModelMessage(ModelRole.ASSISTANT, "done")
        unknown = ModelResponse(message)
        self.assertIsNone(unknown.usage)
        self.assertIsNone(ModelResponse.from_dict(unknown.to_dict()).usage)
        self.assertEqual(ModelResponse(message, usage={}).usage, {})
        self.assertEqual(ModelResponse(message, usage={"total_tokens": 0}).usage, {"total_tokens": 0})
        for counts in ({"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": 1.0},
                       {"details": {"cached": None}}, [], False):
            with self.subTest(counts=counts), self.assertRaises((TypeError, ValueError)):
                ModelResponse(message, usage=counts)

    def test_runtime_copy_detaches_mutable_values_and_preserves_client_identity(self):
        class Client:
            def __deepcopy__(self, memo):
                raise AssertionError("Runtime client must not be cloned")
        client = Client()
        request = ModelRequest(self.exchange(), {"client": client, "options": {"values": [1]}})
        copied = copy_model(request)
        self.assertIs(copied.parameters["client"], client)
        copied.parameters["options"]["values"].append(2)
        copied.messages[1].opaque["fake"]["signature"] = "changed"
        copied.messages[1].calls[0].arguments["text"] = "changed"
        self.assertEqual(request.parameters["options"], {"values": [1]})
        self.assertEqual(request.messages[1].opaque["fake"]["signature"], "signed")
        self.assertEqual(request.messages[1].calls[0].arguments, {"text": "value"})
        with self.assertRaises(ValueError):
            request.to_dict()  # Runtime handles cannot be written into records.

    def test_json_state_rejects_nonfinite_keys_cycles_and_runtime_objects(self):
        cyclic = []; cyclic.append(cyclic)
        for value in (float("nan"), float("inf"), {1: "coerced"}, (1,), object(), cyclic):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                json_copy(value)
        shared = {"values": []}
        self.assertEqual(json_copy([shared, shared]), [{"values": []}, {"values": []}])
        call = ToolCall("c", "write", {})
        call.arguments["bad"] = object()
        with self.assertRaises(ValueError):
            ModelMessage(ModelRole.ASSISTANT, calls=(call,))

    def test_multimodal_and_signed_state_survive_copy_without_silent_text_replacement(self):
        message = ModelMessage(ModelRole.USER,
            (ContentPart(ContentKind.TEXT, "caption"), ContentPart(ContentKind.IMAGE, "private-image",
                "image/png", {"signature": {"parts": ["signed"]}})), opaque={"adapter": "state"})
        copied = copy_model(message)
        self.assertEqual(copied.text, "caption")
        self.assertEqual(copied, message)
        copied.content[1].opaque["signature"]["parts"].append("changed")
        self.assertEqual(message.content[1].opaque["signature"]["parts"], ["signed"])
        with self.assertRaises(ValueError):
            message.with_text("summary")
        call_message = self.exchange()[1]
        changed = call_message.with_text("annotation")
        self.assertEqual(changed.calls, call_message.calls)
        self.assertEqual(changed.opaque, call_message.opaque)
        result = self.exchange()[2]
        self.assertEqual(result.with_text("preview").result, ToolResult("c1", "preview", "write"))

    def test_common_fields_cannot_be_smuggled_through_parameters(self):
        for key in ("messages", "tools"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ModelRequest(parameters={key: []})
        for strict in (0, 1, "false", []):
            with self.subTest(strict=strict), self.assertRaises(TypeError):
                ToolDefinition("write", "", {"type": "object"}, strict=strict)
        self.assertEqual(ModelRequest(tool_mode="none").tool_mode, ToolMode.NONE)
        with self.assertRaises(ValueError):
            ModelRequest(tool_mode="arbitrary")
