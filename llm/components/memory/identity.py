"""The stored identity of one initialized Memory component."""

import re


class MemoryIdentity:
    COMPONENT = "memory"
    FILENAME = "identity.json"
    ID = "id"
    PATTERN = r"[a-f0-9]{32}"
    INVALID = "Invalid Memory identity"

    @classmethod
    def create(cls, identifier):
        return {cls.ID: identifier}

    @classmethod
    def read(cls, value):
        if (not isinstance(value, dict) or set(value) != {cls.ID}
                or not isinstance(value[cls.ID], str) or not re.fullmatch(cls.PATTERN, value[cls.ID])):
            raise ValueError(cls.INVALID)
        return value[cls.ID]
