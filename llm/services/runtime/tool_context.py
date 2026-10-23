"""Task-local Tool provenance lifetime; no execution permission or storage policy."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


class ToolCallContext:
    NAME = "llm_tool_call"
    UNBOUND = None
    _CURRENT = ContextVar(NAME, default=UNBOUND)

    @staticmethod
    def current():
        """Return the same detached public snapshot as current_tool_call()."""
        return deepcopy(ToolCallContext._CURRENT.get())

    @staticmethod
    def is_bound():
        return ToolCallContext._CURRENT.get() is not ToolCallContext.UNBOUND

    @staticmethod
    @contextmanager
    def scope(call):
        token = ToolCallContext._CURRENT.set(call)
        try:
            yield
        finally:
            ToolCallContext._CURRENT.reset(token)
