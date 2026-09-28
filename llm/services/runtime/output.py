"""출력 델타만 제한된 크기로 묶는다. 다른 이벤트는 저장 완료를 기다리는 실행 경계다."""

import asyncio
import math
from dataclasses import replace

from llm.compat import dataclass
from llm.engines.base import EngineEventType
from llm.core.results import EngineOutput, EngineDelta


class RunOutputState:
    """한 Run의 출력 계약과 누적 본문만 관리한다. 저장·Step 수명·UI 알림은 소유하지 않는다."""

    def __init__(self):
        self.outputs = {}
        self.visible_text = {}
        self.sequence = 0

    def accept(self, event, active_steps, *, has_result=False):
        """검증된 이벤트와 대화 본문 변경을 반환한다. 호출자는 저장 후에만 알린다."""
        if event.delta is not None and (event.type != EngineEventType.TEXT_DELTA or event.output is not None):
            raise ValueError("Delta payload requires TEXT_DELTA without an output")
        if event.output is not None and not isinstance(event.output, EngineOutput):
            raise TypeError("EngineEvent.output requires EngineOutput")
        value = event.delta if event.type == EngineEventType.TEXT_DELTA else event.output
        if event.type == EngineEventType.TEXT_DELTA and not isinstance(value, EngineDelta):
            raise ValueError("TEXT_DELTA requires EngineDelta")
        if event.type == EngineEventType.OUTPUT and not isinstance(value, EngineOutput):
            raise ValueError("OUTPUT requires EngineOutput")
        if value is None:
            return event, None
        if not isinstance(value, (EngineDelta, EngineOutput)):
            raise TypeError("Output payload must be an output data class")
        if event.type not in (EngineEventType.TEXT_DELTA, EngineEventType.OUTPUT, EngineEventType.STEP_COMPLETED):
            raise ValueError("Output payload is not supported by this event")
        if value.step_id != event.step_id:
            raise ValueError("Output and event Step ownership mismatch")
        if event.type == EngineEventType.STEP_COMPLETED and value.step_id is None:
            raise ValueError("Step output requires a Step ID")
        previous = self.outputs.get(value.output_id)
        if previous is not None and (previous.final or
                (previous.step_id, previous.visibility) != (value.step_id, value.visibility)):
            raise ValueError("Output is finalized or changed ownership")
        if value.step_id is not None and value.step_id not in active_steps:
            raise ValueError("Output requires a running Step")
        value = replace(value, sequence=self.sequence + 1)
        if isinstance(value, EngineDelta):
            previous = previous or EngineOutput(value.output_id, step_id=value.step_id,
                                                visibility=value.visibility, final=False)
            updated = previous.apply(value)
            event = replace(event, delta=value)
        else:
            if not value.final:
                raise ValueError("Engine result must be final; use deltas for partial output")
            if value.step_id is None and has_result:
                raise ValueError("Run already has a final output")
            # 확정 출력은 이후 델타를 거부할 식별 정보만 유지한다. 큰 결과는 저장소가 소유한다.
            updated = replace(value, text="", data=None, metadata={})
            event = replace(event, output=value)
        # 모든 검증에 성공한 뒤에만 상태와 순서를 반영한다.
        self.sequence = value.sequence
        self.outputs[value.output_id] = updated
        change = None
        if isinstance(value, EngineDelta) and value.visibility == "user":
            self.visible_text[value.output_id] = updated.text
            change = ((value.text, "append") if value.operation == "append" else
                      ("".join(self.visible_text.values()), "replace"))
        return event, change


@dataclass(frozen=True)
class OutputPolicy:
    """기본값 1은 즉시 저장. 묶음 모드는 다음 델타까지 Engine을 미리 진행할 수 있다."""

    batch_size: int = 1
    max_delay: float = 0.025
    max_chars: int = 65536

    def __post_init__(self):
        for name in ("batch_size", "max_chars"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (isinstance(self.max_delay, bool) or not isinstance(self.max_delay, (int, float))
                or not math.isfinite(self.max_delay) or self.max_delay <= 0):
            raise ValueError("max_delay must be positive and finite")

    def for_project(self, settings):
        """Run 시작 시 저장한 프로젝트 정책을 적용한다. null은 호스트 기본값을 유지한다."""
        return OutputPolicy(**{name: settings[name] if settings.get(name) is not None else getattr(self, name)
                               for name in ("batch_size", "max_delay", "max_chars")})


async def consume_events(events, policy, handle):
    """유한 버퍼와 시간 경계. Tool 승인/Step/체크포인트는 절대 미리 ACK하지 않는다."""
    if policy.batch_size == 1:
        async for event in events:
            await handle([event])
        return
    # generator 전체를 하나의 Task에서 실행해야 timeout/context manager의 소유 Task가 유지된다.
    queue = asyncio.Queue(maxsize=policy.batch_size)
    buffer, chars, deadline = [], 0, None
    loop = asyncio.get_running_loop()

    stopping = asyncio.Event()

    async def produce():
        failure = None
        try:
            async for event in events:
                barrier = None if event.type == EngineEventType.TEXT_DELTA else asyncio.Event()
                await queue.put(("event", event, barrier))
                if barrier is not None:
                    await barrier.wait()
        except BaseException as error:
            failure = error
        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException as error:
                    if failure is None:
                        failure = error
        if not stopping.is_set():
            await queue.put(("error" if failure is not None else "end", failure, None))

    async def flush():
        nonlocal buffer, chars, deadline
        accepted, buffer = buffer, []
        chars, deadline = 0, None
        if accepted:
            # handle의 저장 부분만 취소를 drain한다. 관찰 콜백까지 강제로 기다리지 않는다.
            await handle(accepted)

    producer = asyncio.create_task(produce())
    try:
        while True:
            delay = None if deadline is None else max(0, deadline - loop.time())
            try:
                kind, event, barrier = await asyncio.wait_for(queue.get(), timeout=delay)
            except asyncio.TimeoutError:
                await flush()
                continue
            if kind == "end":
                break
            if kind == "error":
                raise event
            if event.type == EngineEventType.TEXT_DELTA:
                buffer.append(event)
                chars += len(getattr(event.delta, "text", ""))
                if deadline is None:
                    deadline = loop.time() + policy.max_delay
                if len(buffer) >= policy.batch_size or chars >= policy.max_chars:
                    await flush()
            else:
                await flush()
                await handle([event])
                barrier.set()
    finally:
        stopping.set()
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await flush()
