"""Stop feedback is immediate even while backend cancellation is still pending."""

import asyncio
from concurrent.futures import Future
from dataclasses import replace
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout

from hub.asset.icon import NERD, UNICODE
from hub.backend.runtime import HubConfig
from hub.locales import Language
from hub.model import ChatMessage, HubSnapshot, RetrySummary, RunSummary, SessionSummary
from hub.ui.application import create_application
from hub.ui.live import LiveController, LiveHubView
from tests.hub.support import SizedOutput
from tests.hub.test_live import until


def running_snapshot():
    return HubSnapshot("p", "Project", "test/model", "file",
        (SessionSummary("s", "Session", "running"), SessionSummary("t", "Other", "idle")), "s",
        (ChatMessage("reasoning", "Earlier thought", id="a:reasoning"),
         ChatMessage("assistant", "Partial response", status="streaming", id="a", phase="thinking"),
         ChatMessage("user", "Next request", status="queued", id="queued")),
        run=RunSummary("r", "running", "loop", 1, True, (), ""), engines=("loop",))


class InterruptFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.view = LiveHubView()
        self.controller = LiveController(self.view, HubConfig(directory.name, auto_title=False))
        self.controller.app = NS(invalidate=Mock())
        self.controller.loop = asyncio.get_running_loop()
        self.calls = []
        def call(operation, *args, **kwargs):
            future = Future()
            self.calls.append((operation, args, future))
            return future
        self.controller.worker = NS(call=call, close=Mock())
        self.addCleanup(self.controller.close)
        self.base = running_snapshot()
        self.view.apply_snapshot(self.base)

    def test_icons_are_separate_from_running_and_keep_unicode_fallback(self):
        language = Language(icons=NERD)
        self.assertEqual(language.status("preparing"), " 준비 중")
        self.assertEqual(language.status("thinking"), " 생각 중")
        self.assertEqual(NERD.statuses["running"], "")
        plain = Language(icons=UNICODE)
        self.assertEqual(plain.status("preparing"), "⚙ 준비 중")
        self.assertEqual(plain.status("thinking"), "◌ 생각 중")

    async def test_parallel_runs_stop_together_even_when_latest_run_is_already_interrupted(self):
        messages = (
            ChatMessage('assistant', 'one', id='a1', run_id='r1', status='streaming'),
            ChatMessage('assistant', 'two', id='a2', run_id='r2', status='streaming'),
            ChatMessage('assistant', 'stopped', id='a3', run_id='r3', status='interrupted'),
        )
        snapshot = replace(self.base, messages=messages, active_run_ids=('r1', 'r2'),
                           run=replace(self.base.run, id='r3', status='interrupted', active=False))
        self.view.apply_snapshot(snapshot)
        self.controller.interrupt('s')
        self.assertEqual(self.calls[0][0], 'interrupt')
        shown = self.view.output.transcript.control.messages
        self.assertEqual([m.phase for m in shown[:2]], ['interrupting', 'interrupting'])
        self.assertEqual(shown[2].status, 'interrupted')
        partial = replace(snapshot, active_run_ids=('r2',), messages=(
            replace(messages[0], status='interrupted'), messages[1], messages[2]))
        self.view.apply_snapshot(partial)
        self.assertIn(('p', 's'), self.view.interrupting.pending)
        self.view.apply_snapshot(replace(partial, active_run_ids=(),
            messages=tuple(replace(m, status='interrupted') for m in messages)))
        self.assertFalse(self.view.interrupting.pending)

    async def test_resume_prepares_existing_response_until_durable_continuation_arrives(self):
        message = replace(self.base.messages[1], status="interrupted", phase="", run_id="r")
        stopped = replace(self.base, messages=(message,), run=replace(self.base.run, status="interrupted", active=False))
        self.view.apply_snapshot(stopped)
        self.controller.interrupt("s")
        shown = self.view.output.transcript.control.messages
        self.assertEqual([(m.id, m.text, m.status) for m in shown], [("a", "Partial response", "preparing")])
        self.assertEqual(self.view.t.status(shown[0].phase), " 재개 준비 중")
        self.view.apply_snapshot(stopped)
        self.assertEqual(len(self.view.output.transcript.control.messages), 1)
        self.calls[0][2].set_result("resumed-request")
        await asyncio.sleep(0)
        self.view.apply_snapshot(stopped)  # Stale observation after admission ACK.
        self.assertEqual(self.view.output.transcript.control.messages[0].status, "preparing")
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "resume_preparing")
        self.controller.interrupt("s")
        self.assertEqual(sum(op == "resume_latest" for op, _, _ in self.calls), 1)
        resumed = replace(stopped, messages=(replace(message, run_id="r2", status="streaming"),),
                          run=replace(stopped.run, id="r2", active=True, status="running"))
        self.view.apply_snapshot(resumed)
        self.assertFalse(self.view.preparing.resuming)
        self.assertEqual([(m.id, m.text, m.status) for m in self.view.output.transcript.control.messages],
                         [("a", "Partial response", "streaming")])

    async def test_resume_header_updates_while_markdown_rendering_is_blocked(self):
        message = replace(self.base.messages[1], status="interrupted", phase="", run_id="r")
        stopped = replace(self.base, messages=(message,),
                          run=replace(self.base.run, status="interrupted", active=False))
        self.view.apply_snapshot(stopped)
        control = self.view.output.transcript.control
        control.create_content(100, 20)
        body_count = control._message_cache.body_renders
        with patch.object(control, "_request_frame"):
            self.controller.interrupt("s")
            self.view.apply_snapshot(stopped)
            for snapshot, caption in (
                (stopped, "재개 준비 중"),
                (replace(stopped, messages=(replace(message, status="streaming", run_id="r2", phase="resuming"),),
                         run=replace(stopped.run, id="r2", status="running", active=True)), "이어서 실행"),
                (replace(stopped, messages=(replace(message, status="streaming", run_id="r2", phase="thinking"),),
                         run=replace(stopped.run, id="r2", status="running", active=True)), "생각 중"),
            ):
                self.view.apply_snapshot(snapshot)
                content = control.create_content(100, 20)
                rendered = "\n".join("".join(part[1] for part in content.get_line(i))
                                     for i in range(content.line_count))
                self.assertIn(caption, rendered)
                self.assertNotIn("중단됨", rendered)
                self.assertIn("Partial response", rendered)
                self.assertEqual(len(control.messages), 1)
                self.assertEqual(control._message_cache.body_renders, body_count)

    async def test_rejected_resume_restores_existing_response(self):
        message = replace(self.base.messages[1], status="interrupted", phase="", run_id="r")
        self.view.apply_snapshot(replace(self.base, messages=(message,),
            run=replace(self.base.run, status="interrupted", active=False)))
        self.controller.interrupt("s")
        self.calls[0][2].set_exception(ValueError("resume blocked"))
        await asyncio.sleep(0)
        self.assertFalse(self.view.preparing.resuming)
        self.assertEqual(self.view.output.transcript.control.messages[0].status, "interrupted")

    async def test_history_retry_tracks_selected_older_turn_not_last_response(self):
        from hub.ui.chat.history_ui import HistoryUI
        messages = (ChatMessage("user", "first", id="u1"),
            ChatMessage("assistant", "partial", id="a1", run_id="r1", status="interrupted"),
            ChatMessage("user", "second", id="u2"),
            ChatMessage("assistant", "later answer", id="a2", run_id="r2", status="completed"))
        snapshot = replace(self.base, messages=messages, run=replace(self.base.run, id="r2", status="completed", active=False))
        self.view.apply_snapshot(snapshot)
        row = {"id": "u1", "request_id": "u1", "runs": [{"run_id": "r1", "agent_id": "assistant", "status": "interrupted", "engine": "loop"}], "status": "interrupted", "text": "first",
               "response": "partial", "time": "", "engine": "loop", "elapsed": None, "error": ""}
        history = HistoryUI(self.controller)
        with create_pipe_input() as pipe:
            self.view.visible = True
            app = Application(layout=Layout(self.view.container), input=pipe, output=SizedOutput())
            with set_app(app):
                history.show("s", [row])
                self.view._dialog.choices.current_values = ["u1"]
                self.view._dialog.buttons[2].handler()
        self.assertEqual(self.calls[0][:2], ("retry_turn", ("s", "u1", "r1")))
        self.assertIsNone(self.view._dialog)
        shown = self.view.output.transcript.control.messages
        self.assertEqual([m.id for m in shown], ["u1", "a1", "u2", "a2"])
        self.assertEqual([m.status for m in shown if m.role == "assistant"], ["preparing", "completed"])
        self.assertEqual(shown[1].phase, "resume_preparing")
        self.view.apply_snapshot(snapshot)
        self.calls[0][2].set_result("new-request")
        await asyncio.sleep(0)
        self.view.apply_snapshot(snapshot)
        self.assertEqual(self.view.output.transcript.control.messages[1].status, "preparing")
        resumed = replace(snapshot, messages=(messages[0], replace(messages[1], run_id="r3", status="streaming"), *messages[2:]))
        self.view.apply_snapshot(resumed)
        self.assertFalse(self.view.preparing.resuming)
        self.assertEqual(self.view.output.transcript.control.messages[1].status, "streaming")
        self.assertEqual(self.view.output.transcript.control.messages[-1], shown[-1])

    async def test_restart_replaces_failed_system_slot_without_extra_preparing_message(self):
        from hub.ui.chat.resume import resume_turn
        messages = (ChatMessage("user", "first", id="u1"),
                    ChatMessage("assistant", "", id="a1", run_id="r1", status="failed"),
                    ChatMessage("user", "second", id="u2"),
                    ChatMessage("assistant", "second answer", id="a2", run_id="r2", status="completed"))
        snapshot = replace(self.base, messages=messages)
        self.view.apply_snapshot(snapshot)
        resume_turn(self.controller, "s", "r1", "u1")
        shown = self.view.output.transcript.control.messages
        self.assertEqual([m.id for m in shown], ["u1", "a1", "u2", "a2"])
        self.assertEqual(shown[1].status, "preparing")
        restarted = replace(snapshot, messages=(messages[0], replace(messages[1], id="a3", run_id="r3", status="streaming"), *messages[2:]))
        self.view.apply_snapshot(restarted)
        self.assertFalse(self.view.preparing.resuming)

    async def test_immediate_header_survives_stale_snapshot_and_ack_until_terminal(self):
        base = replace(self.base, run=replace(self.base.run, retry=RetrySummary(1, 4, 999999999)))
        self.view.apply_snapshot(base)
        self.controller.interrupt("s")
        shown = self.view.output.transcript.control.messages
        self.assertEqual(shown[1].phase, "interrupting")
        self.assertEqual(shown[1].activity, "")
        self.assertEqual(shown[1].text, "Partial response")
        self.assertEqual(shown[1].status, "streaming")
        self.assertEqual(shown[0].text, "Earlier thought")
        self.assertEqual(shown[-1].status, "queued")
        self.assertEqual(self.view._last_snapshot, base)
        self.assertFalse(self.calls[0][2].done())
        self.controller.interrupt("s")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.view.notice, self.view.t("cancel_wait"))
        self.view.apply_snapshot(base)
        self.assertEqual(self.view.output.transcript.control.messages[1].phase, "interrupting")
        self.calls[0][2].set_result(True)
        await asyncio.sleep(0)
        self.assertNotEqual(self.view.notice, self.view.t("cancel_wait"))
        self.assertTrue(self.view.interrupting.pending)
        stopped = replace(base, run=replace(base.run, active=False, status="interrupted"),
            messages=(base.messages[0], replace(base.messages[1], phase="", status="interrupted"), base.messages[2]))
        self.view.apply_snapshot(stopped)
        self.assertFalse(self.view.interrupting.pending)
        self.assertEqual(self.view.output.transcript.control.messages[1].status, "interrupted")
        self.assertEqual(self.view.output.transcript.control.messages[-1].status, "queued")

    async def test_paused_stop_hides_question_and_survives_stale_projection(self):
        paused = replace(self.base, run=replace(self.base.run, status="paused", active=False),
            messages=(replace(self.base.messages[1], status="paused", phase=""),),
            questions=({"run_id": "r", "live": True, "request": {
                "id": "q", "revision": 1, "title": "Continue?", "source": {"choices": ["Yes", "No"]}}},))
        self.view.apply_snapshot(paused)
        self.assertIsNotNone(self.view.question.item)
        self.controller.interrupt("s")
        self.assertIsNone(self.view.question.item)
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "interrupting")
        self.view.apply_snapshot(paused)
        self.assertIsNone(self.view.question.item)
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "interrupting")
        self.calls[0][2].set_result(True)
        await asyncio.sleep(0)
        self.view.apply_snapshot(replace(paused, questions=(), run=replace(paused.run, status="interrupted"),
            messages=(replace(paused.messages[0], status="interrupted"),)))
        self.assertFalse(self.view.interrupting.pending)

    async def test_failure_or_no_active_target_restores_actual_header(self):
        for failure in (False, RuntimeError("stop failed")):
            with self.subTest(failure=failure):
                self.controller.interrupt("s")
                future = self.calls[-1][2]
                if isinstance(failure, Exception):
                    future.set_exception(failure)
                else:
                    future.set_result(failure)
                await asyncio.sleep(0)
                self.assertFalse(self.view.interrupting.pending)
                self.assertEqual(self.view.output.transcript.control.messages[1].phase, "thinking")
        self.assertIn(self.view.t("operation_failed"), self.view.notice)
        self.assertIn("stop failed", self.view.operation_diagnostics.text("p", self.view.t))

    async def test_switch_and_new_run_do_not_inherit_old_stop_or_late_failure(self):
        self.controller.interrupt("s")
        old = self.calls[0][2]
        other = replace(self.base, selected_id="t", run=None,
                        messages=(ChatMessage("assistant", "Other response", id="other", status="completed"),))
        self.view.apply_snapshot(other, select_id="t")
        self.assertNotEqual(self.view.output.transcript.control.messages[0].phase, "interrupting")
        self.view.apply_snapshot(self.base, select_id="s")
        self.assertEqual(self.view.output.transcript.control.messages[1].phase, "interrupting")
        next_run = replace(self.base, run=replace(self.base.run, id="next"),
                           messages=(replace(self.base.messages[1], id="next-answer", text="Next response"),))
        self.view.apply_snapshot(next_run)
        self.assertFalse(self.view.interrupting.pending)
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "thinking")
        self.controller.interrupt("s")
        old.set_result(False)
        await asyncio.sleep(0)
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "interrupting")
        self.assertEqual(self.view.interrupting.pending[("p", "s")].run_id, "next")

    def test_early_stop_before_assistant_is_published_and_terminal_active_flag(self):
        self.view.apply_snapshot(replace(self.base, messages=()))
        self.controller.interrupt("s")
        self.assertEqual(self.view.output.transcript.control.messages[0].phase, "interrupting")
        self.view.apply_snapshot(self.base)
        self.assertEqual(len(self.view.output.transcript.control.messages), len(self.base.messages))
        self.assertEqual(self.view.output.transcript.control.messages[1].phase, "interrupting")
        # Lifecycle and conversation reads may straddle a transition.
        self.view.apply_snapshot(replace(self.base, run=replace(self.base.run, status="completed")))
        self.assertFalse(self.view.interrupting.pending)

    async def test_preparing_stop_survives_old_snapshot_until_new_run_is_observed(self):
        previous = replace(self.base, run=replace(self.base.run, status="completed", active=False),
                           messages=(replace(self.base.messages[1], status="completed", phase=""),))
        self.view.apply_snapshot(previous)
        self.view.preparing.start(("p", "s"))
        self.controller.interrupt("s")
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        self.assertEqual(self.view.output.transcript.control.messages[0].status, "completed")
        self.view.apply_snapshot(previous)
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        active = replace(self.base, run=replace(self.base.run, id="new-run"),
                         messages=(replace(self.base.messages[1], id="new-answer"),))
        self.view.apply_snapshot(active)
        self.view.preparing.finish(("p", "s"))
        self.assertEqual(self.view.interrupting.pending[("p", "s")].run_id, "new-run")
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        # A torn observation of runtime/metadata is not a completed cancellation.
        self.view.apply_snapshot(replace(active, run=replace(active.run, active=False)))
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        self.calls[0][2].set_result(True)
        await asyncio.sleep(0)
        self.view.apply_snapshot(replace(active, run=replace(active.run, status="interrupted", active=False),
            messages=(replace(active.messages[0], status="interrupted", phase=""),)))
        self.assertFalse(self.view.interrupting.pending)

    async def test_preparing_without_run_reverts_rejection_and_accepts_terminal_first_observation(self):
        empty = replace(self.base, run=None, messages=())
        self.view.apply_snapshot(empty)
        self.view.preparing.start(("p", "s"))
        self.controller.interrupt("s")
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        self.view.apply_snapshot(empty)
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "interrupting")
        self.calls[-1][2].set_result(False)
        await asyncio.sleep(0)
        self.assertFalse(self.view.interrupting.pending)
        self.assertEqual(self.view.output.transcript.control.messages[-1].status, "preparing")
        self.assertEqual(self.view.output.transcript.control.messages[-1].phase, "")
        self.controller.interrupt("s")
        self.view.apply_snapshot(replace(empty,
            run=replace(self.base.run, status="interrupted", active=False)))
        self.assertFalse(self.view.interrupting.pending)

    def test_header_only_change_reuses_body_and_thought_rendering(self):
        control = self.view.output.transcript.control
        control.create_content(100, 20)
        cache = control._message_cache
        body = cache.items["a"].body
        thought = cache.items["a:reasoning"].frame
        count = cache.body_renders
        self.controller.interrupt("s")
        control.create_content(100, 20)
        self.assertIs(cache.items["a"].body, body)
        self.assertIs(cache.items["a:reasoning"].frame, thought)
        self.assertEqual(cache.body_renders, count)


class InterruptKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_ish_single_ctrl_w_renders_stopping_during_preparation(self):
        from hub.hub import install
        from tests.hub.test_ish_integration import Prompt

        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput(columns=110, rows=32))
            prompt.context.update("environ", f"PWD={directory}\0PATH=/usr/bin:/bin\0".encode())
            installation = install(prompt, config=HubConfig(directory, auto_title=False))
            app, view, controller = prompt.app, installation.view, installation.controller
            view.on_open = None
            controller.app, controller.loop = app, asyncio.get_running_loop()
            future = Future()
            controller.worker = NS(call=Mock(side_effect=lambda operation, *args:
                future if operation == "interrupt" else Future()), close=Mock(), set_visible=Mock())
            base = running_snapshot()
            with set_app(app):
                view.apply_snapshot(replace(base, run=replace(base.run, status="completed", active=False),
                    messages=(replace(base.messages[1], text="Previous response", status="completed", phase=""),)))
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            def drawn():
                screen = app.renderer._last_screen
                return "\n".join("".join(screen.data_buffer[y][x].char for x in range(110))
                                 for y in range(32)) if screen else ""
            try:
                await until(lambda: "Previous response" in drawn())
                with set_app(app):
                    view.preparing.start(("p", "s"))
                await until(lambda: "준비 중" in drawn())
                pipe.send_bytes(b"\x04")
                await until(lambda: "중단 중" in drawn())
                self.assertFalse(future.done())
                self.assertIn("Previous response", drawn())
                controller.worker.call.assert_called_once_with("interrupt", "s")
                self.assertIs(app.layout.current_control, view.composer.control)
                future.set_result(True)
            finally:
                app.exit(result="")
                await task
                controller.worker.set_visible.assert_called_once_with(False)
                installation.close()

    async def test_ctrl_w_renders_stopping_before_backend_returns(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False),
                input=pipe, output=SizedOutput(columns=110, rows=32))
            view = controller.view
            view.on_open = None
            controller.app, controller.loop = app, asyncio.get_running_loop()
            future = Future()
            controller.worker = NS(call=Mock(side_effect=lambda operation, *args:
                future if operation == "interrupt" else Future()), close=Mock())
            with set_app(app):
                view.apply_snapshot(running_snapshot())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            gate, entered = threading.Event(), threading.Event()
            def drawn():
                screen = app.renderer._last_screen
                return "\n".join("".join(screen.data_buffer[y][x].char for x in range(110))
                                 for y in range(32)) if screen else ""
            try:
                await until(lambda: "Partial response" in drawn())
                # A slow image/Markdown renderer must not delay local stop feedback.
                def slow_render():
                    entered.set()
                    gate.wait(5)
                view.render_worker.request("popup", slow_render, lambda _: None, lambda _: None)
                await until(entered.is_set)
                pipe.send_bytes(b"\x04\x04")
                await until(lambda: "중단 중" in drawn())
                self.assertFalse(future.done())
                self.assertIn("Partial response", drawn())
                self.assertIn("Earlier thought", drawn())
                pipe.send_text("new draft")
                await until(lambda: view.composer.text == "new draft")
                controller.worker.call.assert_called_once_with("interrupt", "s")
                self.assertIs(app.layout.current_control, view.composer.control)
                future.set_result(True)
            finally:
                gate.set()
                app.exit()
                await task
                controller.close()
