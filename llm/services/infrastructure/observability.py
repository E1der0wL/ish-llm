"""호스트 수명 동안의 best-effort 통계. 영속 도메인이나 실행 결정을 소유하지 않는다."""

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import threading

_current = ContextVar("llm_observability", default=None)
_fields = frozenset(("name", "project_id", "session_id", "run_id", "step_id"))
_statuses = {
    "runs": {"started", "completed", "failed", "interrupted", "paused"},
    "tools": {"requests", "executions", "reused", "approval_required", "completed", "failed", "cancelled"},
    "providers": {"calls", "completed", "failed", "retries", "cancelled"},
    "workers": {"spawned", "completed", "failed", "cancelled"},
    "events": {"delivered", "dropped", "failed", "timed_out"},
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
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
                event["code"] = code
            for key in _fields:
                value = fields.get(key)
                if isinstance(value, str):
                    event[key] = value[:128]
            duration = duration_seconds
            if type(duration) in (float, int) and math.isfinite(duration) and duration >= 0:
                event["duration_seconds"] = duration
            with self._lock:
                # EventSubscriptions만 전달 count를 소유한다. 여기서는 최근 사실만 보관한다.
                if kind != "events":
                    self._counts[kind][status] += 1
                if kind == "tools" and status == "executions" and retry:
                    self._counts[kind]["retries"] += 1
                if kind == "workers" and status == "failed" and code == "tool_worker_protocol":
                    self._counts[kind]["protocol_failed"] += 1
                if status == "failed" and "code" in event:
                    # 오류 코드가 확장마다 무한히 달라져도 관찰 메모리는 유한하다.
                    key = code if code in self._failures or len(self._failures) < 128 else "other"
                    self._failures[key] += 1
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
                      for kind, statuses in _statuses.items() if kind != "events"}
            values["tools"]["retries"] = self._counts["tools"]["retries"]
            values["workers"]["protocol_failed"] = self._counts["workers"]["protocol_failed"]
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
        value["providers"].update(self._providers.stats)
        value["events"] = self._events.stats
        return value

    async def asnapshot(self):
        return self.snapshot()
