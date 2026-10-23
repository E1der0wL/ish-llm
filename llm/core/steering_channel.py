"""Run-owned instruction channel; reservations and execution targets share one Reader."""

from copy import deepcopy
from dataclasses import replace
from enum import StrEnum

from .steering import SteeringTarget, SteeringRoute, SteeringMode, InstructionDataError
from .continuations import ResumeRecord


class SteeringChannel:
    KEY = "steering"
    ROOT = "root"
    ACCEPTING = True
    CLOSED = False
    UNDECLARED = False
    DECLARED = True
    INVALID = "run reservation channel"
    INVALID_ROUTES = "run reservation routes"
    INVALID_ROUTE = "run reservation route"
    INVALID_ROUTE_KEY = "run reservation route key"
    INVALID_BINDINGS = "run reservation bindings"
    INVALID_TARGETS = "run targets"
    INVALID_OWNERSHIP = "run target ownership/scope"
    ROUTES_ONCE = "Reservation routes must be declared once by the forwarding Run"
    DUPLICATE_ROUTES = "Ambiguous reservation routes"
    INVALID_BINDING = "Invalid or repeated reservation execution binding"
    INVALID_REGISTRATION = "Invalid instruction target registration"
    TARGET_CLOSED = "Instruction target is closed"
    NOT_CONSUMING = "Instruction target does not consume input"

    class Field(StrEnum):
        ACCEPTING = "accepting"
        MODE = "mode"
        INPUT_MESSAGE_ID = "input_message_id"
        TARGETS = "targets"
        ROOT_OPENED = "root_opened"
        ROUTES = "routes"
        BINDINGS = "bindings"
        ROUTES_DECLARED = "routes_declared"
        UNAPPLIED_REASON = "unapplied_reason"

    def __init__(self, value, run):
        if not isinstance(value, dict):
            raise InstructionDataError(self.INVALID)
        self._value = deepcopy(value)
        self._run_id = run.id
        self._resuming = ResumeRecord.present(run.metadata)

    @classmethod
    def initial(cls, run, mode, input_message_id):
        targets = {}
        if mode == SteeringMode.CONSUME:
            target = SteeringTarget(cls.ROOT, run.id, run.engine, mode, accepting=cls.ACCEPTING)
            targets[target.id] = target.to_dict()
        return cls({cls.Field.ACCEPTING: cls.ACCEPTING, cls.Field.MODE: mode,
            cls.Field.INPUT_MESSAGE_ID: input_message_id, cls.Field.TARGETS: targets,
            cls.Field.ROOT_OPENED: cls.UNDECLARED, cls.Field.ROUTES: {}, cls.Field.BINDINGS: {},
            cls.Field.ROUTES_DECLARED: cls.UNDECLARED}, run)

    @classmethod
    def suspended(cls, run):
        return cls({cls.Field.ACCEPTING: cls.CLOSED}, run)

    @classmethod
    def read(cls, run):
        return cls(run.metadata[cls.KEY], run) if cls.KEY in run.metadata else None

    @classmethod
    def require(cls, run):
        return cls(run.metadata[cls.KEY], run)

    @classmethod
    def present(cls, metadata):
        return cls.KEY in metadata

    def store(self, run):
        if run.id != self._run_id:
            raise InstructionDataError(self.INVALID_OWNERSHIP)
        run.metadata[self.KEY] = self.to_dict()

    def to_dict(self):
        return deepcopy(self._value)

    @property
    def accepting(self):
        return self._value.get(self.Field.ACCEPTING)

    @property
    def input_message_id(self):
        return self._value[self.Field.INPUT_MESSAGE_ID]

    @property
    def unapplied_reason(self):
        return self._value.get(self.Field.UNAPPLIED_REASON)

    @property
    def routes(self):
        raw = self._value.get(self.Field.ROUTES, {})
        if not isinstance(raw, dict):
            raise InstructionDataError(self.INVALID_ROUTES)
        result = {}
        for key, value in raw.items():
            try:
                route = SteeringRoute.from_dict(value)
            except (TypeError, ValueError) as error:
                raise InstructionDataError(self.INVALID_ROUTE) from error
            if route.key != key:
                raise InstructionDataError(self.INVALID_ROUTE_KEY)
            result[key] = route
        bindings = self._value.get(self.Field.BINDINGS, {})
        if not isinstance(bindings, dict) or any(not isinstance(key, str) or not key
                or not isinstance(value, str) or value not in result for key, value in bindings.items()):
            raise InstructionDataError(self.INVALID_BINDINGS)
        return result

    @property
    def targets(self):
        if self._resuming and self._value == {self.Field.ACCEPTING: self.CLOSED}:
            return {}
        raw = self._value.get(self.Field.TARGETS)
        if not isinstance(raw, dict):
            raise InstructionDataError(self.INVALID_TARGETS)
        self.routes
        result, scopes = {}, set()
        for identifier, value in raw.items():
            target = SteeringTarget.from_dict(value)
            if target.id != identifier or target.run_id != self._run_id or target.scope in scopes:
                raise InstructionDataError(self.INVALID_OWNERSHIP)
            scopes.add(target.scope)
            result[identifier] = target
        return result

    def declare_routes(self, routes):
        if self._value[self.Field.MODE] != SteeringMode.FORWARD or self._value[self.Field.ROUTES_DECLARED]:
            raise ValueError(self.ROUTES_ONCE)
        if len({route.key for route in routes}) != len(routes):
            raise ValueError(self.DUPLICATE_ROUTES)
        self._value[self.Field.ROUTES] = {route.key: route.to_dict() for route in routes}
        self._value[self.Field.ROUTES_DECLARED] = self.DECLARED

    def bind_route(self, route, scope):
        if (not self.accepting or self.routes.get(route.key) != route or not isinstance(scope, str)
                or not scope or scope in self._value[self.Field.BINDINGS]):
            raise ValueError(self.INVALID_BINDING)
        self._value[self.Field.BINDINGS][scope] = route.key

    def register(self, identifier, target):
        targets = self.targets
        reserved_root = (identifier == self.ROOT and targets.get(identifier) == target
                         and not self._value[self.Field.ROOT_OPENED])
        if (target.id != identifier or target.run_id != self._run_id or identifier in targets and not reserved_root
                or not self.accepting or target.reason is not None
                or target.accepting != (target.mode == SteeringMode.CONSUME)
                or any(item.scope == target.scope and item.id != identifier for item in targets.values())):
            raise ValueError(self.INVALID_REGISTRATION)
        self._value[self.Field.TARGETS][identifier] = target.to_dict()
        if identifier == self.ROOT:
            self._value[self.Field.ROOT_OPENED] = self.DECLARED

    def target(self, identifier, *, consuming=False):
        target = self.targets[identifier]
        if target.reason is not None:
            raise ValueError(self.TARGET_CLOSED)
        if consuming and (target.mode != SteeringMode.CONSUME or not target.accepting):
            raise ValueError(self.NOT_CONSUMING)
        return target

    def close_target(self, identifier, reason):
        target = self.targets[identifier]
        self._value[self.Field.TARGETS][identifier] = replace(target, accepting=self.CLOSED, reason=reason).to_dict()
        if identifier == self.ROOT:
            self._value[self.Field.ACCEPTING] = self.CLOSED

    def close(self, reason):
        for identifier, target in self.targets.items():
            if target.reason is None:
                self.close_target(identifier, reason)
        self._value[self.Field.ACCEPTING] = self.CLOSED
