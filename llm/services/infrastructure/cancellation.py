"""Owned-task cancellation and collection; never infer effects or retry permission."""

import asyncio
from collections.abc import Iterable

from .storage import drain_on_cancel


async def cancel_tasks(tasks: Iterable[asyncio.Task]) -> None:
    """Cancel children once and finish collection despite repeated caller cancellation.

    An already cancelling child may be closing a same-task SDK context. Sending
    another cancel would interrupt that cleanup. The owner decides when to call
    this helper; storage publication and result/error precedence remain its own.
    Child exceptions are collected, as in a finally-block gather. This helper is
    not the result of executing those children and must not authorize a retry.
    """
    children = tuple(tasks)
    if asyncio.current_task() in children:
        raise ValueError("Cancellation owner cannot collect itself")
    for task in children:
        if not task.done() and not task.cancelling():
            task.cancel()
    if children:
        await drain_on_cancel(asyncio.gather(*children, return_exceptions=True))
