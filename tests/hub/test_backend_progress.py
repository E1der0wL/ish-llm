"""Foreground stages remain responsive, request-local and bounded by completion."""

import asyncio
from functools import partial
import tempfile
from threading import Event
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.progress import BackendProgress, OperationProgress
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from hub.locales.keys import MessageKey
from hub.ui.application import create_application
from tests.hub.support import SizedOutput, eventually, backend_ready
from tests.hub.test_settings import settings_backend


class BackendProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_reports_wait_then_execution_without_forwarding_stage_to_runtime(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._closed = worker._restarting = False
        worker._failure = None
        worker._commands = set()
        async def settings(operation):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_SCHEMA)
            return operation
        worker.runtime = SimpleNamespace(lock=asyncio.Lock(), dirty=asyncio.Event(), settings=settings)
        updates = []
        task = asyncio.create_task(worker._execute('settings', ('catalog',), stage=updates.append))
        await asyncio.sleep(0)
        self.assertEqual([u.key for u in updates], [MessageKey.PROGRESS_BACKEND_WAIT])
        worker._initialized.set()
        self.assertEqual(await task, 'catalog')
        self.assertEqual([u.key for u in updates], [MessageKey.PROGRESS_BACKEND_WAIT,
            MessageKey.PROGRESS_BACKEND_WORK, MessageKey.PROGRESS_BACKEND_SCHEMA])
        self.assertFalse(worker._commands)

    async def test_concurrent_scopes_and_detached_background_work(self):
        seen = [[], []]
        release = asyncio.Event()
        late = []
        async def inherited():
            await release.wait()
            OperationProgress.report('late')
        async def request(index):
            with OperationProgress.bind(seen[index].append):
                OperationProgress.report('begin', index)
                late.append(asyncio.create_task(inherited()))
                await asyncio.sleep(0)
                OperationProgress.report('end', index)
        await asyncio.gather(request(0), request(1))
        release.set()
        await asyncio.gather(*late)
        self.assertEqual([[v.completed for v in values] for values in seen], [[0, 0], [1, 1]])
        self.assertTrue(all([v.key for v in values] == ['begin', 'end'] for values in seen))

    async def test_initial_connection_blocks_home_but_preserves_minimize_and_loading(self):
        gate = Event()
        def factory(config):
            if not gate.wait(10):
                raise TimeoutError('test gate')
            return settings_backend(config)
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False), input=pipe,
                output=SizedOutput(), worker_factory=partial(BackendWorker, backend_factory=factory))
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: controller._startup_popup is not None)
                popup = controller._startup_popup
                self.assertFalse(view.connected)
                self.assertTrue(popup.progress.active)
                self.assertFalse(view.progress.active)
                self.assertFalse(popup.retry_button.control.is_focusable())
                self.assertFalse(popup.reset_button.control.is_focusable())
                pipe.send_text('ignoredtyping')
                pipe.send_text('\x11')
                await eventually(lambda: not view.visible)
                self.assertEqual(view.composer.text, '')
                pipe.send_text('\x11')
                await eventually(lambda: view.visible)
                self.assertIs(view._dialog, popup)
                self.assertIs(app.layout.current_control, popup.minimize_button.control)
                gate.set()
                await backend_ready(lambda: view.connected and not controller.starting, 'initial connection')
                self.assertIsNone(view._dialog)
                self.assertIsNone(popup.progress._timer)
                self.assertEqual(view.composer.text, '')
                self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                gate.set()
                app.exit()
                await task
                await controller.aclose()

    async def test_initialization_failure_removes_progress(self):
        def fail(config):
            raise ValueError('startup failed')
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root), input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=fail))
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await backend_ready(lambda: controller.startup_failure is not None, 'startup failure')
                self.assertFalse(view.connected)
                self.assertFalse(view.progress.active)
                popup = view._dialog
                self.assertTrue(popup.showing_failure)
                self.assertIn('startup failed', str(popup.message()))
                self.assertFalse(popup.progress.active)
                self.assertFalse(view.notice)
                self.assertTrue(popup.retry_button.control.is_focusable())
                self.assertFalse(popup.reset_button.control.is_focusable())
            finally:
                app.exit()
                await task
                await controller.aclose()

    async def test_loading_can_complete_while_minimized_without_stealing_shell_focus(self):
        gate = Event()
        def factory(config):
            if not gate.wait(10):
                raise TimeoutError('test gate')
            return settings_backend(config)
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(root, auto_title=False), input=pipe,
                output=SizedOutput(), worker_factory=partial(BackendWorker, backend_factory=factory))
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view._dialog is not None)
                pipe.send_text('\x11')
                await eventually(lambda: not view.visible)
                shell_focus = app.layout.current_control
                gate.set()
                await backend_ready(lambda: view.connected and not controller.starting, 'hidden startup')
                self.assertFalse(view.visible)
                self.assertIsNone(view._dialog)
                self.assertIs(app.layout.current_control, shell_focus)
                pipe.send_text('\x11')
                await eventually(lambda: view.visible)
                self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                gate.set()
                app.exit()
                await task
                await controller.aclose()

    async def test_open_history_does_not_prepare_unused_project_toolkit(self):
        from hub.backend.tools.builtin import HubBuiltinTools
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()  # Creation must resolve the initial Agent's tools.
            await runtime.close()
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            try:
                stages = []
                with patch.object(HubBuiltinTools, 'resolve', side_effect=AssertionError('eager tool resolution')):
                    with OperationProgress.bind(stages.append):
                        await runtime.start()
                        await runtime.snapshot()
                self.assertIn(MessageKey.PROGRESS_BACKEND_STORAGE, [v.key for v in stages])
                self.assertIn(MessageKey.PROGRESS_BACKEND_SESSIONS, [v.key for v in stages])
                self.assertEqual(stages[-1].key, MessageKey.PROGRESS_BACKEND_HISTORY)
            finally:
                await runtime.close()

    async def test_stage_change_invalidates_cached_line_and_ignores_late_update(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            try:
                with set_app(app):
                    progress = view.progress
                    token = progress.start('old')
                    control = progress.container().content
                    control.create_content(120, 1)
                    progress.observe(token, BackendProgress(MessageKey.PROGRESS_BACKEND_SESSIONS, 2, 4))
                    text = ''.join(part[1] for part in control.create_content(120, 1).get_line(0))
                    self.assertIn('50%', text)
                    self.assertNotIn('old', text)
                    progress.finish(token)
                    progress.observe(token, BackendProgress(MessageKey.PROGRESS_BACKEND_HISTORY))
                    self.assertFalse(progress.active)
            finally:
                view.close()
                view.progress.close()
