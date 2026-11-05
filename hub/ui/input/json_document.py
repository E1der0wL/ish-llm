"""JSON draft presentation, strict parsing and type-sensitive edit comparison."""

import json

from llm.core.json_data import JsonObject, JsonValue


class JsonDocument:
    OBJECT_OPEN = '{'
    OBJECT_CLOSE = '}'
    ITEM_SEPARATOR = ','
    ARRAY_OPEN = '['
    ARRAY_CLOSE = ']'
    SUFFIX = ".json"
    INDENT = 2
    ENSURE_ASCII = False
    ALLOW_NON_FINITE_PREVIEW = True
    STRICT_NON_FINITE = JsonObject.ALLOW_NON_FINITE
    INVALID_CONSTANT = "Invalid JSON: "

    @classmethod
    def render(cls, value, *, allow_non_finite):
        return json.dumps(
            value,
            indent=cls.INDENT,
            ensure_ascii=cls.ENSURE_ASCII,
            allow_nan=allow_non_finite,
        )

    @classmethod
    def _invalid_constant(cls, value):
        raise ValueError(cls.INVALID_CONSTANT + value)

    @classmethod
    def parse(cls, text):
        return json.loads(text, parse_constant=cls._invalid_constant)

    @classmethod
    def locate(cls, text, pointer):
        """Locate the nearest existing JSON-pointer value without rewriting a draft.

        Missing fields and external Workflow paths stop at their visible parent.
        The standard decoder owns JSON syntax, escapes and duplicate-key behavior.
        """
        from llm.core.json_data import JsonPointer

        cls.parse(text)
        decoder = json.JSONDecoder(parse_constant=cls._invalid_constant)
        def space(position):
            while position < len(text) and text[position].isspace():
                position += 1
            return position
        position = space(0)
        for part in JsonPointer.parse(pointer):
            start, candidate = space(position + 1), None
            if text[position] == JsonDocument.OBJECT_OPEN:
                while text[start] != JsonDocument.OBJECT_CLOSE:
                    key, end = decoder.raw_decode(text, start)
                    value_start = space(space(end) + 1)
                    _, end = decoder.raw_decode(text, value_start)
                    if key == part:
                        candidate = value_start
                    start = space(end)
                    if text[start] == JsonDocument.ITEM_SEPARATOR:
                        start = space(start + 1)
                # The last duplicate key is the value used by json.loads.
            elif text[position] == JsonDocument.ARRAY_OPEN and part.isdecimal():
                ordinal = 0
                while text[start] != JsonDocument.ARRAY_CLOSE:
                    _, end = decoder.raw_decode(text, start)
                    if str(ordinal) == part:
                        candidate = start
                        break
                    ordinal += 1
                    start = space(end)
                    if text[start] == JsonDocument.ITEM_SEPARATOR:
                        start = space(start + 1)
            if candidate is None:
                break
            position = candidate
        return position

    @classmethod
    def same(cls, left, right, *, missing):
        if left is missing or right is missing:
            return left is right
        return JsonValue.same(left, right)
