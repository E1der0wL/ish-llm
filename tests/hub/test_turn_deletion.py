"""Hub의 삭제 영향 확인과 재개 포기: 취소는 무변경, 확인은 revision을 전달한다."""

import asyncio
from types import SimpleNamespace as NS
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.chat.history_ui import HistoryUI
from llm.engines.loop import LoopEngine
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_overlays import screen_text
from tests.llm.test_loop import ScriptedCompletion


class TurnDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_loop_delete_requires_confirmation_and_blocks_future_resume(self):
        provider = ScriptedCompletion([RuntimeError("test provider failure")])
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "failed request")
                session = runtime.sessions[sid]
                await session.run.wait_idle()
                row, = await runtime.turns(sid)
                plan = await runtime.delete_turn_plan(sid, row["id"])
                run_ids = [item["source"]["run_id"] for item in plan["blockers"]]
                self.assertEqual(run_ids, [row["run_id"]])
                with self.assertRaises(ValueError):
                    await runtime.delete_turn(sid, row["id"])
                self.assertEqual(len(await runtime.turns(sid)), 1)
                self.assertTrue(await runtime.delete_turn(sid, row["id"], run_ids, plan["revision"]))
                self.assertFalse(await runtime.turns(sid))
                self.assertEqual(len(await session.aconversation(include_deleted=True)), 2)
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await session.run.resume(row["run_id"], engine="loop")
                self.assertEqual(len(provider.requests), 1)
            finally:
                await runtime.close()

    async def test_confirmation_lists_dependencies_cancel_preserves_and_accept_forwards_exact_plan(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.project_id = "p"
            view._submitted_messages = {}
            sid = view.sessions[view.selected].id
            rows = [{"id": "turn", "text": "failed request", "status": "failed", "time": "", "engine": "loop",
                     "elapsed": None, "response": "", "error": ""}]
            plan = {"revision": "observed-revision", "blockers": [
                {"source": {"run_id": "run-a"}, "details": {"engine": "loop", "status": "failed"}},
                {"source": {"run_id": "run-b"}, "details": {"engine": "graph", "status": "paused"}}]}
            calls = []
            def call(operation, *args, completed):
                calls.append((operation, args))
                if operation == "delete_turn_plan":
                    completed(plan)
                elif operation == "delete_turn":
                    rows.clear()
                    completed(True)
                elif operation == "turns":
                    completed(rows)
                else:
                    completed(None)
            history = HistoryUI(NS(view=view, _call=call, _snapshot=lambda _: None))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    history.show(sid, rows)
                pipe.send_text("d")
                await eventually(lambda: "run-b" in screen_text(app, 132, 40))
                text = screen_text(app, 132, 40)
                self.assertIn("run-a", text)
                self.assertIn(view.t("history_abandon_delete"), text)
                self.assertFalse(any(op == "delete_turn" for op, _ in calls))
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is None)
                self.assertEqual(len(rows), 1)
                with set_app(app):
                    history.show(sid, rows)
                pipe.send_text("d")
                await eventually(lambda: "run-b" in screen_text(app, 132, 40))
                pipe.send_text("\r")
                await eventually(lambda: not rows)
                deletes = [args for op, args in calls if op == "delete_turn"]
                self.assertEqual(deletes, [(sid, "turn", ["run-a", "run-b"], "observed-revision")])
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()
