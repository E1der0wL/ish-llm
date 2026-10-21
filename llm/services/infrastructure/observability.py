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

class ObservationKind(StrEnum):
    """관찰 사실의 집계 경계. 실행 권한이나 영속 도메인이 아니다."""

    RUNS = 'runs'
    TOOLS = 'tools'
    PROVIDERS = 'providers'
    WORKERS = 'workers'
    EVENTS = 'events'


class ObservationStatus(StrEnum):
    """기존 관찰 사실의 이름. 각 경계의 허용 집합은 별도로 유지한다."""

    STARTED = 'started'
    COMPLETED = 'completed'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    PAUSED = 'paused'
    REQUESTS = 'requests'
    EXECUTIONS = 'executions'
    REUSED = 'reused'
    APPROVAL_REQUIRED = 'approval_required'
    CANCELLED = 'cancelled'
    CALLS = 'calls'
    RETRIES = 'retries'
    SPAWNED = 'spawned'
    DELIVERED = 'delivered'
    DROPPED = 'dropped'
    TIMED_OUT = 'timed_out'


_current = ContextVar("llm_observability", default=None)
_fields = frozenset(("name", "project_id", "session_id", "run_id", "step_id"))
_statuses = {
    ObservationKind.RUNS.value: {ObservationStatus.STARTED.value, ObservationStatus.COMPLETED.value, ObservationStatus.FAILED.value, ObservationStatus.INTERRUPTED.value, ObservationStatus.PAUSED.value},
    ObservationKind.TOOLS.value: {ObservationStatus.REQUESTS.value, ObservationStatus.EXECUTIONS.value, ObservationStatus.REUSED.value, ObservationStatus.APPROVAL_REQUIRED.value, ObservationStatus.COMPLETED.value, ObservationStatus.FAILED.value, ObservationStatus.CANCELLED.value},
    ObservationKind.PROVIDERS.value: {ObservationStatus.CALLS.value, ObservationStatus.COMPLETED.value, ObservationStatus.FAILED.value, ObservationStatus.RETRIES.value, ObservationStatus.CANCELLED.value},
    ObservationKind.WORKERS.value: {ObservationStatus.SPAWNED.value, ObservationStatus.COMPLETED.value, ObservationStatus.FAILED.value, ObservationStatus.CANCELLED.value},
    ObservationKind.EVENTS.value: {ObservationStatus.DELIVERED.value, ObservationStatus.DROPPED.value, ObservationStatus.FAILED.value, ObservationStatus.TIMED_OUT.value},
}


def record(kind, status, **fields):
    observer = _current.get()
    if observer is not None:
        observer.record(kind, status, **fields)


class Observability:
    """하나의 fact에서 집계/latency/recent를 파생한다. ID별 aggregate는 만들지 않는다."""
    def __init__(self, *, sink=None):
        self._lock = threading.RLock()
        self._counts = {kind: Counter() for kind in _statuses}
        self._failures = Counter()
        self._latency = {}
        # 관찰 버퍼의 메모리 상한이며 실행/저장 데이터의 retention 정책이 아니다.
        self._recent = deque(maxlen=256)
        self._sink = sink

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            yield
        finally:
            _current.reset(token)

    def record(self, kind, status, *, code=None, duration_seconds=None, retry=False, **fields):
        try:
            if kind not in _statuses or status not in _statuses[kind]:
                return
            event = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, "status": status}
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]*", code):
                event["code"] = code
            for key in _fields:
                value = fields.get(key)
                if isinstance(value, str):
                    event[key] = value
            duration = duration_seconds
            if type(duration) in (float, int) and math.isfinite(duration) and duration >= 0:
                event["duration_seconds"] = duration
            with self._lock:
                # EventSubscriptions만 전달 count를 소유한다. 여기서는 최근 사실만 보관한다.
                if kind != ObservationKind.EVENTS.value:
                    self._counts[kind][status] += 1
                if kind == ObservationKind.TOOLS.value and status == ObservationStatus.EXECUTIONS.value and retry:
                    self._counts[kind][ObservationStatus.RETRIES.value] += 1
                if kind == ObservationKind.WORKERS.value and status == ObservationStatus.FAILED.value and code == "tool_worker_protocol":
                    self._counts[kind]["protocol_failed"] += 1
                if status == ObservationStatus.FAILED.value and "code" in event:
                    self._failures[code] += 1
                if "duration_seconds" in event:
                    aggregate = self._latency.setdefault(kind, {"count": 0, "total_seconds": 0., "max_seconds": 0.})
                    aggregate["count"] += 1
                    aggregate["total_seconds"] += duration
                    aggregate["max_seconds"] = max(aggregate["max_seconds"], duration)
                self._recent.append(event)
            if self._sink is not None:
                self._sink(deepcopy(event))
        except BaseException:
            # 관찰 sink는 cancellation/실행 의미를 바꿀 수 없다.
            return

    def snapshot(self):
        with self._lock:
            values = {kind: {key: self._counts[kind][key] for key in statuses}
                      for kind, statuses in _statuses.items() if kind != ObservationKind.EVENTS.value}
            values[ObservationKind.TOOLS.value][ObservationStatus.RETRIES.value] = self._counts[ObservationKind.TOOLS.value][ObservationStatus.RETRIES.value]
            values[ObservationKind.WORKERS.value]["protocol_failed"] = self._counts[ObservationKind.WORKERS.value]["protocol_failed"]
            return deepcopy({**values, "failures_by_code": dict(self._failures),
                             "latency": self._latency, "recent": list(self._recent)})


class ObservabilityView:
    """UI용 read-only surface. snapshot은 provider/Tool/파일 접근을 유발하지 않는다."""
    def __init__(self, observer, runtime, providers, events):
        self._observer, self._runtime = observer, runtime
        self._providers, self._events = providers, events

    def snapshot(self):
        value = self._observer.snapshot()
        value["runtime"] = self._runtime()
        value[ObservationKind.PROVIDERS.value].update(self._providers.stats)
        value[ObservationKind.EVENTS.value] = self._events.stats
        return value

    async def asnapshot(self):
        return self.snapshot()
