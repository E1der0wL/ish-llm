"""실행에 영향을 주는 이벤트 처리기와 실행을 관찰하는 구독자를 분리한다."""

from llm.core.execution_observations import ExecutionObservations
from llm.core.continuations import RestartRecord, ResumeRecord, RunExecutionEvidence

import asyncio
import inspect
import time
from copy import deepcopy
from contextvars import ContextVar
from typing import Callable, Optional
from weakref import ref
from asyncio import timeout
from llm.services.runtime.policies import positive_seconds
from llm.core.steering_channel import SteeringChannel
from llm.core.checkpoint_records import CheckpointDeclarations, CheckpointDeletion
from llm.core.checkpoints import ResumeAbandonment
from llm.core.results import CompletionRecords
from llm.core.agents import AgentBinding
from llm.core.policies import RunPolicySnapshot
from .subscription_state import (
    EventChannel,
    EventDelivery,
    EventOverflow,
    DeliveryCounters,
    SubscriptionEntry,
    SubscriptionStats,
    SubscriptionContext,
)
from llm.services.infrastructure.observability import ObservationKind, ObservationStatus


_current_subscription = ContextVar(SubscriptionContext.NAME, default=SubscriptionContext.ABSENT)


class Subscription:
    """호출하면 전달을 중단한다. aclose는 진행 중인 콜백과 자원 정리까지 기다린다."""

    ABSENT = None
    SELF_CLOSE = "Cannot await subscription close from its own callback; call subscription()"

    def __init__(self, entry, owner):
        self._entry = entry
        self._owner = ref(owner)

    def __call__(self):
        owner = self._owner()
        if owner is not Subscription.ABSENT:
            owner._request_unsubscribe(self._entry)

    async def aclose(self):
        """해제 완료를 기다린다. 자기 콜백 안에서는 동기 해제만 사용한다."""
        if _current_subscription.get() is self._entry:
            raise RuntimeError(Subscription.SELF_CLOSE)
        owner = self._owner()
        if owner is not Subscription.ABSENT:
            owner._bind_loop()
            owner._unsubscribe(self._entry)
            await owner._wait_closed(self._entry)

    @property
    def stats(self):
        return SubscriptionStats.from_entry(self._entry).to_dict()


class EventHandlers:
    """사용자 이벤트 처리기. 실패하면 Run도 실패하며, 비동기 검증/요청을 기다릴 수 있다."""

    INVALID_TYPE = "Custom event type must not replace a built-in event"
    INVALID_HANDLER = "Invalid or duplicate event handler"
    MISSING_HANDLER = "No handler for Engine event: {event_type}"

    def __init__(self):
        self._handlers = {}

    def register(self, event_type: str, handler: Callable) -> None:
        from llm.engines.base import EngineEventType

        if (
            not isinstance(event_type, str)
            or not event_type
            or event_type in EngineEventType._value2member_map_
        ):
            raise ValueError(EventHandlers.INVALID_TYPE)
        if not callable(handler) or event_type in self._handlers:
            raise ValueError(EventHandlers.INVALID_HANDLER)
        self._handlers[event_type] = handler

    async def handle(self, context, event) -> None:
        try:
            handler = self._handlers[event.type]
        except KeyError:
            raise ValueError(EventHandlers.MISSING_HANDLER.format(event_type=event.type)) from None
        result = handler(context, deepcopy(event))
        if inspect.isawaitable(result):
            await result


