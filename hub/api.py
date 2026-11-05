"""Official async Agent API for ish plugins. No backend handles leave its loop.

Use ``hub.hub.client(prompt, plugin_id)`` after installing Hub. Closing an
iterator only detaches observation; cancel/interrupt are explicit operations.
"""

import asyncio
from copy import deepcopy
from uuid import uuid4

from .backend.plugins.definitions import PluginDefinition, PluginContext, PluginIdentity
from .backend.plugins.protocol import PluginOperation, PluginReference, PluginBatch, PluginTransport, PluginDelivery

__all__ = ["PluginClient", "PluginRequest", "PluginDefinition", "PluginContext"]


class PluginClient:
    def __init__(self, plugin_id, worker):
        self.plugin_id = PluginIdentity.validate(plugin_id)
        self._worker = worker

    async def _call(self, operation, *args):
        # Resolve the current installation for every call, not a stale worker
        # captured before a Hub restart. Input is detached before crossing loops.
        future = self._worker().call(PluginTransport.DISPATCH, operation, self.plugin_id, *deepcopy(args))
        return await asyncio.wrap_future(future)

    async def register(self, definition: PluginDefinition):
        return await self._call(PluginOperation.REGISTER, definition)

    async def open_session(self, key: str, *, agents: list[str]):
        """Create/reuse a durable hidden session; a key identifies one serial lane."""
        return await self._call(PluginOperation.OPEN_SESSION, key, agents)

    async def submit(self, session_id: str, text: str, *, recipients: list[str],
                     context: PluginContext | None = None, request_key: str | None = None):
        """Return only after QUEUED is durable. Supply a stable key for retrying admission."""
        reference = await self._call(PluginOperation.SUBMIT, session_id, text, recipients, context,
                                     request_key if request_key is not None else uuid4().hex)
        return PluginRequest(self, reference)

    async def reconnect(self, session_id: str, request_id: str):
        """Reopen saved observations after restart; never replays the request."""
        return PluginRequest(self, await self._call(PluginOperation.RECONNECT, session_id, request_id))

    async def receipt(self, session_id: str, request_key: str):
        return await self._call(PluginOperation.RECEIPT, session_id, request_key)


class PluginRequest:
    def __init__(self, client, reference):
        self.client = client
        self._reference = PluginReference.from_dict(reference)

    @property
    def reference(self):
        return self._reference.to_dict()

    async def _call(self, operation, *args):
        return await self.client._call(operation, self.reference, *args)

    async def events(self, *, cursors=None, include_internal=False):
        """Yield persisted batches; consumers apply delta append/replace by output ID.

        Internal observations are opt-in and are never verified public answers.
        A terminal batch can be paused/failed/interrupted, not only completed.
        Keep ``cursors`` to reconnect without replaying already displayed deltas.
        """
        positions, revision = dict(cursors or {}), PluginBatch.INITIAL_REVISION
        while True:
            batch = PluginBatch.from_dict(await self._call(PluginOperation.POLL, positions, revision, include_internal))
            positions, revision = batch.cursors, batch.revision
            yield batch.to_dict()
            if batch.terminal:
                return

    async def cancel(self):
        """Cancel an undispatched request, without cancelling other queued work."""
        return await self._call(PluginOperation.CANCEL)

    async def interrupt(self, run_id):
        return await self._call(PluginOperation.INTERRUPT, run_id)

    async def steer(self, run_id, text, *, request_key=None):
        return await self._call(PluginOperation.STEER, run_id, text, request_key if request_key is not None else uuid4().hex)

    async def respond(self, run_id, response: dict, *, request_key=None):
        """Persist an explicit user decision. This does not resume execution."""
        return await self._call(PluginOperation.RESPOND, run_id, response, request_key if request_key is not None else uuid4().hex)

    async def cancel_interaction(self, run_id, interaction: dict):
        return await self._call(PluginOperation.CANCEL_INTERACTION, run_id, interaction)

    async def questions(self, run_id):
        """Read durable nonblocking questions even after the source Run ended."""
        return await self._call(PluginOperation.QUESTIONS, run_id)

    async def answer_question(self, run_id, response: dict):
        """Save a user answer only; no model call, resume or submission."""
        return await self._call(PluginOperation.ANSWER_QUESTION, run_id, response)

    async def cancel_question(self, run_id, request: dict):
        return await self._call(PluginOperation.CANCEL_QUESTION, run_id, request)

    async def deliver_answer(self, run_id, request_id, *, mode, **options):
        """Explicit delivery with native per-question idempotency; not application proof."""
        return await self._call(PluginOperation.DELIVER_ANSWER, run_id, request_id,
            PluginDelivery(mode=mode, **options).to_dict())

    async def get_delivery(self, run_id, request_id):
        return await self._call(PluginOperation.GET_DELIVERY, run_id, request_id)

    async def question_targets(self, run_id):
        return await self._call(PluginOperation.QUESTION_TARGETS, run_id)

    async def resume_plan(self, run_id):
        return await self._call(PluginOperation.RESUME_PLAN, run_id)

    async def resume(self, run_id, **options):
        return PluginRequest(self.client, await self._call(PluginOperation.RESUME, run_id, options))
