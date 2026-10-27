"""Preview-only Applications release workers when their UI loop ends."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from tests.hub.support import SizedOutput, eventually


class PreviewLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exit_and_cancel_release_render_thread(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), create_pipe_input() as pipe:
                app, view, _ = create_preview(input=pipe, output=SizedOutput())
                task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
                try:
                    await eventually(lambda: view.render_worker._thread is not None)
                    thread = view.render_worker._thread
                    self.assertTrue(thread.is_alive())
                finally:
                    if cancel:
                        task.cancel()
                    else:
                        app.exit()
                    await asyncio.gather(task, return_exceptions=True)
                await eventually(lambda: not thread.is_alive())
                self.assertTrue(view.render_worker._closed)
                self.assertFalse(view.render_worker._pending)
