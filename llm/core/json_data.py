"""JSON object syntax validation; domain fields and policies belong to their Owners."""

import json


class JsonObject:
    """Validate an open object without coercion, projection, defaults or mutation."""

    INVALID_OBJECT = "JSON data must be a dictionary"
    INVALID_KEY = "JSON keys must be strings"
    INVALID_VALUE = "JSON data must contain only JSON values"
    ALLOW_NON_FINITE = False
    NULL = None
    SCALARS = (str, bool, int, float)

    @classmethod
    def validate(cls, value: dict) -> None:
        if not isinstance(value, dict):
            raise TypeError(cls.INVALID_OBJECT)

        def check(item):
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise TypeError(cls.INVALID_KEY)
                    check(nested)
            elif isinstance(item, list):
                for nested in item:
                    check(nested)
            elif item is not cls.NULL and not isinstance(item, cls.SCALARS):
                raise TypeError(cls.INVALID_VALUE)

        check(value)
        json.dumps(value, allow_nan=cls.ALLOW_NON_FINITE)


class JsonValue:
    """Exact JSON representation comparison; callers retain their own validation."""

    SORT_KEYS = True
    ALLOW_NONFINITE = JsonObject.ALLOW_NON_FINITE

    @classmethod
    def same(cls, left, right):
        return json.dumps(left, sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NONFINITE) == json.dumps(
            right, sort_keys=cls.SORT_KEYS, allow_nan=cls.ALLOW_NONFINITE)


class JsonPointer:
    FRAGMENT = "#"
    ROOT = ""
    SEPARATOR = "/"
    ESCAPE = "~"
    ESCAPED_ESCAPE = "~0"
    ESCAPED_SEPARATOR = "~1"
    INVALID_PATH = "Binding path must be a JSON Pointer"
    INVALID_ESCAPE = "Invalid JSON Pointer escape"

    @classmethod
    def append(cls, path, key):
        return path + cls.SEPARATOR + key.replace(cls.ESCAPE, cls.ESCAPED_ESCAPE).replace(
            cls.SEPARATOR, cls.ESCAPED_SEPARATOR)

    @classmethod
    def parts(cls, path):
        return tuple(part.replace(cls.ESCAPED_SEPARATOR, cls.SEPARATOR).replace(
            cls.ESCAPED_ESCAPE, cls.ESCAPE) for part in path.lstrip(cls.SEPARATOR).split(cls.SEPARATOR))

    @classmethod
    def parse(cls, path):
        """Strict RFC 6901 parsing for persisted bindings, including the root."""
        if not isinstance(path, str) or (path and not path.startswith(cls.SEPARATOR)):
            raise ValueError(cls.INVALID_PATH)
        parts = path[len(cls.SEPARATOR):].split(cls.SEPARATOR) if path else []
        if any(cls.ESCAPE in part.replace(cls.ESCAPED_SEPARATOR, cls.ROOT).replace(
                cls.ESCAPED_ESCAPE, cls.ROOT) for part in parts):
            raise ValueError(cls.INVALID_ESCAPE)
        return [part.replace(cls.ESCAPED_SEPARATOR, cls.SEPARATOR).replace(
            cls.ESCAPED_ESCAPE, cls.ESCAPE) for part in parts]

    @classmethod
    def reference(cls, parts):
        path = cls.ROOT
        for part in parts:
            path = cls.append(path, part)
        return cls.FRAGMENT + path
