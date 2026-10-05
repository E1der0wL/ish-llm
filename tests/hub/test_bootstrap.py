"""Hub 소유의 최초 생성/선택과 llm의 일반 CRUD 경계를 검증한다."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.bootstrap import select_or_create
from hub.backend.runtime import HubConfig, HubRuntime, create_backend
from hub.asset.guides import SYSTEM_PROMPT


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_client_does_not_trigger_a_false_missing_model_guard(self):
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, chunk
        provider = ScriptedCompletion([chunk("hello", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False, engine_factories={
                "loop": lambda: LoopEngine(completion_fn=provider,
                    completion_kwargs={"model": "test/host", "client": object()})}))
            try:
                await runtime.start()
                await runtime.new_session()
                self.assertFalse((await runtime.snapshot()).model_required)
                await runtime.submit(runtime.selected_id, "hello")
                await runtime.sessions[runtime.selected_id].run.wait_idle()
                run = (await runtime.sessions[runtime.selected_id].run.alist())[-1]
                self.assertEqual(str((await run.aresult()).status), "completed")
                self.assertEqual(provider.requests[0]["model"], "test/host")
                self.assertEqual((await runtime.project.aget_data()).config.parameters,
                                 {"engines": {"loop": {'config': {'system_prompt': SYSTEM_PROMPT}}}})
            finally:
                await runtime.close()

    async def test_session_model_is_visible_and_used_without_project_model(self):
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, chunk
        provider = ScriptedCompletion([chunk("hello", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, engine="writer", auto_title=False,
                engine_factories={"writer": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                session = await runtime.project.sessions.acreate(config={"parameters": {
                    "engines": {"writer": {'config': {'completion': {'model': 'test/session'}}}}}})
                await runtime.activate_project(runtime.project, session_id=session.id)
                view = await runtime.snapshot()
                self.assertEqual(view.model, "test/session")
                self.assertFalse(view.model_required)
                await runtime.submit(session.id, "hello")
                await runtime.sessions[session.id].run.wait_idle()
                self.assertEqual(provider.requests[0]["model"], "test/session")
                self.assertEqual((await runtime.project.aget_data()).config.parameters,
                                 {"engines": {"loop": {'config': {'system_prompt': SYSTEM_PROMPT}}}})
            finally:
                await runtime.close()

    async def test_concurrent_start_creates_once_and_reopen_preserves_edits(self):
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model="test/initial", auto_title=False)
            async with create_backend(config) as a, create_backend(config) as b:
                first, second = await asyncio.gather(select_or_create(a, config), select_or_create(b, config))
                self.assertEqual(first.id, second.id)
                self.assertEqual(len(await a.projects.alist()), 1)
                self.assertEqual(await first.sessions.alist(), [])
                record = await first.aget_data()
                record.config.parameters["engines"]["loop"]["config"]["completion"]["model"] = "test/edited"
                await first.asave(title="Edited", config=record.config)
            async with create_backend(config) as app:
                again = await select_or_create(app, config)
                self.assertEqual(again.id, first.id)
                record = await again.aget_data()
                self.assertEqual(record.title, "Edited")
                self.assertEqual(record.config.parameters["engines"]["loop"]["config"]["completion"]["model"], "test/edited")
                self.assertFalse(list(Path(root).rglob("default-project.json")))

    async def test_failed_create_releases_lock_without_reference_record(self):
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, auto_title=False)
            async with create_backend(config) as app:
                with patch.object(app.projects, "acreate", side_effect=OSError("create failure")):
                    with self.assertRaises(OSError):
                        await select_or_create(app, config)
                self.assertEqual(await app.projects.alist(), [])
                project = await select_or_create(app, config)
                self.assertEqual((await project.aget_data()).config.parameters,
                                 {"engines": {"loop": {'config': {'system_prompt': SYSTEM_PROMPT}}}})
                self.assertEqual(await project.sessions.alist(), [])

    async def test_cancelled_lock_wait_does_not_create_and_future_start_works(self):
        import fcntl
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / ".hub"
            folder.mkdir()
            config = HubConfig(root, auto_title=False)
            async with create_backend(config) as app:
                with (folder / "startup.lock").open("w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    task = asyncio.create_task(select_or_create(app, config))
                    await asyncio.sleep(.02)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertEqual(await app.projects.alist(), [])
                self.assertIsNotNone(await select_or_create(app, config))
