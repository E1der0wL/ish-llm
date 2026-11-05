"""Run-local cancellation of a selected invocation, never cancellation of storage ACKs."""

import asyncio
from contextlib import aclosing
from contextvars import copy_context

from llm.errors import CodedError


def _restore_context(context, inherited):
    """Retain binding changes a direct await exposes to its caller's callbacks."""
    for variable, value in context.items():
        if variable not in inherited or value is not inherited[variable]:
            variable.set(value)


class InvocationRedirected(CodedError, Exception):
    code = "execution_redirected"

    def __init__(self, instructions):
        self.instructions = tuple(instructions)
        super().__init__("Invocation interrupted by an explicit direction change")


class InvocationControl:
    """A transient wake-up mirror of durable, unselected redirect instructions.

    Only the invocation task is cancelled. Its caller remains alive to publish
    Steps/checkpoints after cleanup. A returned Tool result wins a cancellation
    race: successful effects must never be discarded or repeated.
    """

    def __init__(self):
        self.pending = set()
        self.task = None
        self.cancel_sent = False

    def request(self, identifier):
        self.pending.add(identifier)
        if self.task is not None and not self.task.done() and not self.cancel_sent:
            self.cancel_sent = True
            self.task.cancel()

    def selected(self, identifiers):
        self.pending.difference_update(identifiers)

    def check(self):
        if self.pending:
            raise InvocationRedirected(self.pending)

    async def call(self, factory):
        self.check()
        if self.task is not None:
            raise RuntimeError("Invocation control cannot own overlapping calls")
        invocation_context = copy_context()
        inherited = dict(invocation_context.items())
        task = self.task = asyncio.create_task(factory(), context=invocation_context)
        self.cancel_sent = False
        try:
            value = await task
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            return value
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling() or not self.cancel_sent:
                raise
            raise InvocationRedirected(self.pending) from None
        finally:
            # A cancellable child must not erase binding changes that a direct
            # await exposes to the caller's error/retry callbacks. Values are
            # propagated without knowing any Host-specific ContextVar names.
            # Tokens themselves remain local to the Context that created them.
            _restore_context(invocation_context, inherited)
            self.task = None
            self.cancel_sent = False

    async def stream(self, iterator):
        # One producer preserves generator ContextVars across yields. A
        # rendezvous ACK prevents read-ahead across execution/storage events.
        queue = asyncio.Queue(maxsize=1)
        acknowledged = asyncio.Event()
        closing = False

        async def produce():
            try:
                async with aclosing(iterator):
                    while True:
                        self.check()
                        if self.task is not None:
                            raise RuntimeError('Invocation control cannot own overlapping calls')
                        self.task = asyncio.current_task()
                        self.cancel_sent = False
                        try:
                            item = await anext(iterator)
                        except asyncio.CancelledError:
                            if self.cancel_sent and not closing:
                                raise InvocationRedirected(self.pending) from None
                            raise
                        finally:
                            self.task = None
                            self.cancel_sent = False
                        self.check()
                        await queue.put(('item', item))
                        await acknowledged.wait()
                        acknowledged.clear()
            except StopAsyncIteration:
                await queue.put(('end', None))
            except BaseException as error:
                if not closing:
                    await queue.put(('error', error))

        invocation_context = copy_context()
        inherited = dict(invocation_context.items())
        producer = asyncio.create_task(produce(), context=invocation_context)
        try:
            while True:
                kind, value = await queue.get()
                if kind == 'end':
                    self.check()
                    return
                if kind == 'error':
                    raise value
                self.check()
                yield value
                acknowledged.set()
        finally:
            closing = True
            from llm.services.infrastructure.cancellation import cancel_tasks
            try:
                await cancel_tasks((producer,))
            finally:
                _restore_context(invocation_context, inherited)
