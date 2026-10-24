"""Relay auxiliary recovery observations through the owning Engine event stream."""

import asyncio
from contextlib import aclosing, nullcontext
from dataclasses import dataclass
from typing import Callable
from llm.core.policies import ToolRetryPolicy


@dataclass(slots=True)
class RecoveryContext:
    context: object
    engine: object
    environment: object
    emit: Callable


class RecoveryRelay:
    QUEUE_CAPACITY = 1
    NO_PROVIDER = None
    NO_READER = None
    ACKNOWLEDGED = None
    CAPTURE_CLEANUP_EXCEPTIONS = True


async def recovery_events(
    selector, engine, context, environment, *, provider=RecoveryRelay.NO_PROVIDER
):
    # Keep the ordinary rendering/execution path unchanged when retries are off.
    retries = ToolRetryPolicy.from_run(context.run)
    if not retries.max_retries and not (
        provider and provider.settings.recovery_enabled
    ):
        async with aclosing(engine.execute(context)) as events:
            async for event in events:
                yield event
        return

    queue = asyncio.Queue(maxsize=RecoveryRelay.QUEUE_CAPACITY)

    async def emit(event):
        acknowledgement = asyncio.get_running_loop().create_future()
        await queue.put((event, acknowledgement))
        await acknowledgement

    async def produce():
        frame = RecoveryContext(context, engine, environment, emit)
        token = selector.context.set(frame)
        try:
            with (
                provider.bind(frame)
                if provider is not RecoveryRelay.NO_PROVIDER
                else nullcontext()
            ):
                async with aclosing(engine.execute(context)) as events:
                    async for event in events:
                        await emit(event)
        finally:
            selector.context.reset(token)

    producer = asyncio.create_task(produce())
    reader = RecoveryRelay.NO_READER
    try:
        while not producer.done() or not queue.empty():
            reader = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                (reader, producer), return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done:
                event, acknowledgement = reader.result()
                reader = RecoveryRelay.NO_READER
                yield event
                # The backend has persisted/admitted the event before any more effects.
                if not acknowledgement.done():
                    acknowledgement.set_result(RecoveryRelay.ACKNOWLEDGED)
            else:
                reader.cancel()
                await asyncio.gather(
                    reader, return_exceptions=RecoveryRelay.CAPTURE_CLEANUP_EXCEPTIONS
                )
                reader = RecoveryRelay.NO_READER
        await producer
    finally:
        if reader is not RecoveryRelay.NO_READER:
            reader.cancel()
        producer.cancel()
        await asyncio.gather(
            producer,
            *([reader] if reader is not RecoveryRelay.NO_READER else []),
            return_exceptions=RecoveryRelay.CAPTURE_CLEANUP_EXCEPTIONS
        )
