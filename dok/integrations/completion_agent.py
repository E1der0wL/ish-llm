"""Typed Hub transport adapter for completion analysis; no model/backend execution shortcuts."""
import asyncio
from contextlib import aclosing
from uuid import uuid4

from ..features.completion.analysis import HelpAnalysis
from ..locales import Locale


class CompletionAgent:
    def __init__(self, agents, *, locale=Locale):
        self._agents = agents
        self._locale = locale

    async def analyze(self, source, timeout):
        client = self._agents.client()
        if client is None:
            return None
        language = self._locale().language
        from hub.api import PluginContext
        from hub.backend.plugins.protocol import PluginSession, PluginBatch, PluginOutputKind
        from llm.core.results import EngineOutput, OutputVisibility
        from llm.core.models import RunStatus

        admission = None
        terminal = False
        outputs = {}
        try:
            # Keep the admission receipt owned if the caller cancels after Hub
            # persists QUEUED but before submit returns its request handle.
            session = PluginSession.from_dict(await client.open_session(
                "options-" + uuid4().hex, agents=[HelpAnalysis.AGENT_ID]))
            admission = asyncio.create_task(client.submit(session.session_id, HelpAnalysis.request(language),
                recipients=[HelpAnalysis.AGENT_ID],
                context=PluginContext(text=source, source="dok explicit lookup output", max_chars=len(source))))
            request = await asyncio.shield(admission)
            async with asyncio.timeout(timeout), aclosing(request.events()) as events:
                async for raw in events:
                    batch = PluginBatch.from_dict(raw)
                    for entry in batch.outputs:
                        if entry.kind == PluginOutputKind.OUTPUT:
                            output = EngineOutput.from_dict(entry.value)
                            if output.final and output.step_id is None and output.visibility == OutputVisibility.USER:
                                outputs[(entry.run_id, output.output_id)] = output
                    if batch.terminal:
                        terminal = True
                        if not batch.runs or any(run.status != RunStatus.COMPLETED for run in batch.runs):
                            raise ValueError("Hub analysis did not complete")
                        answers = [output.text for output in outputs.values() if output.text.strip()]
                        if not answers:
                            raise ValueError("Hub returned no completion data")
                        return HelpAnalysis.decode(answers[-1], source)
        finally:
            if admission is not None and not terminal:
                # Closing observation alone does not cancel a Run. Cancel admission, then
                # inspect this request's runs and explicitly interrupt active ones.
                async def stop():
                    try:
                        request = await admission
                    except Exception:
                        return  # Admission failed before returning a request.
                    await request.cancel()
                    async with aclosing(request.events()) as events:
                        batch = PluginBatch.from_dict(await anext(events))
                    from llm.core.models import Run
                    for run in batch.runs:
                        if run.status in Run.ACTIVE_STATES:
                            await request.interrupt(run.id)
                task = asyncio.create_task(stop())
                cancelled = False
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                if cancelled:
                    if not task.cancelled():
                        task.exception()
                    raise asyncio.CancelledError
                task.result()
