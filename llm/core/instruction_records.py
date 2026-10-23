"""Message-owned instruction receipts; no execution or persistence side effects."""

from copy import deepcopy
from enum import StrEnum

from .models import MessageRole, MessageStatus
from .steering import InstructionDataError, InstructionStatus, SteeringRoute, SteeringMode, _text
from .steering_events import SteeringReason


class InstructionApplication:
    INVALID = "target application"

    class Field(StrEnum):
        RUN_ID = "run_id"
        BOUNDARY = "boundary"
        TARGET_ID = "target_id"
        TIME = "time"
        STEP_ID = "step_id"

    def __init__(self, value):
        if (not isinstance(value, dict)
                or any(not _text(value.get(key)) for key in (self.Field.RUN_ID, self.Field.BOUNDARY,
                                                           self.Field.TARGET_ID, self.Field.TIME))
                or self.Field.STEP_ID not in value
                or value[self.Field.STEP_ID] is not None and not _text(value[self.Field.STEP_ID])):
            raise InstructionDataError(self.INVALID)
        self._value = deepcopy(value)

    @classmethod
    def create(cls, run_id, boundary, target_id, step_id, timestamp, details=None):
        return cls({**(details or {}), cls.Field.RUN_ID: run_id, cls.Field.BOUNDARY: boundary,
                    cls.Field.TARGET_ID: target_id, cls.Field.STEP_ID: step_id, cls.Field.TIME: timestamp})

    @property
    def run_id(self):
        return self._value[self.Field.RUN_ID]

    @property
    def boundary(self):
        return self._value[self.Field.BOUNDARY]

    @property
    def target_id(self):
        return self._value[self.Field.TARGET_ID]

    def to_dict(self):
        return deepcopy(self._value)


