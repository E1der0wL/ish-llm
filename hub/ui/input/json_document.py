"""JSON draft presentation, strict parsing and type-sensitive edit comparison."""

import json

from llm.core.json_data import JsonObject, JsonValue


class JsonDocument:
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
    def same(cls, left, right, *, missing):
        if left is missing or right is missing:
            return left is right
        return JsonValue.same(left, right)
