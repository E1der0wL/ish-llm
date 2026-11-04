"""Official async Agent API for ish plugins. No backend handles leave its loop.

Use ``hub.hub.client(prompt, plugin_id)`` after installing Hub. Closing an
iterator only detaches observation; cancel/interrupt are explicit operations.
"""

import asyncio
from copy import deepcopy
from uuid import uuid4

from .backend.plugins.definitions import PluginDefinition, PluginContext, PluginIdentity

__all__ = ["PluginClient", "PluginRequest", "PluginDefinition", "PluginContext"]


class PluginClient:
    def __init__(self, plugin_id, worker):
        self.plugin_id = PluginIdentity.validate(plugin_id)
        self._worker = worker

    async def _call(self, operation, *args):
        # Resolve the current installation for every call, not a stale worker
        # captured before a Hub restart. Input is detached before crossing loops.
        future = self._worker().call("plugin_call", operation, self.plugin_id, *deepcopy(args))
        return await asyncio.wrap_future(future)

    async def register(self, definition: PluginDefinition):
        return await self._call("register", definition)

    async def open_session(self, key: str, *, agents: list[str]):
        """Create/reuse a durable hidden session; a key identifies one serial lane."""
        return await self._call("open_session", key, agents)

    async def submit(self, session_id: str, text: str, *, recipients: list[str],
                     context: PluginContext | None = None, request_key: str | None = None):
        """Return only after QUEUED is durable. Supply a stable key for retrying admission."""
        reference = await self._call("submit", session_id, text, recipients, context,
                                     request_key if request_key is not None else uuid4().hex)
        return PluginRequest(self, reference)

    async def reconnect(self, session_id: str, request_id: str):
        """Reopen saved observations after restart; never replays the request."""
        return PluginRequest(self, await self._call("reconnect", session_id, request_id))

    async def receipt(self, session_id: str, request_key: str):
        return await self._call("receipt", session_id, request_key)


class PluginRequest:
    def __init__(self, client, reference):
        self.client = client
        self._reference = deepcopy(reference)

    @property
    def reference(self):
        return deepcopy(self._reference)

    async def _call(self, operation, *args):
        return await self.client._call(operation, self._reference, *args)

    async def events(self, *, cursors=None, include_internal=False):
        """Yield persisted batches; consumers apply delta append/replace by output ID.

        Internal observations are opt-in and are never verified public answers.
        A terminal batch can be paused/failed/interrupted, not only completed.
        Keep ``cursors`` to reconnect without replaying already displayed deltas.
        """
        positions, revision = dict(cursors or {}), -1
        while True:
            batch = await self._call("poll", positions, revision, include_internal)
            positions, revision = batch["cursors"], batch["revision"]
            yield batch
            if batch["terminal"]:
                return

    async def cancel(self):
        """Cancel an undispatched request, without cancelling other queued work."""
        return await self._call("cancel")

    async def interrupt(self, run_id):
        return await self._call("interrupt", run_id)

    async def steer(self, run_id, text, *, request_key=None):
        return await self._call("steer", run_id, text, request_key or uuid4().hex)

    async def respond(self, run_id, response: dict, *, request_key=None):
        """Persist an explicit user decision. This does not resume execution."""
        return await self._call("respond", run_id, response, request_key or uuid4().hex)

    async def cancel_interaction(self, run_id, interaction: dict):
        return await self._call("cancel_interaction", run_id, interaction)

    async def answer_live(self, run_id, question: dict, option_id, value):
        """Answer a Hub live question; its active Tool continues without resume."""
        return await self._call("answer_live", run_id, question, option_id, value)

    async def resume_plan(self, run_id):
        return await self._call("resume_plan", run_id)

    async def resume(self, run_id, **options):
        return PluginRequest(self.client, await self._call("resume", run_id, options))
