"""Hub selects native batching without delaying durable execution barriers."""
import asyncio
from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, create_backend
from hub.config.defaults import HubDefaults
from llm.core.policies import PolicySection
from llm.core.results import EngineDelta
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.services.runtime.output import consume_events


class OutputBatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sparse_text_is_persisted_before_the_stream_finishes(self):
        persisted, finish = asyncio.Event(), asyncio.Event()
        async def stream():
            yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta('out', 'x'))
            await finish.wait()
        async def persist(events):
            self.assertEqual([event.delta.text for event in events], ['x'])
            persisted.set()
        task = asyncio.create_task(consume_events(stream(), HubDefaults.OUTPUT_BUFFER, persist))
        try:
            async with asyncio.timeout(3):
                await persisted.wait()
                self.assertFalse(task.done())
                finish.set()
                await task
        finally:
            finish.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_native_barrier_waits_for_the_batch_and_its_own_commit(self):
        batches, passed = [], asyncio.Event()
        started, committed = asyncio.Event(), asyncio.Event()
        async def stream():
            for _ in range(8):
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta('out', 'x'))
            yield EngineEvent(EngineEventType.STEP_STARTED, step_id='step', kind='tool')
            passed.set()  # Represents code reached only after the durable barrier.
        async def persist(events):
            batches.append(events)
            if events[0].type == EngineEventType.STEP_STARTED:
                started.set()
                await committed.wait()
        task = asyncio.create_task(consume_events(stream(), HubDefaults.OUTPUT_BUFFER, persist))
        try:
            async with asyncio.timeout(3):
                await started.wait()
                self.assertEqual([len(events) for events in batches], [8, 1])
                self.assertFalse(passed.is_set())
                committed.set()
                await task
                self.assertTrue(passed.is_set())
        finally:
            committed.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_host_batching_reduces_syncs_and_keeps_project_override_and_text(self):
        async def stream(context):
            for _ in range(64):
                yield '한글'
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, auto_title=False, postprocess=False,
                engine_factories={'host': lambda: BaseEngine(action=stream)})
            async with create_backend(config) as app:
                self.assertEqual(app.services.output_buffer, HubDefaults.OUTPUT_BUFFER)
                project = await app.projects.acreate('Batching')
                await project.agents.acreate({'engine':'host'}, identifier='host')
                counts = {}
                for name, policy in (('immediate', {'batch_size':1}), ('batched', {})):
                    record = await project.aget_data()
                    values = deepcopy(record.config)
                    values.policies[PolicySection.OUTPUT] = policy
                    await project.asave(config=values)
                    session = await project.sessions.acreate(name, agent_ids=['host'])
                    count = 0
                    fsync = os.fsync
                    def sync(fd):
                        nonlocal count
                        count += 1
                        return fsync(fd)
                    with patch('os.fsync', side_effect=sync):
                        request = await session.run.submit('show', recipients=['host'])
                        runs = await request.wait(timeout=90)
                    counts[name] = count
                    self.assertEqual((await runs[0].aresult()).status, 'completed')
                    self.assertEqual((await runs[0].aresponse()).content, '한글'*64)
                self.assertLess(counts['batched'], counts['immediate'])
