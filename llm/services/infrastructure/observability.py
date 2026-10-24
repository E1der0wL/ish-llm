"""호스트 수명 동안의 best-effort 통계. 영속 도메인이나 실행 결정을 소유하지 않는다."""

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from enum import StrEnum
from datetime import datetime, timezone
import math
import re
import threading

from llm.core.tool_codes import ToolErrorCode
from llm.core.contracts import ProjectActivityEvent, ResourceRef


class ObservationRuntime:
    BINDING = "_observability"
    CONTEXT_NAME = "llm_observability"
    ABSENT = None
    RECENT_CAPACITY = 256
    NO_RETRY = False
    ONE = 1


class ObservationKind(StrEnum):
    """관찰 사실의 집계 경계. 실행 권한이나 영속 도메인이 아니다."""

    RUNS = "runs"
    TOOLS = "tools"
    PROVIDERS = "providers"
    WORKERS = "workers"
    EVENTS = "events"


class ObservationStatus(StrEnum):
    """기존 관찰 사실의 이름. 각 경계의 허용 집합은 별도로 유지한다."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    REQUESTS = "requests"
    EXECUTIONS = "executions"
    REUSED = "reused"
    APPROVAL_REQUIRED = "approval_required"
    CANCELLED = "cancelled"
    CALLS = "calls"
    RETRIES = "retries"
    SPAWNED = "spawned"
    DELIVERED = "delivered"
    DROPPED = "dropped"
    TIMED_OUT = "timed_out"


_current = ContextVar(ObservationRuntime.CONTEXT_NAME, default=ObservationRuntime.ABSENT)
_statuses = {
    ObservationKind.RUNS.value: {
        ObservationStatus.STARTED.value,
        ObservationStatus.COMPLETED.value,
        ObservationStatus.FAILED.value,
        ObservationStatus.INTERRUPTED.value,
        ObservationStatus.PAUSED.value,
    },
    ObservationKind.TOOLS.value: {
        ObservationStatus.REQUESTS.value,
        ObservationStatus.EXECUTIONS.value,
        ObservationStatus.REUSED.value,
        ObservationStatus.APPROVAL_REQUIRED.value,
        ObservationStatus.COMPLETED.value,
        ObservationStatus.FAILED.value,
        ObservationStatus.CANCELLED.value,
    },
    ObservationKind.PROVIDERS.value: {
        ObservationStatus.CALLS.value,
        ObservationStatus.COMPLETED.value,
        ObservationStatus.FAILED.value,
        ObservationStatus.RETRIES.value,
        ObservationStatus.CANCELLED.value,
    },
    ObservationKind.WORKERS.value: {
        ObservationStatus.SPAWNED.value,
        ObservationStatus.COMPLETED.value,
        ObservationStatus.FAILED.value,
        ObservationStatus.CANCELLED.value,
    },
    ObservationKind.EVENTS.value: {
        ObservationStatus.DELIVERED.value,
        ObservationStatus.DROPPED.value,
        ObservationStatus.FAILED.value,
        ObservationStatus.TIMED_OUT.value,
    },
}


def record(kind, status, **fields):
    observer = _current.get()
    if observer is not ObservationRuntime.ABSENT:
        observer.record(kind, status, **fields)


class ObservationRecord:
    TIME = ProjectActivityEvent.time.__name__
    KIND = ResourceRef.kind.__name__
    STATUS = ProjectActivityEvent.status.__name__
    CODE = ProjectActivityEvent.code.__name__
    NAME = "name"
    DURATION_SECONDS = "duration_seconds"
    FIELDS = frozenset(
        (
            NAME,
            ResourceRef.project_id.__name__,
            ResourceRef.session_id.__name__,
            ResourceRef.run_id.__name__,
            ResourceRef.step_id.__name__,
        )
    )
    CODE_PATTERN = r"[a-z][a-z0-9_]*"
    MINIMUM_DURATION = 0

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, kind, status, code, duration_seconds, fields):
        event = {cls.TIME: datetime.now(timezone.utc).isoformat(), cls.KIND: kind, cls.STATUS: status}
        if isinstance(code, str) and re.fullmatch(cls.CODE_PATTERN, code):
            event[cls.CODE] = code
        for key in cls.FIELDS:
            value = fields.get(key)
            if isinstance(value, str):
                event[key] = value
        duration = duration_seconds
        if type(duration) in (float, int) and math.isfinite(duration) and duration >= cls.MINIMUM_DURATION:
            event[cls.DURATION_SECONDS] = duration
        return cls(event)

    @property
    def has_code(self):
        return self.CODE in self._value

    @property
    def has_duration(self):
        return self.DURATION_SECONDS in self._value

    @property
    def record(self):
        return self._value


class ObservationLatency:
    COUNT = "count"
    TOTAL_SECONDS = "total_seconds"
    MAX_SECONDS = "max_seconds"
    ZERO_COUNT = 0
    ZERO_SECONDS = 0.0
    ONE = 1

    @classmethod
    def empty(cls):
        return {
            cls.COUNT: cls.ZERO_COUNT,
            cls.TOTAL_SECONDS: cls.ZERO_SECONDS,
            cls.MAX_SECONDS: cls.ZERO_SECONDS,
        }

    @classmethod
    def add(cls, aggregate, duration):
        aggregate[cls.COUNT] += cls.ONE
        aggregate[cls.TOTAL_SECONDS] += duration
        aggregate[cls.MAX_SECONDS] = max(aggregate[cls.MAX_SECONDS], duration)


class ObservationSnapshot:
    FAILURES_BY_CODE = "failures_by_code"
    LATENCY = "latency"
    RECENT = "recent"
    RUNTIME = "runtime"
    PROTOCOL_FAILED = "protocol_failed"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, values, failures, latency, recent):
        return deepcopy(
            {**values, cls.FAILURES_BY_CODE: dict(failures), cls.LATENCY: latency, cls.RECENT: list(recent)}
        )

    def set_runtime(self, runtime):
        self._value[self.RUNTIME] = runtime

    def merge_providers(self, providers):
        self._value[ObservationKind.PROVIDERS.value].update(providers)

    def set_events(self, events):
        self._value[ObservationKind.EVENTS.value] = events

    @property
    def record(self):
        return self._value


class Observability:
    """하나의 fact에서 집계/latency/recent를 파생한다. ID별 aggregate는 만들지 않는다."""

    def __init__(self, *, sink=ObservationRuntime.ABSENT):
        self._lock = threading.RLock()
        self._counts = {kind: Counter() for kind in _statuses}
        self._failures = Counter()
        self._latency = {}
        # 관찰 버퍼의 메모리 상한이며 실행/저장 데이터의 retention 정책이 아니다.
        self._recent = deque(maxlen=ObservationRuntime.RECENT_CAPACITY)
        self._sink = sink

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            yield
        finally:
            _current.reset(token)

    def record(
        self,
        kind,
        status,
        *,
        code=ObservationRuntime.ABSENT,
        duration_seconds=ObservationRuntime.ABSENT,
        retry=ObservationRuntime.NO_RETRY,
        **fields,
    ):
        try:
            if kind not in _statuses or status not in _statuses[kind]:
                return
            event = ObservationRecord.create(kind, status, code, duration_seconds, fields)
            duration = duration_seconds
            with self._lock:
                # EventSubscriptions만 전달 count를 소유한다. 여기서는 최근 사실만 보관한다.
                if kind != ObservationKind.EVENTS.value:
                    self._counts[kind][status] += ObservationRuntime.ONE
                if (
                    kind == ObservationKind.TOOLS.value
                    and status == ObservationStatus.EXECUTIONS.value
                    and retry
                ):
                    self._counts[kind][ObservationStatus.RETRIES.value] += ObservationRuntime.ONE
                if (
                    kind == ObservationKind.WORKERS.value
                    and status == ObservationStatus.FAILED.value
                    and code == ToolErrorCode.WORKER_PROTOCOL
                ):
                    self._counts[kind][ObservationSnapshot.PROTOCOL_FAILED] += ObservationRuntime.ONE
                if status == ObservationStatus.FAILED.value and event.has_code:
                    self._failures[code] += ObservationRuntime.ONE
                if event.has_duration:
                    aggregate = self._latency.setdefault(kind, ObservationLatency.empty())
                    ObservationLatency.add(aggregate, duration)
                self._recent.append(event.record)
            if self._sink is not ObservationRuntime.ABSENT:
                self._sink(deepcopy(event.record))
        except BaseException:
            # 관찰 sink는 cancellation/실행 의미를 바꿀 수 없다.
            return

    def snapshot(self):
        with self._lock:
            values = {
                kind: {key: self._counts[kind][key] for key in statuses}
                for kind, statuses in _statuses.items()
                if kind != ObservationKind.EVENTS.value
            }
            values[ObservationKind.TOOLS.value][ObservationStatus.RETRIES.value] = self._counts[
                ObservationKind.TOOLS.value
            ][ObservationStatus.RETRIES.value]
            values[ObservationKind.WORKERS.value][ObservationSnapshot.PROTOCOL_FAILED] = self._counts[
                ObservationKind.WORKERS.value
            ][ObservationSnapshot.PROTOCOL_FAILED]
            return ObservationSnapshot.create(values, self._failures, self._latency, self._recent)


class ObservabilityView:
    """UI용 read-only surface. snapshot은 provider/Tool/파일 접근을 유발하지 않는다."""

    def __init__(self, observer, runtime, providers, events):
        self._observer, self._runtime = observer, runtime
        self._providers, self._events = providers, events

    def snapshot(self):
        value = ObservationSnapshot(self._observer.snapshot())
        value.set_runtime(self._runtime())
        value.merge_providers(self._providers.stats)
        value.set_events(self._events.stats)
        return value.record

    async def asnapshot(self):
        return self.snapshot()
