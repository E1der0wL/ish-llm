"""Logical Tool invocation identity; no approval or execution authority."""

import json


class ToolInvocationIdentity:
    SORT_KEYS = True
    ENSURE_ASCII = False

    @classmethod
    def pending(cls, name, arguments):
        return json.dumps([name, arguments], sort_keys=cls.SORT_KEYS, ensure_ascii=cls.ENSURE_ASCII)

    @staticmethod
    def invocation(scope, key):
        return json.dumps([scope, key])
