"""Invocation-local output destination selected by the bound builtin Component."""

from contextlib import contextmanager
from contextvars import ContextVar

_output_store = ContextVar("builtin_output_store", default=None)


@contextmanager
def output_scope(store):
    token = _output_store.set(store)
    try:
        yield
    finally:
        _output_store.reset(token)


def current_output_store():
    return _output_store.get()
