"""Deletion feedback stays responsive until the authoritative refresh returns."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.locales import Language
from hub.model import ChatMessage
from hub.ui.chat.history_ui import HistoryUI
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_overlays import screen_text


class DeletionFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_clear_progress_partial_failure_and_history_invalidation(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.project_id = "project"
            sid = "session"
            messages = (ChatMessage("user", "remove me", id="u0"),
                        ChatMessage("assistant", "old answer", id="a0"),
                        ChatMessage("user", "keep me", id="u1"))
            view.sessions[view.selected] = replace(view.sessions[view.selected], id=sid, messages=messages)
            rows = [dict(id=f"u{i}", text=text, response="old answer", status="completed", time="",
                         engine="loop", elapsed=1, error="")
                    for i, text in enumerate(("remove me", "keep me"))]
            pending = {}
            def invoke(operation, *args, completed, progress=None):
                if operation == "delete_turns_plan":
                    completed({"items": [{"id": "u0", "queued": False}, {"id": "u1", "queued": False}],
                               "blockers": [], "revision": "plan"})
                elif operation == "turns":
                    completed(rows[1:])
                else:
                    pending[operation] = (completed, progress)
            history = HistoryUI(SimpleNamespace(view=view, _call=invoke, _snapshot=lambda _: None))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.composer.text = ""
                    view.input_history.move(-1)
                    view.input_history.move(-1)
                    self.assertEqual(view.composer.text, "remove me")
                    history.show(sid, rows)
                    history.popup.choices.current_values = ["u0", "u1"]
                    history.popup.buttons[0].handler()
                pipe.send_text("\r")
                await eventually(lambda: "delete_turns" in pending)
                await eventually(lambda: view.t("history_deleting") in screen_text(app, 132, 40))
                self.assertIsNone(view._dialog)
                self.assertFalse(view.transcript.control.messages)
                self.assertFalse(view.output_objects)
                self.assertFalse(view.input_history.available)
                with set_app(app):
                    view.preparing.paint()  # An old observation cannot refill the transcript.
                    self.assertFalse(view.transcript.control.messages)
                    completed, progress = pending["delete_turns"]
                    progress(1, 2)
                await eventually(lambda: "50%" in screen_text(app, 132, 40))
                with set_app(app):
                    completed({"removed": ["u0"], "errors": ["second request could not be deleted"]})
                    self.assertTrue(view.progress.active)
                    self.assertFalse(view.input_history.browsing)
                    self.assertEqual(view.composer.text, "")
                    pending["snapshot"][0](None)
                    self.assertFalse(view.progress.active)
                    self.assertIn("second request", view.notice)
                    view.close_dialog()
                    view.input_history.move(-1)
                    self.assertEqual(view.composer.text, "keep me")
                    view.input_history.move(-1)
                    self.assertEqual(view.composer.text, "keep me")
                    self.assertNotIn("old answer", [m.text for m in view.transcript.control.messages])
            finally:
                app.exit()
                await task
                view.progress.close()
                view.output_renderers.close()

    async def test_failure_restores_output_and_switch_does_not_blank_other_session(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.project_id = "project"
            view.sessions = [replace(session, id=f"session-{index}") for index, session in enumerate(view.sessions)]
            key = (view.project_id, view.sessions[0].id)
            original = view.sessions[0].messages
            with set_app(app):
                pending = view.deleting.start(key, 2)
                self.assertFalse(view.transcript.control.messages)
                view.select(1)
                other = view.transcript.control.messages
                self.assertTrue(other)
                view.select(0)
                self.assertFalse(view.transcript.control.messages)
                view.select(1)
                view.deleting.finish(pending)
                self.assertEqual(view.transcript.control.messages, other)
                self.assertFalse(view.progress.active)
                view.select(0)
                self.assertEqual(view.transcript.control.messages, original)
                pending = view.deleting.start(key, 2)
                view.deleting.finish(pending)
                self.assertEqual(view.transcript.control.messages, original)
                # Recovered drafts and cancelled queue entries must not be recalled.
                view.input_history.recover(key[1], "deleted queued")
                view.input_history.discard(key[1], {"deleted queued"})
                view.sessions[0] = replace(view.sessions[0], messages=(
                    ChatMessage("user", "deleted queued", status="cancelled"),))
                view.composer.text = ""
                view.input_history.move(-1)
                self.assertEqual(view.composer.text, "")
                for locale in ("ko", "en"):
                    self.assertFalse(Language(locale)("help").startswith("# "))
            view.progress.close()
            view.output_renderers.close()