class EventContext:
    """처리기에 제공하는 Run 문맥. 영속 메타데이터 갱신은 서비스 I/O 경로를 사용한다."""

    RESERVED_ENGINE_OPTIONS = "engine_options"
    PROTECTED = "Run metadata is managed by RunManager: "
    FIELD_SEPARATOR = ", "

    # Run 시작 시 확정한 정책과 실행/재개 원본은 서비스만 변경한다.
    _managed_metadata = frozenset(
        (
            RunPolicySnapshot.KEY,
            CompletionRecords.KEY,
            ResumeRecord.KEY,
            CheckpointDeclarations.NAMES,
            ExecutionObservations.Field.OUTPUT,
            SteeringChannel.KEY,
            RESERVED_ENGINE_OPTIONS,
            ResumeAbandonment.KEY,
            CheckpointDeletion.KEY,
            RunExecutionEvidence.KEY,
            RestartRecord.KEY,
            ExecutionObservations.Field.DIAGNOSTIC,
            AgentBinding.METADATA_FIELD,
            AgentBinding.CONTEXT_FIELD,
            AgentBinding.EXECUTION_FIELD,
        )
    )

    def __init__(self, run, engine_context, io, repository):
        self._run, self.engine_context = run, engine_context
        self._io, self._repository = io, repository

    @property
    def run(self):
        return deepcopy(self._run)

    async def update_metadata(self, changes: dict) -> None:
        """사용자 메타데이터만 갱신한다. 서비스 소유 키가 섞이면 전체 변경을 거부한다."""
        from llm.core.json_data import JsonObject

        JsonObject.validate(changes)
        protected = self._managed_metadata.intersection(changes)
        if protected:
            raise ValueError(EventContext.PROTECTED + EventContext.FIELD_SEPARATOR.join(sorted(protected)))
        snapshot = deepcopy(changes)

        def persist(run):
            run.metadata.update(snapshot)
            self._repository.save(run)

        # 인자로 전달한 도메인 객체는 StorageIO가 변경 전에 watch한다.
        # 저장 또는 commit 실패 시 파일과 메모리의 정책/메타데이터가 함께 복원된다.
        await self._io.run(persist, self._run)


