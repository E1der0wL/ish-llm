"""실행에 영향을 주는 이벤트 처리기와 실행을 관찰하는 구독자를 분리한다."""

import asyncio
import inspect
import time
from copy import deepcopy
from typing import Callable, Optional
from llm.compat import timeout
from llm.services.runtime.policies import positive_seconds


class Subscription:
    """호출하면 구독을 해제한다. stats는 UI 재조회 판단에 쓸 전달 통계다."""

    def __init__(self, entry):
        self._entry = entry

    def __call__(self):
        self._entry["active"] = False

    @property
    def stats(self):
        entry = self._entry
        return {"active": entry["active"], "delivered": entry["delivered"],
                "dropped": entry["dropped"], "failures": entry["failures"],
                "timed_out": entry["timed_out"],
                "pending": entry["queue"].qsize() if entry["queue"] is not None else 0}


class EventHandlers:
    """사용자 이벤트 처리기. 실패하면 Run도 실패하며, 비동기 검증/요청을 기다릴 수 있다."""

    def __init__(self):
        self._handlers = {}

    def register(self, event_type: str, handler: Callable) -> None:
        from llm.engines.base import EngineEventType
        if not isinstance(event_type, str) or not event_type or event_type in EngineEventType._value2member_map_:
            raise ValueError("Custom event type must not replace a built-in event")
        if not callable(handler) or event_type in self._handlers:
            raise ValueError("Invalid or duplicate event handler")
        self._handlers[event_type] = handler

    async def handle(self, context, event) -> None:
        try:
            handler = self._handlers[event.type]
        except KeyError:
            raise ValueError(f"No handler for Engine event: {event.type}") from None
        result = handler(context, deepcopy(event))
        if inspect.isawaitable(result):
            await result


class EventContext:
    """처리기에 제공하는 Run 문맥. 영속 메타데이터 갱신은 서비스 I/O 경로를 사용한다."""

    def __init__(self, run, engine_context, io, repository):
        self._run, self.engine_context = run, engine_context
        self._io, self._repository = io, repository

    @property
    def run(self):
        return deepcopy(self._run)

    async def update_metadata(self, changes: dict) -> None:
        from llm.core.models import ProjectConfig
        ProjectConfig.validate_settings(changes)
        if {"completions", "resume", "checkpoints", "output"} & changes.keys():
            raise ValueError("Completion and checkpoint lineage are managed by RunManager")
        snapshot = deepcopy(changes)
        def persist():
            self._run.metadata.update(snapshot)
            self._repository.save(self._run)
        await self._io.run(persist)