class InstructionReceipt:
    APPLIED_APPLICATIONS = 1
    UNCONSUMED_APPLICATIONS = 0
    INVALID_FIELDS = "message target fields"
    INVALID_ROUTE = "message reservation route"
    INVALID_BINDING = "reservation binding"
    INVALID_REASON = "unapplied target reason"
    INVALID_APPLICATION_OWNER = "reservation application ownership"
    MISSING_APPLICATION = "applied target requires application"
    RESERVED_IDENTITY = "reservation"
    EXECUTION_IDENTITY = "execution"
    RESERVED_NOT_CONSUMING = "Reserved execution must consume instructions"
    CHANGED_ENGINE = "Reserved Agent engine changed"
    INVALID_TRANSITION = "Invalid reservation receipt transition"

    class Field(StrEnum):
        ID = "id"
        SCOPE = "scope"
        STATUS = "status"
        APPLICATIONS = "applications"
        RESERVATION = "reservation"
        EXECUTION_ID = "execution_id"
        REASON = "reason"

    def __init__(self, value):
        self._value = deepcopy(value)
        self._validate()

    def _validate(self):
        value = self._value
        if (not isinstance(value, dict) or not _text(value.get(self.Field.ID)) or self.Field.SCOPE not in value
                or value[self.Field.SCOPE] is not None and not _text(value[self.Field.SCOPE])
                or value.get(self.Field.STATUS) not in (InstructionStatus.PENDING, InstructionStatus.APPLIED, InstructionStatus.UNAPPLIED)
                or not isinstance(value.get(self.Field.APPLICATIONS), list)):
            raise InstructionDataError(self.INVALID_FIELDS)
        if self.reserved:
            self.reservation
            if (self.Field.EXECUTION_ID not in value
                    or self.execution_id is not None and not _text(self.execution_id)
                    or self.execution_id is not None and self.scope is None
                    or self.status == InstructionStatus.APPLIED and self.execution_id is None
                    or len(self.applications) != (self.APPLIED_APPLICATIONS if self.status == InstructionStatus.APPLIED else self.UNCONSUMED_APPLICATIONS)):
                raise InstructionDataError(self.INVALID_BINDING)
        if self.status == InstructionStatus.UNAPPLIED and not _text(self.reason):
            raise InstructionDataError(self.INVALID_REASON)
        if self.status == InstructionStatus.APPLIED and not self.applications:
            raise InstructionDataError(self.MISSING_APPLICATION)
        self.applications

    @classmethod
    def active(cls, target):
        return cls({cls.Field.ID: target.id, cls.Field.SCOPE: target.scope,
                    cls.Field.STATUS: InstructionStatus.PENDING, cls.Field.APPLICATIONS: []})

    @classmethod
    def reserve(cls, identifier, route):
        return cls({cls.Field.ID: identifier, cls.Field.SCOPE: None,
                    cls.Field.STATUS: InstructionStatus.PENDING, cls.Field.APPLICATIONS: [],
                    cls.Field.RESERVATION: route.to_dict(), cls.Field.EXECUTION_ID: None})

    @property
    def id(self):
        return self._value[self.Field.ID]

    @property
    def scope(self):
        return self._value[self.Field.SCOPE]

    @property
    def status(self):
        return InstructionStatus(self._value[self.Field.STATUS])

    @property
    def applications(self):
        return tuple(InstructionApplication(value) for value in self._value[self.Field.APPLICATIONS])

    @property
    def reserved(self):
        return self.Field.RESERVATION in self._value

    @property
    def reservation(self):
        try:
            return SteeringRoute.from_dict(self._value[self.Field.RESERVATION])
        except (TypeError, ValueError) as error:
            raise InstructionDataError(self.INVALID_ROUTE) from error

    @property
    def execution_id(self):
        return self._value.get(self.Field.EXECUTION_ID)

    @property
    def recipient_id(self):
        return self._value.get(self.Field.EXECUTION_ID, self.id)

    @property
    def reason(self):
        return self._value.get(self.Field.REASON)

    @property
    def identity(self):
        return ((self.RESERVED_IDENTITY, self.reservation.key) if self.reserved
                else (self.EXECUTION_IDENTITY, self.scope))

    def validate_run(self, run_id):
        if self.reserved and any(application.target_id != self.execution_id or application.run_id != run_id
                                 for application in self.applications):
            raise InstructionDataError(self.INVALID_APPLICATION_OWNER)

    def pending_for(self, identifier=None):
        return self.status == InstructionStatus.PENDING and (identifier is None or self.recipient_id == identifier)

    def waiting_for_route(self, route):
        return (self.reserved and self.reservation == route and self.scope is None
                and self.status == InstructionStatus.PENDING)

    def waiting_for_target(self, target):
        return (self.reserved and self.scope == target.scope and self.status == InstructionStatus.PENDING
                and self.execution_id is None)

    @property
    def restores_context(self):
        return not self.reserved or self.status == InstructionStatus.APPLIED

    def bind(self, route, scope):
        if not self.waiting_for_route(route):
            raise InstructionDataError(self.INVALID_TRANSITION)
        return type(self)({**self._value, self.Field.SCOPE: scope})

    def activate(self, target):
        if not self.waiting_for_target(target):
            raise InstructionDataError(self.INVALID_TRANSITION)
        if target.mode != SteeringMode.CONSUME:
            raise ValueError(self.RESERVED_NOT_CONSUMING)
        if self.reservation.engine != target.engine:
            raise ValueError(self.CHANGED_ENGINE)
        return type(self)({**self._value, self.Field.EXECUTION_ID: target.id})

    def finish(self, reason):
        if self.status != InstructionStatus.PENDING:
            raise InstructionDataError(self.INVALID_TRANSITION)
        if self.reserved and self.scope is None and reason == SteeringReason.COMPLETED:
            reason = SteeringReason.NODE_NOT_REACHED
        return type(self)({**self._value, self.Field.STATUS: InstructionStatus.UNAPPLIED, self.Field.REASON: reason})

    def apply(self, run_id, boundary, target_id, step_id, timestamp, details=None):
        # Resume restores context without re-consuming an already applied reservation.
        if self.reserved and self.applications:
            return self
        applications = list(self.applications)
        if not any(value.run_id == run_id and value.boundary == boundary for value in applications):
            applications.append(InstructionApplication.create(run_id, boundary, target_id, step_id, timestamp, details))
        value = {**self._value, self.Field.STATUS: InstructionStatus.APPLIED,
                 self.Field.APPLICATIONS: [item.to_dict() for item in applications]}
        value.pop(self.Field.REASON, None)
        return type(self)(value)

    def to_dict(self):
        return deepcopy(self._value)


