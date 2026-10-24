"""Loop execution binding snapshot and deterministic digest; no policy or compatibility defaults."""

import hashlib
import json
from llm.providers.streaming import CompletionAdapterContract


class LoopExecutionBinding:
    RUNTIME_TYPE = "runtime_type"
    TYPE_SEPARATOR = "."
    SETTINGS = "settings"
    COMPLETION = "completion"
    PROMPT = "prompt"
    AGENT = "agent"
    ADAPTER = "adapter"
    ADAPTER_TYPE = "type"
    ADAPTER_BINDING = "binding"
    FINAL_REVIEWERS = "final_reviewers"
    SORT_KEYS = True
    ENSURE_ASCII = False
    ALLOW_NONFINITE = False
    ABSENT = None

    @classmethod
    def portable(cls, value):
        if isinstance(value, dict):
            return {k: cls.portable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls.portable(v) for v in value]
        if value is cls.ABSENT or isinstance(value, (str, int, float, bool)):
            return value
        return {cls.RUNTIME_TYPE: type(value).__module__ + cls.TYPE_SEPARATOR + type(value).__qualname__}

    @classmethod
    def adapter_identity(cls, adapter):
        return {
            cls.ADAPTER_TYPE: type(adapter).__module__ + cls.TYPE_SEPARATOR + type(adapter).__qualname__,
            cls.ADAPTER_BINDING: CompletionAdapterContract.execution_binding(adapter),
        }

    @classmethod
    def create(cls, configuration, completion, prompt, agent, adapter, tools):
        return {
            cls.SETTINGS: configuration,
            cls.COMPLETION: completion,
            cls.PROMPT: prompt,
            cls.AGENT: agent,
            cls.ADAPTER: adapter,
            **tools,
        }

    @classmethod
    def attach_reviewers(cls, value, reviewers):
        value[cls.FINAL_REVIEWERS] = reviewers

    @classmethod
    def digest(cls, value):
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=cls.SORT_KEYS, ensure_ascii=cls.ENSURE_ASCII, allow_nan=cls.ALLOW_NONFINITE
            ).encode()
        ).hexdigest()
