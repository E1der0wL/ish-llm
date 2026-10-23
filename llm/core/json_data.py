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