class InstructionMessageRecord:
    MESSAGE_STATUSES = {InstructionStatus.PENDING: MessageStatus.QUEUED,
        InstructionStatus.UNAPPLIED: MessageStatus.CANCELLED, InstructionStatus.APPLIED: MessageStatus.COMMITTED,
        InstructionStatus.PARTIALLY_APPLIED: MessageStatus.COMMITTED}
    KEY = "steering"
    INVALID_OWNER = "message ownership"
    INVALID_TARGETS = "message targets (nonempty list required)"
    DUPLICATE_TARGETS = "duplicate message target identity/scope"
    DUPLICATE_SCOPE = "duplicate bound reservation scope"
    INVALID_STATUS = "message aggregate status"
    INVALID_APPLICATIONS = "message aggregate applications"
    INVALID_REASON = "unapplied message reason"

    class Field(StrEnum):
        RUN_ID = "run_id"
        INPUT_MESSAGE_ID = "input_message_id"
        TARGETS = "targets"
        STATUS = "status"
        APPLICATIONS = "applications"
        REASON = "reason"

    def __init__(self, value):
        if (not isinstance(value, dict)
                or any(not _text(value.get(key)) for key in (self.Field.RUN_ID, self.Field.INPUT_MESSAGE_ID))):
            raise InstructionDataError(self.INVALID_OWNER)
        raw = value.get(self.Field.TARGETS)
        if not isinstance(raw, list) or not raw:
            raise InstructionDataError(self.INVALID_TARGETS)
        self._value = deepcopy(value)
        ids, identities, scopes = set(), set(), set()
        for target in self.targets:
            target.validate_run(self.run_id)
            if target.id in ids or target.identity in identities:
                raise InstructionDataError(self.DUPLICATE_TARGETS)
            if target.scope is not None:
                if target.scope in scopes:
                    raise InstructionDataError(self.DUPLICATE_SCOPE)
                scopes.add(target.scope)
            ids.add(target.id)
            identities.add(target.identity)
        if value.get(self.Field.STATUS) != self.aggregate_status:
            raise InstructionDataError(self.INVALID_STATUS)
        if value.get(self.Field.APPLICATIONS, []) != self.application_records:
            raise InstructionDataError(self.INVALID_APPLICATIONS)
        if self.status == InstructionStatus.UNAPPLIED and not _text(self.reason):
            raise InstructionDataError(self.INVALID_REASON)

    @classmethod
    def present(cls, metadata):
        return cls.KEY in metadata

    @classmethod
    def from_message(cls, message):
        value = cls(message.metadata.get(cls.KEY))
        if (message.role != MessageRole.USER or message.run_id is not None and message.run_id != value.run_id):
            raise InstructionDataError(cls.INVALID_OWNER)
        if message.status != value.message_status:
            raise InstructionDataError(cls.INVALID_STATUS)
        return value

    @classmethod
    def create(cls, run_id, input_message_id, targets):
        return cls({cls.Field.RUN_ID: run_id, cls.Field.INPUT_MESSAGE_ID: input_message_id,
                    cls.Field.STATUS: InstructionStatus.PENDING,
                    cls.Field.TARGETS: [target.to_dict() for target in targets]})

    @property
    def run_id(self):
        return self._value[self.Field.RUN_ID]

    @property
    def input_message_id(self):
        return self._value[self.Field.INPUT_MESSAGE_ID]

    @property
    def status(self):
        return InstructionStatus(self._value[self.Field.STATUS])

    @property
    def targets(self):
        return tuple(InstructionReceipt(raw) for raw in self._value[self.Field.TARGETS])

    @property
    def reason(self):
        return self._value.get(self.Field.REASON)

    @property
    def aggregate_status(self):
        return self.status_for(self.targets)

    @staticmethod
    def status_for(targets):
        states = {target.status for target in targets}
        return (InstructionStatus.PENDING if InstructionStatus.PENDING in states else
                InstructionStatus.APPLIED if states == {InstructionStatus.APPLIED} else
                InstructionStatus.UNAPPLIED if states == {InstructionStatus.UNAPPLIED} else
                InstructionStatus.PARTIALLY_APPLIED)

    @property
    def message_status(self):
        return self.MESSAGE_STATUSES[self.status]

    @property
    def application_records(self):
        return [value.to_dict() for target in self.targets for value in target.applications]

    def with_targets(self, targets):
        """Derive aggregates once here, then validate through the same Reader."""
        targets = tuple(targets)
        status = self.status_for(targets)
        value = {**self._value, self.Field.TARGETS: [target.to_dict() for target in targets],
                 self.Field.STATUS: status,
                 self.Field.APPLICATIONS: [item.to_dict() for target in targets for item in target.applications]}
        if status == InstructionStatus.UNAPPLIED:
            value[self.Field.REASON] = targets[0].reason
        else:
            value.pop(self.Field.REASON, None)
        return type(self)(value)

    def metadata(self):
        return {self.KEY: self.to_dict()}

    def to_dict(self):
        return deepcopy(self._value)
