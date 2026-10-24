"""Live subscription state and detached statistics; never execution authority."""

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any


class SubscriptionContext:
    NAME = "llm_event_subscription"
    ABSENT = None


class EventChannel(StrEnum):
    ENGINE = "engine"
    RUN = "run"


class EventDelivery(StrEnum):
    INLINE = "inline"
    QUEUED = "queued"


class EventOverflow(StrEnum):
    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"


@dataclass(slots=True)
class DeliveryCounters:
    INITIAL = 0
    INCREMENT = 1

    delivered: int = INITIAL
    dropped: int = INITIAL
    failures: int = INITIAL
    timed_out: int = INITIAL

    def retire(self, counters):
        for item in fields(self):
            setattr(self, item.name, getattr(self, item.name) + getattr(counters, item.name))

    def total(self, snapshots):
        return {
            item.name: getattr(self, item.name) + sum(value[item.name] for value in snapshots)
            for item in fields(self)
        }


@dataclass(slots=True)
class SubscriptionEntry:
    ABSENT = None
    ACTIVE = True
    INACTIVE = False
    OPEN = False
    CLOSED = True
    NO_INVOCATIONS = 0
    INVOCATION_INCREMENT = 1

    callback: Any
    channel: str
    delivery: str
    queue: Any
    worker: Any
    size: int
    active: bool
    space: Any
    done: Any
    closed: bool
    invoking: int
    overflow: str
    timeout: float | None
    counters: DeliveryCounters

    @classmethod
    def create(cls, callback, channel, delivery, size, overflow, timeout, done):
        # These are live objects. Serializing or copying their ownership is invalid.
        return cls(
            callback,
            channel,
            delivery,
            cls.ABSENT,
            cls.ABSENT,
            size,
            cls.ACTIVE,
            cls.ABSENT,
            done,
            cls.OPEN,
            cls.NO_INVOCATIONS,
            overflow,
            timeout,
            DeliveryCounters(),
        )


@dataclass(frozen=True, slots=True)
class SubscriptionStats:
    NO_PENDING = 0

    active: bool
    delivered: int
    dropped: int
    failures: int
    timed_out: int
    pending: int

    @classmethod
    def from_entry(cls, entry):
        counters = entry.counters
        return cls(
            entry.active,
            counters.delivered,
            counters.dropped,
            counters.failures,
            counters.timed_out,
            (
                entry.queue.qsize()
                if entry.active and entry.queue is not SubscriptionEntry.ABSENT
                else cls.NO_PENDING
            ),
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def aggregate(cls, retired, snapshots):
        return {
            **retired.total(snapshots),
            cls.pending.__name__: sum(row[cls.pending.__name__] for row in snapshots),
        }
