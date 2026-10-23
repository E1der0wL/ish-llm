"""Additional-instruction event protocol shared by Engines and its service Owner."""

from copy import deepcopy
from dataclasses import dataclass, fields
from enum import StrEnum

from llm.core.json_data import JsonObject
from .steering import SteeringTarget, SteeringRoute, RunInstruction


class SteeringOperation(StrEnum):
    OPEN = "open"
    CLOSE = "close"
    ROUTES = "routes"
    BIND = "bind"
    SELECT = "select"
    APPLY = "apply"


class SteeringReason(StrEnum):
    COMPLETED = "completed"
    NODE_NOT_REACHED = "node_not_reached"
    CLONED = "cloned"


class SteeringEventData:
    """One wire Reader with typed consumption, preserving optional fields and extensions.

    This event is a request for a durable boundary, never an acknowledgement or
    permission. Current channel, scope, record and receipt validation stays with
    the service transaction that handles the request.
    """

    class Field(StrEnum):
        OPERATION = "operation"
        TARGET_ID = "target_id"
        REQUEST_ID = "request_id"
        TARGET = "target"
        ROUTES = "routes"
        ROUTE = "route"
        SCOPE = "scope"
        REASON = "reason"
        CHECKPOINT = "checkpoint"
        BOUNDARY = "boundary"
        ENVELOPE = "envelope"
        ALLOW_CONTINUE = "allow_continue"
        FINAL = "final"
        DETAILS = "details"

    def __init__(self, value):
        JsonObject.validate(value)
        self._value = deepcopy(value)

    @classmethod
    def request(cls, operation, target_id, request_id, options):
        return cls({**options, cls.Field.OPERATION: operation,
                    cls.Field.TARGET_ID: target_id, cls.Field.REQUEST_ID: request_id})

    def to_dict(self):
        return deepcopy(self._value)

    def scoped(self, checkpoint, boundary, envelope):
        return type(self)({**self._value, self.Field.CHECKPOINT: checkpoint,
                          self.Field.BOUNDARY: boundary, self.Field.ENVELOPE: deepcopy(envelope)})

    @property
    def operation(self):
        return SteeringOperation(self._value[self.Field.OPERATION])

    @property
    def target_id(self):
        return self._value[self.Field.TARGET_ID]

    @property
    def request_id(self):
        return self._value[self.Field.REQUEST_ID]

    @property
    def target(self):
        return SteeringTarget.from_dict(self._value[self.Field.TARGET])

    @property
    def routes(self):
        return tuple(SteeringRoute.from_dict(value) for value in self._value[self.Field.ROUTES])

    @property
    def route(self):
        return SteeringRoute.from_dict(self._value[self.Field.ROUTE])

    @property
    def scope(self):
        return self._value[self.Field.SCOPE]

    @property
    def reason(self):
        return self._value.get(self.Field.REASON)

    @property
    def required_reason(self):
        return self._value[self.Field.REASON]

    @property
    def checkpoint(self):
        return self._value[self.Field.CHECKPOINT]

    @property
    def boundary(self):
        return self._value[self.Field.BOUNDARY]

    @property
    def envelope(self):
        return deepcopy(self._value.get(self.Field.ENVELOPE))

    @property
    def allow_continue(self):
        return self._value[self.Field.ALLOW_CONTINUE]

    @property
    def final(self):
        return self._value[self.Field.FINAL]

    @property
    def details(self):
        return deepcopy(self._value.get(self.Field.DETAILS))


@dataclass(frozen=True, slots=True)
class SteeringNotification:
    """Partial change notification: omitted collections do not mean empty state."""

    instructions: tuple[RunInstruction, ...] | None = None
    targets: tuple[SteeringTarget, ...] | None = None
    routes: tuple[SteeringRoute, ...] | None = None

    def __post_init__(self):
        for item in fields(self):
            values = getattr(self, item.name)
            if values is not None:
                object.__setattr__(self, item.name, deepcopy(tuple(values)))

    def to_dict(self):
        return {item.name: [value.to_dict() for value in getattr(self, item.name)]
                for item in fields(self) if getattr(self, item.name) is not None}
