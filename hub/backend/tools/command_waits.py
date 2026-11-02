"""Run-local cooperative observation of native processes; no process ownership store."""

import asyncio
import math
from contextlib import contextmanager
from dataclasses import dataclass, field

from llm.components.tools.builtin.process_values import (
    ProcessReferenceArguments, ProcessStatus, ProcessStartArguments, ProcessOutputArguments, ProcessSnapshot,
)
from llm.services.runtime.tools import current_tool_call

from ...config.defaults import HubDefaults


@dataclass(slots=True)
class CommandWait:
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class CommandWaits:
    """A persisted steering ACK wakes observation, never consumes the instruction."""

    WAIT_SECONDS = "wait_seconds"
    DESCRIPTION = (
        "Inspect a process. In a Hub conversation, wait briefly for completion or new user "
        "instructions before returning its current status. Set wait_seconds=0 for an immediate "
        "snapshot. A running result is not completion; continue observing the same process_id, "
        "do not start the command again."
    )

    def __init__(self):
        self.runs: dict[tuple[str, str, str], CommandWait] = {}

    @contextmanager
    def scope(self, context):
        key = context.project.id, context.session.id, context.run.id
        waiter = CommandWait()
        self.runs[key] = waiter
        try:
            yield
        finally:
            if self.runs.get(key) is waiter:
                del self.runs[key]

    def notify(self, project_id, session_id, run_id, targets):
        from llm.core.steering_channel import SteeringChannel

        # This adapter handles the root conversational Loop, not Graph child routes.
        if SteeringChannel.ROOT in targets:
            waiter = self.runs.get((project_id, session_id, run_id))
            if waiter is not None:
                waiter.changed.set()

    async def status(self, read, arguments):
        args = dict(arguments)
        supplied = args.pop(self.WAIT_SECONDS, None)
        call = current_tool_call()
        waiter = (self.runs.get((call.project_id, call.session_id, call.run_id))
                  if call is not None else None)
        seconds = (HubDefaults.COMMAND_WAIT_SECONDS if waiter is not None else 0) if supplied is None else supplied
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("wait_seconds must be finite and nonnegative")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            value = await read(args)
            if value[ProcessSnapshot.status.__name__] != ProcessStatus.RUNNING or loop.time() >= deadline:
                return value
            if waiter is not None and waiter.changed.is_set():
                waiter.changed.clear()
                return value
            delay = min(HubDefaults.COMMAND_POLL_SECONDS, deadline - loop.time())
            if delay <= 0:
                return value
            if waiter is None:
                await asyncio.sleep(delay)
            else:
                try:
                    async with asyncio.timeout(delay):
                        await waiter.changed.wait()
                except TimeoutError:
                    pass

    @classmethod
    def parameters(cls, original):
        from copy import deepcopy

        schema = deepcopy(original)
        schema["properties"][cls.WAIT_SECONDS] = {
            "type": "number", "minimum": 0,
            "description": "Observation wait in seconds, not a command timeout. Zero returns immediately.",
        }
        return schema


class ConversationalCommands:
    """Expose existing asynchronous tools only within the Agent's selected ceiling."""

    TOOLS = frozenset((ProcessStartArguments.NAME, ProcessReferenceArguments.STATUS_NAME,
                       ProcessOutputArguments.NAME, ProcessReferenceArguments.CANCEL_NAME))
    GUIDANCE = (
        "For commands, use process_start with argv (for shell syntax: "
        "[\"/bin/sh\", \"-c\", \"command\"]). It starts once and returns process_id. "
        "Use process_status to wait and process_output to read bounded pages; running is not "
        "completed. Respond to new user instructions while the process runs, then keep observing "
        "the same ID unless told otherwise. Use process_cancel to stop it when requested; "
        "never rerun a running or uncertain command. Background commands keep running when a "
        "conversation ends and are cleaned up when Hub shuts down. Approval, cwd/env and "
        "execution limits still apply."
    )

    @classmethod
    def available(cls, names):
        return cls.TOOLS <= set(names)

    @classmethod
    def select(cls, names, available):
        from llm.components.tools.builtin.process_values import ShellArguments

        result = set(names)
        if cls.available(available):
            result.update(cls.TOOLS)
            result.discard(ShellArguments.NAME)
        return result
