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


async def recovery_events(selector, engine, context, environment, *, provider=None):
    # Keep the ordinary rendering/execution path unchanged when retries are off.
    retries = ToolRetryPolicy.from_run(context.run)
    if not retries.max_retries and not (provider and provider.settings.recovery_enabled):
        async with aclosing(engine.execute(context)) as events:
            async for event in events:
                yield event
        return

    queue = asyncio.Queue(maxsize=1)

    async def emit(event):
        acknowledgement = asyncio.get_running_loop().create_future()
        await queue.put((event, acknowledgement))
        await acknowledgement

    async def produce():
        frame = RecoveryContext(context, engine, environment, emit)
        token = selector.context.set(frame)
        try:
            with provider.bind(frame) if provider is not None else nullcontext():
                async with aclosing(engine.execute(context)) as events:
                    async for event in events:
                        await emit(event)
        finally:
            selector.context.reset(token)

    producer = asyncio.create_task(produce())
    reader = None
    try:
        while not producer.done() or not queue.empty():
            reader = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait((reader, producer), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                event, acknowledgement = reader.result()
                reader = None
                yield event
                # The backend has persisted/admitted the event before any more effects.
                if not acknowledgement.done():
                    acknowledgement.set_result(None)
            else:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
                reader = None
        await producer
    finally:
        if reader is not None:
            reader.cancel()
        producer.cancel()
        await asyncio.gather(producer, *([reader] if reader is not None else []), return_exceptions=True)