class EventSubscriptions:
    """여러 관찰자를 순서대로 호출한다. 비동기 전달은 유한 큐와 backpressure를 사용한다.

    구독자는 관찰 전용이며 같은 백엔드의 wait/shutdown을 기다리면 안 된다.
    delivery='queued'는 동기 콜백도 별도 스레드에서 실행한다.
    """

    ABSENT = None
    OPEN = False
    CLOSED = True
    DEFAULT_BUFFER_SIZE = 64
    MINIMUM_BUFFER_SIZE = 1
    OTHER_LOOP = "Event subscriptions belong to another event loop"
    CLOSED_MESSAGE = "Event subscriptions are closed"
    INVALID = "Invalid event subscription"
    INVALID_BUFFER = "Event buffer must be positive"
    INVALID_OVERFLOW = "Invalid event overflow policy"
    TIMEOUT_LABEL = "Callback timeout"
    SELF_CLOSE = "Cannot await event shutdown from a subscription callback"

    def __init__(self, observability=ABSENT):
        self.observability = observability
        self._subscriptions = {}
        self._retired = DeliveryCounters()
        self._closed = EventSubscriptions.OPEN
        self._loop = EventSubscriptions.ABSENT
        self._closing = EventSubscriptions.ABSENT

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is EventSubscriptions.ABSENT:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError(EventSubscriptions.OTHER_LOOP)

    def _request_unsubscribe(self, entry):
        if entry.closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = EventSubscriptions.ABSENT
        if self._loop is not EventSubscriptions.ABSENT and loop is not self._loop:
            # queued 동기 콜백은 스레드에서 실행하므로 asyncio 객체는 소유 루프에서 정리한다.
            self._loop.call_soon_threadsafe(self._unsubscribe, entry)
        else:
            self._unsubscribe(entry)

    @staticmethod
    def _discard_pending(entry):
        queue = entry.queue
        if queue is not EventSubscriptions.ABSENT:
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
            entry.space.set()

    def _unsubscribe(self, entry):
        if not entry.active:
            return
        entry.active = SubscriptionEntry.INACTIVE
        self._discard_pending(entry)
        if entry.worker is not EventSubscriptions.ABSENT:
            # 큐를 비운 뒤 종료 신호를 넣는다. 실행 중인 콜백은 취소하지 않는다.
            entry.queue.put_nowait(EventSubscriptions.ABSENT)
        else:
            self._retire(entry)

    def _retire(self, entry):
        if entry.closed or entry.active or entry.invoking or entry.worker is not EventSubscriptions.ABSENT:
            return
        entry.closed = SubscriptionEntry.CLOSED
        self._subscriptions.pop(id(entry), EventSubscriptions.ABSENT)
        self._retired.retire(entry.counters)
        # 종료된 handle에는 통계만 남긴다. owner는 구독별 이력을 보관하지 않는다.
        entry.callback = entry.queue = entry.space = EventSubscriptions.ABSENT
        entry.done.set()
        entry.done = EventSubscriptions.ABSENT

    @staticmethod
    async def _wait_closed(entry):
        if not entry.closed:
            await entry.done.wait()

    async def _invoke(self, entry, args):
        callback = entry.callback
        if entry.delivery == EventDelivery.QUEUED.value and not inspect.iscoroutinefunction(callback):
            result = await asyncio.to_thread(callback, *args)
        else:
            result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _call(self, entry, args, on_error):
        guard = timeout(entry.timeout)
        started, status = time.monotonic(), ObservationStatus.DELIVERED.value
        entry.invoking += SubscriptionEntry.INVOCATION_INCREMENT
        token = _current_subscription.set(entry)
        try:
            async with guard:
                await self._invoke(entry, args)
            entry.counters.delivered += DeliveryCounters.INCREMENT
        except asyncio.TimeoutError:
            status = ObservationStatus.FAILED.value
            # 멈춘 동기 콜백 스레드는 강제 종료할 수 없다. 구독을 꺼 후속 스레드 증가를 막는다.
            expired = guard.expired() if callable(guard.expired) else guard.expired
            if expired:
                self._unsubscribe(entry)
                entry.counters.timed_out += DeliveryCounters.INCREMENT
                status = ObservationStatus.TIMED_OUT.value
            entry.counters.failures += DeliveryCounters.INCREMENT
            if on_error is not EventSubscriptions.ABSENT:
                on_error()
        except asyncio.CancelledError:
            status = EventSubscriptions.ABSENT
            if entry.delivery == EventDelivery.INLINE.value or asyncio.current_task().cancelling():
                raise
            if on_error is not EventSubscriptions.ABSENT:
                on_error()
        except Exception:
            status = ObservationStatus.FAILED.value
            entry.counters.failures += DeliveryCounters.INCREMENT
            # 관찰자 실패가 이미 저장된 실행 결과를 바꾸면 안 된다.
            if on_error is not EventSubscriptions.ABSENT:
                on_error()
        finally:
            if (
                self.observability is not EventSubscriptions.ABSENT
                and status is not EventSubscriptions.ABSENT
            ):
                self.observability.record(
                    ObservationKind.EVENTS.value, status, duration_seconds=time.monotonic() - started
                )
            _current_subscription.reset(token)
            entry.invoking -= SubscriptionEntry.INVOCATION_INCREMENT
            self._retire(entry)

    async def _consume(self, entry):
        queue = entry.queue
        try:
            while True:
                item = await queue.get()
                entry.space.set()
                try:
                    if item is EventSubscriptions.ABSENT:
                        return
                    args, on_error = item
                    if entry.active:
                        await self._call(entry, args, on_error)
                finally:
                    # idle worker도 이전 Run/event/on_error를 붙잡지 않도록 해제한다.
                    item = args = on_error = EventSubscriptions.ABSENT
                    queue.task_done()
        finally:
            entry.active = SubscriptionEntry.INACTIVE
            self._discard_pending(entry)
            entry.worker = EventSubscriptions.ABSENT
            self._retire(entry)

    async def _enqueue(self, entry, snapshot, on_error):
        if entry.queue is EventSubscriptions.ABSENT:
            entry.queue = asyncio.Queue(maxsize=entry.size)
            entry.space = asyncio.Event()
            token = _current_subscription.set(EventSubscriptions.ABSENT)
            try:
                entry.worker = asyncio.create_task(self._consume(entry))
            finally:
                _current_subscription.reset(token)
        queue, space = entry.queue, entry.space
        if entry.overflow == EventOverflow.DROP_OLDEST.value:
            if queue.full():
                queue.get_nowait()
                queue.task_done()
                entry.counters.dropped += DeliveryCounters.INCREMENT
                if self.observability is not EventSubscriptions.ABSENT:
                    self.observability.record(ObservationKind.EVENTS.value, ObservationStatus.DROPPED.value)
        else:
            # queue.put만 기다리면 해제 후 종료 신호 뒤에 알림을 넣을 수 있다.
            # 공간/해제 신호마다 상태를 재검사해 막힌 생산자도 빠져나오게 한다.
            while entry.active and queue.full():
                space.clear()
                await space.wait()
        if entry.active:
            queue.put_nowait((snapshot, on_error))

    async def _close(self):
        await self.flush()
        entries = tuple(self._subscriptions.values())
        for entry in entries:
            self._unsubscribe(entry)
        for entry in entries:
            await self._wait_closed(entry)

    # 공개 API
    @property
    def stats(self):
        """현재 구독과 회수된 구독의 숫자 합계. pending은 살아 있는 큐에서만 읽는다."""
        entries = [Subscription(entry, self).stats for entry in tuple(self._subscriptions.values())]
        return SubscriptionStats.aggregate(self._retired, entries)

    def subscribe(
        self,
        callback: Callable,
        *,
        channel: str = EventChannel.ENGINE.value,
        delivery: str = EventDelivery.INLINE.value,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        overflow: str = EventOverflow.BLOCK.value,
        callback_timeout: Optional[float] = ABSENT,
    ) -> Subscription:
        """queued/drop_oldest는 실행을 막지 않는 UI용 알림이다. 누락 시 저장소를 재조회한다.

        기본 block은 순서를 유지하며 생산자에게 backpressure를 적용한다.
        callback_timeout은 정지한 구독을 비활성화한다. 동기 inline 콜백은 직접 차단하지 말아야 한다.
        """
        if self._closed:
            raise RuntimeError(EventSubscriptions.CLOSED_MESSAGE)
        if (
            not callable(callback)
            or channel not in (EventChannel.ENGINE.value, EventChannel.RUN.value)
            or delivery not in (EventDelivery.INLINE.value, EventDelivery.QUEUED.value)
        ):
            raise ValueError(EventSubscriptions.INVALID)
        if type(buffer_size) is not int or buffer_size < EventSubscriptions.MINIMUM_BUFFER_SIZE:
            raise ValueError(EventSubscriptions.INVALID_BUFFER)
        if overflow not in (EventOverflow.BLOCK.value, EventOverflow.DROP_OLDEST.value) or (
            delivery == EventDelivery.INLINE.value and overflow != EventOverflow.BLOCK.value
        ):
            raise ValueError(EventSubscriptions.INVALID_OVERFLOW)
        positive_seconds(callback_timeout, EventSubscriptions.TIMEOUT_LABEL)
        entry = SubscriptionEntry.create(
            callback, channel, delivery, buffer_size, overflow, callback_timeout, asyncio.Event()
        )
        self._subscriptions[id(entry)] = entry
        return Subscription(entry, self)

    async def publish(self, channel: str, *args, on_error: Optional[Callable] = ABSENT):
        self._bind_loop()
        if self._closed:
            raise RuntimeError(EventSubscriptions.CLOSED_MESSAGE)
        for entry in tuple(self._subscriptions.values()):
            if not entry.active or entry.channel != channel:
                continue
            snapshot = deepcopy(args)
            if entry.delivery == EventDelivery.INLINE.value:
                await self._call(entry, snapshot, on_error)
            else:
                await self._enqueue(entry, snapshot, on_error)

    async def flush(self):
        """현재까지 큐가 수락한 알림의 처리가 끝날 때까지 기다린다."""
        self._bind_loop()
        for entry in tuple(self._subscriptions.values()):
            if entry.queue is not EventSubscriptions.ABSENT:
                await entry.queue.join()

    async def close(self):
        """생산자가 종료된 후 호출하며, 수락된 알림을 모두 전달하고 종료한다."""
        self._bind_loop()
        if id(_current_subscription.get()) in self._subscriptions:
            raise RuntimeError(EventSubscriptions.SELF_CLOSE)
        if self._closing is EventSubscriptions.ABSENT:
            self._closed = EventSubscriptions.CLOSED
            self._closing = asyncio.create_task(self._close())
        # 대기자의 취소가 다른 대기자/진행 중인 콜백의 정리를 취소하지 않는다.
        await asyncio.shield(self._closing)
