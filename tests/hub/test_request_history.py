"""Durable attempts stay at their logical position; UI actions honor selection."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from functools import partial
import unittest
from unittest.mock import AsyncMock, Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.environment import ENVIRONMENT_OPTION
from hub.ui.chat.history_ui import HistoryUI
from llm.engines.loop import LoopEngine
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_live import controlled_backend


class HistoryBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_early_interrupt_without_checkpoint_restarts_in_place_after_reopen(self):
        entered, release = asyncio.Event(), asyncio.Event()
        provider = ScriptedCompletion([chunk("later answer", finish="stop")], [chunk("fresh answer", finish="stop")])

        class PreparingLoop(LoopEngine):
            async def execute(self, context):
                if not release.is_set():
                    entered.set()
                    await release.wait()
                async for event in super().execute(context):
                    yield event

        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: PreparingLoop(completion_fn=provider)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                options = {ENVIRONMENT_OPTION: {"cwd": directory, "env": {"PWD": directory, "TEST_ENV": "retained"}}}
                original = await runtime.submit(sid, "first request", engine_options=options)
                await asyncio.wait_for(entered.wait(), 5)
                await runtime.interrupt(sid)
                await session.run.wait_idle()
                first, = await runtime.turns(sid)
                self.assertEqual(first["status"], "interrupted")
                handle = await session.run.aload(first["run_id"])
                self.assertEqual(await handle.steps.alist(), [])
                with self.assertRaises(FileNotFoundError):
                    await handle.acheckpoint("loop")
                self.assertEqual(provider.requests, [])
                release.set()
                later = await runtime.submit(sid, "later request")
                await session.run.wait_idle()
                project_id = runtime.project.id
                await runtime.close()
                runtime = HubRuntime(replace(config, project_id=project_id, session_id=sid))
                await runtime.start()
                session = runtime.sessions[sid]
                retry_id = await runtime.retry_turn(sid, original, first["run_id"])
                await session.run.wait_idle()
                rows = await runtime.turns(sid)
                self.assertEqual([row["id"] for row in rows], [original, later])
                self.assertEqual(rows[0]["status"], "completed")
                self.assertEqual(rows[0]["response"], "fresh answer")
                self.assertEqual(rows[0]["request_id"], retry_id)
                self.assertNotEqual(rows[0]["run_id"], first["run_id"])
                fresh = await (await session.run.aload(rows[0]["run_id"])).aget_data()
                self.assertNotIn("resume", fresh.metadata)
                self.assertEqual(fresh.metadata["engine_options"][ENVIRONMENT_OPTION], options[ENVIRONMENT_OPTION])
                self.assertEqual([m["content"] for m in provider.requests[-1]["messages"] if m["role"] == "user"],
                                 ["first request"])
                snapshot = await runtime.snapshot()
                self.assertEqual([m.text for m in snapshot.messages if m.role == "user"], ["first request", "later request"])
                with self.assertRaisesRegex(ValueError, "변경"):
                    await runtime.retry_turn(sid, original, first["run_id"])
                self.assertEqual(len(await session.run.alist()), 3)
            finally:
                release.set()
                await runtime.close()

    async def test_missing_checkpoint_does_not_restart_executed_or_protected_runs(self):
        from hub.backend.history import TurnHistory
        from hub.locales import Language
        request = SimpleNamespace(id="u", role="user", run_id="r", metadata={},
                                  status="committed", content="request", created_at="")
        run = SimpleNamespace(id="r", engine="loop", status="failed", metadata={})
        handle = SimpleNamespace(aget_data=AsyncMock(return_value=run),
            acheckpoint=AsyncMock(side_effect=FileNotFoundError("missing checkpoint")),
            steps=SimpleNamespace(alist=AsyncMock(return_value=[])))
        plan = SimpleNamespace(can_resume=False, interactions=[], reused=[], retry_nodes=[],
                               blockers=[SimpleNamespace(message="missing checkpoint")])
        session = SimpleNamespace(aconversation=AsyncMock(return_value=[request]), run=SimpleNamespace(
            astatus=AsyncMock(return_value=SimpleNamespace(active_run_id=None, queued_count=0)),
            aload=AsyncMock(return_value=handle), resume_plan=AsyncMock(return_value=plan), resume=AsyncMock()))
        runtime = SimpleNamespace(sessions={"s": session}, namer=SimpleNamespace(tasks={}), t=Language(),
            submit=AsyncMock(), backend=SimpleNamespace(engines=SimpleNamespace(resolve_request=Mock(
                return_value=SimpleNamespace(checkpoint_name="loop", validate_resume=lambda _: None)))))
        history = TurnHistory(runtime)
        for metadata, steps, interactions in (({"checkpoints": ["loop"]}, [], []),
                ({"resume": {"run_id": "parent"}}, [], []), ({"resume_abandonment": {}}, [], []),
                ({}, [SimpleNamespace(kind="tool", status="completed")], []),
                ({}, [SimpleNamespace(kind="tool", status="interrupted")], []), ({}, [], [object()])):
            with self.subTest(metadata=metadata, steps=steps, interactions=interactions):
                run.metadata, plan.interactions = metadata, interactions
                handle.steps.alist.return_value = steps
                with self.assertRaisesRegex(ValueError, "체크포인트"):
                    await history.retry("s", "u", "r")
                runtime.submit.assert_not_awaited()
                session.run.resume.assert_not_awaited()
        run.metadata, plan.interactions = {}, []
        handle.steps.alist.return_value = []
        for error in (PermissionError("cannot read checkpoint"), ValueError("invalid checkpoint")):
            with self.subTest(error=error):
                handle.acheckpoint.side_effect = error
                with self.assertRaises(type(error)):
                    await history.retry("s", "u", "r")
                runtime.submit.assert_not_awaited()

    async def test_edit_approval_waiting_request_starts_fresh_without_old_effects(self):
        provider = ScriptedCompletion([chunk(calls=[call('{"path":"old.txt","content":"old"}',
            name="file_create")], finish="tool_calls")], [chunk("edited response", finish="stop")])
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(Path(directory, "workspace"), file_root=directory, model="test/model",
                auto_title=False, engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                await runtime.submit(sid, "create old file")
                await session.run.wait_idle()
                row, = await runtime.turns(sid)
                self.assertEqual(row["status"], "paused")
                await runtime.edit_turn(sid, row["id"], "do not create a file", row["request_id"])
                await session.run.wait_idle()
                edited, = await runtime.turns(sid)
                self.assertEqual(edited["status"], "completed")
                self.assertEqual(edited["id"], row["id"])
                self.assertFalse(Path(directory, "old.txt").exists())
                self.assertEqual([m["content"] for m in provider.requests[-1]["messages"] if m["role"] == "user"],
                                 ["do not create a file"])
            finally:
                await runtime.close()

    async def test_queued_retry_keeps_queued_state_despite_previous_response(self):
        from hub.backend.history import TurnHistory
        records = [SimpleNamespace(id="u1", role="user", run_id="r1", metadata={}, status="committed", content="request", created_at=""),
                   SimpleNamespace(id="a1", role="assistant", run_id="r1", metadata={}, status="failed", content="partial"),
                   SimpleNamespace(id="u2", role="user", run_id=None, metadata={"resume": {"run_id": "r1"}},
                                   status="queued", content="request", created_at="")]
        history = TurnHistory(SimpleNamespace(sessions={"s": SimpleNamespace(aconversation=AsyncMock(return_value=records))}))
        row, = await history.rows("s")
        self.assertEqual(row["id"], "u1")
        self.assertEqual(row["request_id"], "u2")
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["response"], "partial")

    async def test_retry_edit_order_context_restart_and_delete_attempts(self):
        provider = ScriptedCompletion([RuntimeError("failed")], [chunk("second answer", finish="stop")],
            [chunk("recovered", finish="stop")], [chunk("edited answer", finish="stop")],
            [chunk("third answer", finish="stop")])
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, model="test/model", auto_title=False,
                               engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                for text in ("first", "second"):
                    await runtime.submit(sid, text)
                    await session.run.wait_idle()
                first, second = await runtime.turns(sid)
                self.assertEqual(first["status"], "failed")
                await runtime.retry_turn(sid, first["id"], first["run_id"])
                await session.run.wait_idle()
                rows = await runtime.turns(sid)
                self.assertEqual([row["id"] for row in rows], [first["id"], second["id"]])
                self.assertEqual(rows[0]["status"], "completed")
                self.assertNotEqual(rows[0]["run_id"], first["run_id"])
                with self.assertRaisesRegex(ValueError, "중단 혹은 실패"):
                    await runtime.retry_turn(sid, second["id"], second["run_id"])
                with self.assertRaisesRegex(ValueError, "변경"):
                    await runtime.edit_turn(sid, first["id"], "stale", first["request_id"])
                await runtime.edit_turn(sid, first["id"], "changed", rows[0]["request_id"])
                await session.run.wait_idle()
                self.assertEqual([m["content"] for m in provider.requests[-1]["messages"] if m["role"] == "user"], ["changed"])
                await runtime.submit(sid, "third")
                await session.run.wait_idle()
                self.assertEqual([m["content"] for m in provider.requests[-1]["messages"] if m["role"] == "user"],
                                 ["changed", "second", "third"])
                snapshot = await runtime.snapshot()
                self.assertEqual([m.text for m in snapshot.messages if m.role == "user"], ["changed", "second", "third"])
                self.assertEqual([m.text for m in snapshot.messages if m.role == "assistant"],
                                 ["edited answer", "second answer", "third answer"])
                self.assertEqual(len(await session.run.alist()), 5)
                branch = await runtime.new_session("clone", "edited branch", sid, first["id"])
                cloned = await runtime.turns(branch)
                self.assertEqual([row["text"] for row in cloned], ["changed"])
                self.assertEqual(cloned[0]["response"], "edited answer")
                self.assertEqual(cloned[0]["status"], "completed")
                await runtime.select(sid)
                project_id = runtime.project.id
                await runtime.close()
                runtime = HubRuntime(replace(config, project_id=project_id, session_id=sid))
                await runtime.start()
                rows = await runtime.turns(sid)
                self.assertEqual([r["text"] for r in rows], ["changed", "second", "third"])
                self.assertEqual(rows[0]["id"], first["id"])
                plan = await runtime.delete_turns_plan(sid, [first["id"], second["id"]])
                result = await runtime.delete_turns(sid, [first["id"], second["id"]], plan["revision"])
                self.assertFalse(result["errors"])
                self.assertEqual([r["text"] for r in await runtime.turns(sid)], ["third"])
            finally:
                await runtime.close()

    async def test_bulk_cancel_preserves_active_run_and_reports_partial_delete(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model="test/model", auto_title=False),
                                 partial(controlled_backend, gate=gate))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                finished = await runtime.submit(sid, "finished")
                await session.run.wait_idle()
                await runtime.submit(sid, "hold active")
                async with asyncio.timeout(3):
                    while not (await session.run.astatus()).active_run_id:
                        await asyncio.sleep(.01)
                active = (await session.run.astatus()).active_run_id
                queued = [await runtime.submit(sid, f"queued {i}") for i in range(2)]
                plan = await runtime.delete_turns_plan(sid, [finished, *queued])
                progress = []
                result = await runtime.delete_turns(sid, [finished, *queued], plan["revision"],
                    progress=lambda done, total: progress.append((done, total)))
                self.assertEqual(progress, [(0, 3), (1, 3), (2, 3), (3, 3)])
                self.assertEqual(set(result["removed"]), set(queued))
                self.assertTrue(result["errors"])
                self.assertEqual((await session.run.astatus()).active_run_id, active)
                self.assertEqual((await session.run.astatus()).queued_count, 0)
                await runtime.interrupt(sid)
                await session.run.wait_idle()
                row = next(row for row in await runtime.turns(sid) if row["run_id"] == active)
                with self.assertRaisesRegex(ValueError, "체크포인트"):
                    await runtime.retry_turn(sid, row["id"], active)
                self.assertEqual(len(await session.run.alist()), 2)
            finally:
                gate.set()
                await runtime.close()

    async def test_retry_reuses_completed_tool(self):
        provider = ScriptedCompletion([chunk(calls=[call('{"path":"input.txt"}', name="file_read")], finish="tool_calls")],
            [RuntimeError("provider failed after tool")], [chunk("done", finish="stop")])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "input.txt").write_text("test input")
            runtime = HubRuntime(HubConfig(Path(directory, "workspace"), file_root=directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                await runtime.submit(sid, "read input")
                await session.run.wait_idle()
                row, = await runtime.turns(sid)
                self.assertEqual(row["status"], "failed")
                await runtime.retry_turn(sid, row["id"], row["run_id"])
                await session.run.wait_idle()
                self.assertEqual((await runtime.turns(sid))[0]["status"], "completed")
                steps = [step for run in await session.run.alist() for step in await run.steps.alist()]
                tools = [step for step in steps if step.kind == "tool"]
                self.assertEqual(sum(not step.metadata.get("reused") for step in tools), 1)
                self.assertEqual(sum(bool(step.metadata.get("reused")) for step in tools), 1)
                self.assertTrue(any(m["role"] == "tool" for m in provider.requests[-1]["messages"]))
            finally:
                await runtime.close()


class HistoryDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkbox_selection_drafts_action_arguments_and_close(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.project_id = "project"
            sid = view.sessions[view.selected].id
            rows = [{"id": f"u{i}", "request_id": f"u{i}", "run_id": f"r{i}", "text": f"request {i}",
                     "response": f"response {i}", "status": "failed" if i == 0 else "completed",
                     "time": "", "engine": "loop", "elapsed": None, "error": ""} for i in range(2)]
            calls = []
            def invoke(operation, *args, completed, progress=None):
                calls.append((operation, args))
                if operation == "delete_turns_plan":
                    completed({"items": [], "blockers": [], "revision": "revision"})
                else:
                    completed(None)
            history = HistoryUI(SimpleNamespace(view=view, _call=invoke, _snapshot=lambda _: None))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    history.show(sid, rows)
                    popup = history.popup
                    self.assertEqual([b.text for b in popup.buttons], ["삭제", "편집", "재시도", "닫기"])
                    popup.editor.text = "edited first"
                self.assertEqual(popup.selected(), [])
                pipe.send_text(" \x1b[B ")
                await eventually(lambda: len(popup.selected()) == 2)
                self.assertEqual(popup.editor.text, "request 1")
                pipe.send_text("er")
                await eventually(lambda: view.notice == view.t("history_select_one"))
                self.assertFalse(calls)
                pipe.send_text("d")
                await eventually(lambda: any(op == "delete_turns_plan" for op, _ in calls))
                self.assertEqual(calls[-1], ("delete_turns_plan", (sid, ["u0", "u1"])))
                pipe.send_text("\r")
                await eventually(lambda: any(op == "delete_turns" for op, _ in calls))
                with set_app(app):
                    history.show(sid, rows)
                    history.popup.editor.text = "edited first"
                pipe.send_text(" e")
                await eventually(lambda: any(op == "edit_turn" for op, _ in calls))
                self.assertIn(("edit_turn", (sid, "u0", "edited first", "u0")), calls)
                with set_app(app):
                    history.show(sid, rows)
                pipe.send_text(" r")
                await eventually(lambda: ("retry_turn", (sid, "u0", "r0")) in calls)
                with set_app(app):
                    history.show(sid, rows)
                    history.popup.choices.current_values = ["u1"]
                pipe.send_text("r")
                await eventually(lambda: view._dialog is None)
                self.assertEqual(view.notice, view.t("history_retry_unavailable"))
                with set_app(app):
                    history.show(sid, rows)
                    history.popup.buttons[-1].handler()
                self.assertIsNone(view._dialog)
            finally:
                app.exit()
                await task
                view.output_renderers.close()