class EventSubscriptions:
    """여러 관찰자를 순서대로 호출한다. 비동기 전달은 유한 큐와 backpressure를 사용한다.

    구독자는 관찰 전용이며 같은 백엔드의 wait/shutdown을 기다리면 안 된다.
    delivery='queued'는 동기 콜백도 별도 스레드에서 실행한다.
    """

    def __init__(self, observability=None):
        self.observability = observability
        self._subscriptions = []
        self._closed = False

    async def _invoke(self, entry, args):
        callback = entry["callback"]
        if entry["delivery"] == "queued" and not inspect.iscoroutinefunction(callback):
            result = await asyncio.to_thread(callback, *args)
        else:
            result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _call(self, entry, args, on_error):
        guard = timeout(entry["timeout"])
        started, status = time.monotonic(), "delivered"
        try:
            async with guard:
                await self._invoke(entry, args)
            entry["delivered"] += 1
        except asyncio.TimeoutError:
            status = "failed"
            # 멈춘 동기 콜백 스레드는 강제 종료할 수 없다. 구독을 꺼 후속 스레드 증가를 막는다.
            expired = guard.expired() if callable(guard.expired) else guard.expired
            if expired:
                entry["active"] = False
                entry["timed_out"] += 1
                status = "timed_out"
            entry["failures"] += 1
            if on_error is not None:
                on_error()
        except asyncio.CancelledError:
            status = None
            if entry["delivery"] == "inline":
                raise
            if on_error is not None:
                on_error()
        except Exception:
            status = "failed"
            entry["failures"] += 1
            # 관찰자 실패가 이미 저장된 실행 결과를 바꾸면 안 된다.
            if on_error is not None:
                on_error()
        finally:
            if self.observability is not None and status is not None:
                self.observability.record("events", status, duration_seconds=time.monotonic() - started)

    async def _consume(self, entry):
        queue = entry["queue"]
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                args, on_error = item
                if entry["active"]:
                    await self._call(entry, args, on_error)
            finally:
                queue.task_done()

    # 공개 API
    @property
    def stats(self):
        """Subscription이 소유하는 실제 통계의 합. 별도 전달 counter를 만들지 않는다."""
        entries = [Subscription(entry).stats for entry in tuple(self._subscriptions)]
        return {key: sum(entry[key] for entry in entries)
                for key in ("delivered", "dropped", "failures", "timed_out", "pending")}

    def subscribe(self, callback: Callable, *, channel: str = "engine",
                  delivery: str = "inline", buffer_size: int = 64,
                  overflow: str = "block", callback_timeout: Optional[float] = None) -> Subscription:
        """queued/drop_oldest는 실행을 막지 않는 UI용 알림이다. 누락 시 저장소를 재조회한다.

        기본 block은 순서를 유지하며 생산자에게 backpressure를 적용한다.
        callback_timeout은 정지한 구독을 비활성화한다. 동기 inline 콜백은 직접 차단하지 말아야 한다.
        """
        if self._closed:
            raise RuntimeError("Event subscriptions are closed")
        if not callable(callback) or channel not in ("engine", "run") or delivery not in ("inline", "queued"):
            raise ValueError("Invalid event subscription")
        if type(buffer_size) is not int or buffer_size < 1:
            raise ValueError("Event buffer must be positive")
        if overflow not in ("block", "drop_oldest") or (delivery == "inline" and overflow != "block"):
            raise ValueError("Invalid event overflow policy")
        positive_seconds(callback_timeout, "Callback timeout")
        entry = {"callback": callback, "channel": channel, "delivery": delivery,
                 "queue": None, "worker": None, "size": buffer_size, "active": True,
                 "overflow": overflow, "timeout": callback_timeout,
                 "delivered": 0, "dropped": 0, "failures": 0, "timed_out": 0}
        self._subscriptions.append(entry)
        return Subscription(entry)

    async def publish(self, channel: str, *args, on_error: Optional[Callable] = None):
        if self._closed:
            raise RuntimeError("Event subscriptions are closed")
        for entry in tuple(self._subscriptions):
            if not entry["active"] or entry["channel"] != channel:
                continue
            snapshot = deepcopy(args)
            if entry["delivery"] == "inline":
                await self._call(entry, snapshot, on_error)
            else:
                if entry["queue"] is None:
                    entry["queue"] = asyncio.Queue(maxsize=entry["size"])
                    entry["worker"] = asyncio.create_task(self._consume(entry))
                queue = entry["queue"]
                if entry["overflow"] == "drop_oldest":
                    if queue.full():
                        queue.get_nowait()
                        queue.task_done()
                        entry["dropped"] += 1
                        if self.observability is not None:
                            self.observability.record("events", "dropped")
                    queue.put_nowait((snapshot, on_error))
                else:
                    await queue.put((snapshot, on_error))

    async def flush(self):
        """현재까지 큐가 수락한 알림의 처리가 끝날 때까지 기다린다."""
        for entry in tuple(self._subscriptions):
            if entry["queue"] is not None:
                await entry["queue"].join()

    async def close(self):
        """생산자가 종료된 후 호출하며, 수락된 알림을 모두 전달하고 종료한다."""
        if self._closed:
            return
        self._closed = True
        await self.flush()
        for entry in self._subscriptions:
            if entry["worker"] is not None:
                await entry["queue"].put(None)
                await entry["worker"]
