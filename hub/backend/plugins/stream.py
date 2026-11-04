"""Lossless, cursor-based delivery of the backend's common output contract."""

import asyncio

from llm.core.models import MessageStatus, Run
from llm.core.results import EngineDelta, OutputVisibility


class PluginStream:
    PAGE_SIZE = 256
    RECONCILE_SECONDS = 15.0
    TERMINAL_MESSAGES = frozenset((MessageStatus.COMPLETED, MessageStatus.FAILED,
        MessageStatus.CANCELLED, MessageStatus.INTERRUPTED, MessageStatus.PAUSED))

    def __init__(self):
        self.revision = 0
        self.changed = asyncio.Event()
        self.closed = False

    def close(self):
        self.closed = True
        self.changed.set()

    def signal(self):
        self.revision += 1
        self.changed.set()

    async def read(self, request, cursors, revision, *, include_internal=False):
        if self.closed:
            raise RuntimeError("Hub output subscription is closed")
        if revision == self.revision:
            self.changed.clear()
            try:
                async with asyncio.timeout(self.RECONCILE_SECONDS):
                    await self.changed.wait()
            except TimeoutError:
                pass  # Lost observation signals never lose persisted outputs.
        if self.closed:
            raise RuntimeError("Hub output subscription is closed")
        observed = self.revision
        message = await request.aget_data()
        records, output, positions = [], [], dict(cursors)
        more = False
        for handle in await request.aget_runs():
            run = await handle.aget_data()
            events = await handle.aoutput_events(after=positions.get(run.id, 0), limit=self.PAGE_SIZE)
            more |= len(events) == self.PAGE_SIZE
            for event in events:
                positions[run.id] = event.sequence
                if include_internal or event.visibility == OutputVisibility.USER:
                    output.append({"run_id": run.id, "agent_id": run.agent_id,
                                   "kind": "delta" if isinstance(event, EngineDelta) else "output",
                                   "value": event.to_dict()})
            records.append({"id": run.id, "agent_id": run.agent_id, "engine": run.engine,
                "status": run.status.value, "error": run.error, "error_code": run.error_code,
                "interactions": [item.to_dict() for item in await handle.ainteraction_views()]})
        # The committed USER message remains committed after execution. The
        # public request query validates the complete recipient Run group.
        terminal = ((bool(records) and all(row["status"] not in Run.ACTIVE_STATES for row in records))
                    or (not records and message.status in self.TERMINAL_MESSAGES)) and not more
        return {"request_id": request.id, "status": message.status.value, "runs": records,
                "outputs": output, "cursors": positions,
                "revision": -1 if more else observed, "terminal": terminal}
