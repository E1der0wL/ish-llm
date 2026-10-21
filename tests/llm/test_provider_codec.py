"""LiteLLM-only wire translation preserves evidence without creating Tool authority."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from llm.core.model_protocol import (
    ContentKind, ContentPart, ModelMessage, ModelRequest, ModelRole, StopReason,
    ToolCall, ToolDefinition, ToolMode,
)
from llm.providers.wire_codec import (
    decode_message, decode_tool_definition, encode_message, encode_request,
    encode_tool_definition, normalize_stop_reason,
)


class ProviderCodecTests(unittest.TestCase):
    def call(self, text=' { "flag": false, "text": "한글" } '):
        return {"id": "signed-call-id", "type": "function", "signature": {"value": "signed"},
                "function": {"name": "write", "arguments": text, "provider_extension": ["keep"]}}

    def test_message_call_and_result_roundtrip_retains_exact_wire_evidence(self):
        messages = [
            {"role": "assistant", "content": None, "reasoning_content": "public reasoning",
             "provider_specific_fields": {"signature": "sealed"}, "tool_calls": [self.call()]},
            {"role": "tool", "content": "stored", "tool_call_id": "signed-call-id", "name": "write",
             "vendor_result": {"seal": "keep"}},
            {"role": "user", "content": "task", "name": "author", "extension": False},
        ]
        for value in messages:
            original = deepcopy(value)
            with self.subTest(value=value):
                common = decode_message(value)
                self.assertEqual(encode_message(common), value)
                self.assertEqual(value, original)
        common = decode_message(messages[0])
        common.calls[0].opaque["litellm"]["extra"]["signature"]["value"] = "changed"
        self.assertEqual(messages[0]["tool_calls"][0]["signature"], {"value": "signed"})

    def test_missing_null_and_empty_wire_shapes_remain_distinct(self):
        for value in ({"role": "assistant"}, {"role": "assistant", "content": None},
                      {"role": "assistant", "content": ""},
                      {"role": "assistant", "tool_calls": None}, {"role": "assistant", "tool_calls": []},
                      {"role": "tool", "content": "", "tool_call_id": "c", "name": None},
                      {"role": "tool", "content": "", "tool_call_id": "c", "tool_calls": None},
                      {"role": "tool", "content": "", "tool_call_id": "c", "tool_calls": []}):
            with self.subTest(value=value):
                self.assertEqual(encode_message(decode_message(value)), value)
        call = self.call()
        del call["type"]
        value = {"role": "assistant", "tool_calls": [call]}
        self.assertEqual(encode_message(decode_message(value)), value)

    def test_malformed_falsey_calls_and_incomplete_arguments_are_rejected(self):
        for calls in (False, 0, "", {}):
            with self.subTest(calls=calls), self.assertRaises((TypeError, ValueError)):
                decode_message({"role": "assistant", "tool_calls": calls})
        for text in ('{"partial":', '[]', 'null', 'true', '{"x":NaN}', '{"x":1e999}'):
            with self.subTest(text=text), self.assertRaises((TypeError, ValueError)):
                decode_message({"role": "assistant", "tool_calls": [self.call(text)]})
        for call in ({"id": "c"}, {**self.call(), "type": "other"}, {**self.call(), "id": ""}):
            with self.subTest(call=call), self.assertRaises((TypeError, ValueError)):
                decode_message({"role": "assistant", "tool_calls": [call]})

    def test_argument_text_reuse_compares_json_types_not_python_equality(self):
        for before, after in ((False, 0), (True, 1), (1, 1.0)):
            with self.subTest(before=before, after=after):
                message = decode_message({"role": "assistant", "tool_calls": [self.call(json.dumps({"x": before}))]})
                call = replace(message.calls[0], arguments={"x": after})
                encoded = encode_message(replace(message, calls=(call,)))
                value = json.loads(encoded["tool_calls"][0]["function"]["arguments"])["x"]
                self.assertEqual(value, after)
                self.assertIs(type(value), type(after))
        text = ' {"b": 2, "a": 1} '
        message = decode_message({"role": "assistant", "tool_calls": [self.call(text)]})
        call = replace(message.calls[0], arguments={"a": 1, "b": 2})
        self.assertEqual(encode_message(replace(message, calls=(call,)))["tool_calls"][0]["function"]["arguments"], text)

    def test_opaque_fields_cannot_inject_common_calls_or_identity(self):
        for reserved in ("role", "content", "tool_calls", "tool_call_id"):
            message = ModelMessage(ModelRole.ASSISTANT, "safe", opaque={"litellm": {reserved: "injected"}})
            with self.subTest(reserved=reserved), self.assertRaises(ValueError):
                encode_message(message)
        message = decode_message({"role": "assistant", "tool_calls": [self.call()]})
        for section, field in (("extra", "id"), ("extra", "function"), ("function_extra", "arguments")):
            changed = deepcopy(message)
            changed.calls[0].opaque["litellm"][section][field] = "injected"
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                encode_message(changed)

    def test_required_adapter_state_is_not_rebuilt_after_field_loss(self):
        original = decode_message({"role": "assistant", "tool_calls": [self.call()]})
        for field in ("arguments_text", "type_missing", "extra", "function_extra"):
            message = deepcopy(original)
            del message.calls[0].opaque["litellm"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                encode_message(message)
        definition = decode_tool_definition({"type": "function", "function": {
            "name": "write", "parameters": {"type": "object"}, "strict": None}})
        for field in ("description_missing", "strict_present", "outer", "function"):
            changed = deepcopy(definition)
            del changed.opaque["litellm"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                encode_tool_definition(changed)

    def test_multimodal_parts_preserve_signed_unknown_content_and_edits(self):
        value = {"role": "user", "content": [
            {"type": "text", "text": "caption", "cache_control": {"type": "ephemeral"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "high"}},
            {"type": "vendor_signed", "signature": "sealed", "payload": {"x": False}},
        ]}
        message = decode_message(value)
        self.assertEqual(encode_message(message), value)
        self.assertEqual(message.text, "caption")
        edited = replace(message, content=(replace(message.content[0], value="edited"), *message.content[1:]))
        expected = deepcopy(value); expected["content"][0]["text"] = "edited"
        self.assertEqual(encode_message(edited), expected)
        self.assertEqual(value["content"][0]["text"], "caption")

    def test_malformed_or_lost_content_state_is_rejected(self):
        for part in ({}, {"type": False}, {"type": "text"}, {"type": "image_url", "image_url": {}}):
            with self.subTest(part=part), self.assertRaises((TypeError, ValueError)):
                decode_message({"role": "user", "content": [part]})
        message = decode_message({"role": "user", "content": [{"type": "text", "text": "original"}]})
        del message.content[0].opaque["litellm"]["text"]
        with self.assertRaises(ValueError):
            encode_message(message)
        with self.assertRaises(ValueError):
            encode_message(ModelMessage(ModelRole.USER, (ContentPart(ContentKind.AUDIO, "audio"),)))

    def test_tool_definition_extensions_and_presence_roundtrip(self):
        for fields in ({}, {"description": ""}, {"strict": None}, {"strict": False}, {"strict": True}):
            value = {"type": "function", "outer_extension": ["keep"], "function": {
                "name": "write", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                "function_extension": {"signature": "keep"}, **fields}}
            with self.subTest(fields=fields):
                self.assertEqual(encode_tool_definition(decode_tool_definition(value)), value)
        definition = ToolDefinition("write", "", {"type": "object"}, strict=False)
        self.assertIs(encode_tool_definition(definition)["function"]["strict"], False)

    def test_request_encoding_preserves_clients_and_does_not_mutate_common_values(self):
        class Client:
            def __deepcopy__(self, memo):
                raise AssertionError("Client identity must be retained")
        client = Client()
        request = ModelRequest([ModelMessage(ModelRole.USER, "task")],
            {"client": client, "vendor_option": {"values": [False, 0, None]}},
            (ToolDefinition("write", "", {"type": "object"}),), ToolMode.REQUIRED)
        encoded = encode_request(request)
        self.assertIs(encoded["client"], client)
        self.assertEqual(encoded["messages"], [{"role": "user", "content": "task"}])
        self.assertEqual(encoded["tool_choice"], "required")
        self.assertEqual(encoded["tools"][0]["function"]["name"], "write")
        encoded["vendor_option"]["values"].append("changed")
        self.assertEqual(request.parameters["vendor_option"]["values"], [False, 0, None])
        with self.assertRaises(TypeError):
            encode_request({"messages": []})
        with self.assertRaises(ValueError):
            encode_request(ModelRequest(parameters={"tool_choice": "auto"}, tool_mode=ToolMode.REQUIRED))

    def test_normalized_finish_is_separate_from_raw_provider_reason(self):
        for reason, expected in (("stop", StopReason.COMPLETE), ("tool_calls", StopReason.TOOLS),
                                 ("length", StopReason.OUTPUT_LIMIT), ("content_filter", StopReason.BLOCKED),
                                 (None, StopReason.UNKNOWN), ("vendor-stop", StopReason.UNKNOWN)):
            with self.subTest(reason=reason):
                self.assertEqual(normalize_stop_reason(reason), expected)
        with self.assertRaises(TypeError):
            normalize_stop_reason(False)
