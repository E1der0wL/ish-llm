"""Explicit lazy composition. Providers interpret their own settings and capabilities."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .models import ConnectionError, ConnectionState, ConnectionStatus

class Connection(Protocol):
    @property
    def status(self) -> ConnectionStatus: ...
    def resolve[T](self, capability: type[T]) -> T | None: ...


@dataclass(frozen=True, slots=True)
class ConnectionSpec:
    id: str
    provider: str
    enabled: bool
    settings: object
    create: Callable[[], Connection]


class ConnectionRegistry:
    def __init__(self):
        self._specs: dict[str, ConnectionSpec] = {}
        self._instances: dict[str, Connection] = {}
        self.revision = 0

    def configure(self, specs: tuple[ConnectionSpec, ...]) -> None:
        candidates = {spec.id: spec for spec in specs}
        if len(candidates) != len(specs):
            raise ValueError("Connection identifiers must be unique")
        changed = (tuple((key, item.provider, item.enabled, item.settings) for key, item in candidates.items()) !=
                   tuple((key, item.provider, item.enabled, item.settings) for key, item in self._specs.items()))
        if not changed:
            return
        self._instances = {key: instance for key, instance in self._instances.items()
            if key in candidates and candidates[key].enabled and key in self._specs
            and (candidates[key].provider, candidates[key].settings) ==
                (self._specs[key].provider, self._specs[key].settings)}
        self._specs = candidates
        self.revision += 1

    def _connection(self, identifier: str) -> Connection:
        spec = self._specs.get(identifier)
        if spec is None:
            raise ConnectionError(ConnectionState.SETUP, "Connection is not configured")
        if not spec.enabled:
            raise ConnectionError(ConnectionState.DISABLED)
        if identifier not in self._instances:
            self._instances[identifier] = spec.create()
        return self._instances[identifier]

    def resolve[T](self, identifier: str, capability: type[T]) -> T | None:
        return self._connection(identifier).resolve(capability)

    def require[T](self, identifier: str, capability: type[T]) -> T:
        result = self.resolve(identifier, capability)
        if result is None:
            raise ConnectionError(ConnectionState.DENIED, "Connection does not provide this capability")
        return result

    def status(self, identifier: str) -> ConnectionStatus:
        try:
            return self._connection(identifier).status
        except ConnectionError as exc:
            return exc.status
