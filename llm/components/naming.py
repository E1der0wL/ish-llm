"""Component directory and record identifier declarations, independent of storage."""

import re

class ComponentName:
    PATTERN = r"[A-Za-z0-9_-]{1,64}"
    SCHEMA_PATTERN = "^" + PATTERN + "$"
    INVALID = "Invalid component directory or record ID"

    @classmethod
    def validate(cls, value):
        if not isinstance(value, str) or not re.fullmatch(cls.PATTERN, value):
            raise ValueError(cls.INVALID)
        return value
