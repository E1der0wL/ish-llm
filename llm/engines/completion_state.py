"""Transient reception evidence for one model attempt, never persisted retry authority."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True)
class CompletionProgress:
    NOT_RECEIVED: ClassVar[bool] = False
    RECEIVED: ClassVar[bool] = True

    received: bool = NOT_RECEIVED

    def accept_chunk(self):
        self.received = self.RECEIVED


class CompletionInvocationOptions:
    """Observe a caller's streaming options without filling its original mapping."""

    INCLUDE_EVENTS = "include_events"
    PROVIDER = "provider"
    EVENTS_INCLUDED = True
    EVENTS_EXCLUDED = False
    NO_PROVIDER = None

    def __init__(self, value):
        self._value = value

    @property
    def events_excluded(self):
        return self._value.get(self.INCLUDE_EVENTS, self.EVENTS_INCLUDED) is self.EVENTS_EXCLUDED

    @property
    def provider(self):
        return self._value.get(self.PROVIDER)

    def resolved_provider(self):
        from llm.providers.requests import resolve_provider_options
        provider = self.provider
        return resolve_provider_options({} if provider is self.NO_PROVIDER else provider)
