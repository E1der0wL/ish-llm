"""Durable attempts stay at their logical position; UI actions honor selection."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from functools import partial
import unittest
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.environment import ENVIRONMENT_OPTION
from hub.ui.chat.history_ui import HistoryUI
from llm.engines.loop import LoopEngine
from llm.llm import Diagnostic, CheckpointError
from llm.services.runtime.runs import RunManager
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_live import controlled_backend


class HistoryBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_checkpoint_notice_does_not_repeat_embedded_blockers(self):
        from hub.ui.error_presentation import error_text
        blocker = Diagnostic("interaction_required", "Resolve pending interactions before execution")
        error = CheckpointError("checkpoint_protected", "Retry blocked: " + blocker.message, blockers=(blocker,))
        self.assertEqual(error_text(error).count(blocker.message), 1)

    async def test_interrupted_tool_retry_exposes_approval_before_reexecuting(self):
        from tests.hub.test_tool_retry import ExampleTools
        entered = asyncio.Event()
        executions = []
        async def tool(arguments):
            executions.append(arguments)
            if len(executions) == 1:
                entered.set()
                await asyncio.Event().wait()
            return {"done": True}
        provider = ScriptedCompletion(
            [chunk("Before tool"), chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")],
            [chunk("After tool", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                component_factories=(lambda: ExampleTools(tool),),
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            await runtime.start()
            try:
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                await runtime.submit(sid, "work")
                await asyncio.wait_for(entered.wait(), 10)
                await runtime.interrupt(sid)
                await session.run.wait_idle()
                original, = await runtime.turns(sid)
                self.assertFalse((await runtime.snapshot()).questions)
                await runtime.resume_latest(sid)
                question, = (await runtime.snapshot()).questions
                self.assertEqual(question["request"]["category"], "execution.retry_uncertain")
                self.assertEqual(len(executions), 1)
                self.assertEqual(len(await session.run.alist()), 1)
                # A cancelled review can be explicitly reopened, without approval.
                handle = await session.run.aload(original["run_id"])
                request, = await handle.ainteractions(pending_only=True)
                await handle.acancel_interaction(request)
                await runtime.resume_latest(sid)
                question, = (await runtime.snapshot()).questions
                self.assertEqual(len(executions), 1)
                await runtime.answer_question(runtime.project.id, sid, question["run_id"], question["request"], "approve", None)
                await session.run.wait_idle()
                final, = await runtime.turns(sid)
                self.assertEqual(final["id"], original["id"])
                self.assertEqual(final["status"], "completed")
                self.assertEqual(len(executions), 2)
                self.assertFalse((await runtime.snapshot()).questions)
                self.assertNotIn("hub_resume_interactions", (await session.aget_data()).metadata)
                assistants = [m for m in (await runtime.snapshot()).messages if m.role == "assistant"]
                self.assertEqual(len(assistants), 1)
                self.assertIn("Before tool", assistants[0].text)
                self.assertIn("After tool", assistants[0].text)
            finally:
                await runtime.close()

    async def test_resume_latest_interrupted_loop_keeps_partial_reference_and_position(self):
        release = threading.Event()
        requests = []
        def completion(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                yield chunk("unfinished answer")
                release.wait(10)
            else:
                yield chunk("continued answer", finish="stop")
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=completion))}))
            await runtime.start()
            try:
                sid = await runtime.new_session()
                request = await runtime.submit(sid, "continue this task")
                session = runtime.sessions[sid]
                for _ in range(100):
                    messages = await session.aconversation()
                    if any(message.role == "assistant" and message.content == "unfinished answer" for message in messages):
                        break
                    await asyncio.sleep(.02)
                else:
                    self.fail("Partial output was not stored")
                stopping = asyncio.create_task(runtime.interrupt(sid))
                await asyncio.sleep(.05)
                release.set()
                self.assertTrue(await asyncio.wait_for(stopping, 5))
                first, = await runtime.turns(sid)
                self.assertEqual(first["status"], "interrupted")
                await runtime.resume_latest(sid)
                await session.run.wait_idle()
                row, = await runtime.turns(sid)
                self.assertEqual(row["id"], first["id"])
                self.assertEqual(row["status"], "completed")
                self.assertIn("unfinished answer", str(requests[-1]["messages"]))
                self.assertEqual(len(requests), 2)
                self.assertIsNone(await runtime.resume_latest(sid))
            finally:
                release.set()
                await runtime.close()

    async def test_early_interrupt_without_checkpoint_restarts_in_place_after_reopen(self):
        entered, release = asyncio.Event(), asyncio.Event()
        provider = ScriptedCompletion([chunk("later answer", finish="stop")], [chunk("fresh answer", finish="stop")])

        original_consume = RunManager._consume_limited
        async def preparing(manager, runtime, run):
            if not release.is_set():
                entered.set()
                await release.wait()
            return await original_consume(manager, runtime, run)
        preparation = patch.object(RunManager, "_consume_limited", preparing)
        preparation.start()
        self.addCleanup(preparation.stop)

        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
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

    async def test_backend_denial_cannot_be_overridden_by_empty_steps(self):
        from hub.backend.history import TurnHistory
        from hub.locales import Language
        request = SimpleNamespace(id="u", role="user", run_id="r", metadata={},
                                  status="committed", content="request", created_at="")
        run = SimpleNamespace(id="r", engine="loop", status="failed", metadata={})
        handle = SimpleNamespace(aget_data=AsyncMock(return_value=run))
        plan = SimpleNamespace(can_resume=False, can_restart=False, blockers=[],
            restart_blockers=[Diagnostic("checkpoint_protected", "No durable proof of a pre-execution stop")])
        session = SimpleNamespace(aconversation=AsyncMock(return_value=[request]), run=SimpleNamespace(
            astatus=AsyncMock(return_value=SimpleNamespace(active_run_id=None, queued_count=0)),
            aload=AsyncMock(return_value=handle), resume_plan=AsyncMock(return_value=plan),
            resume=AsyncMock(), restart=AsyncMock()))
        runtime = SimpleNamespace(sessions={"s": session}, namer=SimpleNamespace(tasks={}), t=Language(), submit=AsyncMock())
        with self.assertRaisesRegex(ValueError, "No durable proof"):
            await TurnHistory(runtime).retry("s", "u", "r")
        runtime.submit.assert_not_awaited()
        session.run.resume.assert_not_awaited()
        session.run.restart.assert_not_awaited()

    async def test_revision_conflict_never_falls_back_to_a_new_request(self):
        from hub.backend.history import TurnHistory
        from hub.locales import Language
        for action in ("resume", "restart"):
            with self.subTest(action=action):
                request = SimpleNamespace(id="u", role="user", run_id="r", metadata={},
                                          status="committed", content="request", created_at="")
                run = SimpleNamespace(id="r", engine="loop", status="failed", metadata={})
                plan = SimpleNamespace(can_resume=action == "resume", can_restart=action == "restart",
                                       revision="reviewed", blockers=[], restart_blockers=[])
                api = SimpleNamespace(
                    astatus=AsyncMock(return_value=SimpleNamespace(active_run_id=None, queued_count=0)),
                    aload=AsyncMock(return_value=SimpleNamespace(aget_data=AsyncMock(return_value=run))),
                    resume_plan=AsyncMock(return_value=plan), resume=AsyncMock(), restart=AsyncMock())
                getattr(api, action).side_effect = CheckpointError("checkpoint_conflict", "changed")
                runtime = SimpleNamespace(sessions={"s": SimpleNamespace(run=api,
                    aconversation=AsyncMock(return_value=[request]))}, namer=SimpleNamespace(tasks={}),
                    t=Language(), submit=AsyncMock())
                with self.assertRaises(CheckpointError):
                    await TurnHistory(runtime).retry("s", "u", "r")
                kwargs = {"partial_output": True} if action == "resume" else {}
                self.assertEqual(getattr(api, action).await_count, 2)
                getattr(api, action).assert_awaited_with("r", engine="loop", expected_revision="reviewed", **kwargs)
                getattr(api, "restart" if action == "resume" else "resume").assert_not_awaited()
                runtime.submit.assert_not_awaited()

    async def test_revision_conflict_replans_once_before_safe_admission(self):
        from hub.backend.history import TurnHistory
        from hub.locales import Language
        request = SimpleNamespace(id="u", role="user", run_id="r", metadata={}, status="committed", content="work")
        run = SimpleNamespace(id="r", engine="loop", status="interrupted", metadata={})
        plans = [SimpleNamespace(can_resume=True, can_restart=False, blockers=[], revision=version) for version in ("old", "new")]
        api = SimpleNamespace(astatus=AsyncMock(return_value=SimpleNamespace(active_run_id=None, queued_count=0)),
            aload=AsyncMock(return_value=SimpleNamespace(aget_data=AsyncMock(return_value=run))),
            resume_plan=AsyncMock(side_effect=plans), resume=AsyncMock(side_effect=[
                CheckpointError("checkpoint_conflict", "changed"), SimpleNamespace(id="new-request")]), restart=AsyncMock())
        runtime = SimpleNamespace(sessions={"s": SimpleNamespace(run=api, aconversation=AsyncMock(return_value=[request]))},
            namer=SimpleNamespace(tasks={}), t=Language(), submit=AsyncMock(),
            snapshot_reader=SimpleNamespace(invalidate=Mock()), dirty=asyncio.Event())
        self.assertEqual(await TurnHistory(runtime).retry("s", "u", "r"), "new-request")
        self.assertEqual([call.kwargs["expected_revision"] for call in api.resume.await_args_list], ["old", "new"])
        api.restart.assert_not_awaited()
        runtime.submit.assert_not_awaited()

    async def test_edit_approval_waiting_request_starts_fresh_without_old_effects(self):
        provider = ScriptedCompletion([chunk(calls=[call('{"path":"old.txt","content":"old"}',
            name="file_create")], finish="tool_calls")], [chunk("edited response", finish="stop")])
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(Path(directory, "workspace"), file_root=directory, model="test/model",
                auto_title=False, engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
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
                               engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
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
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
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
            history = HistoryUI(SimpleNamespace(view=view, _call=invoke, _snapshot=lambda _: None, _submitting=set()))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    history.show(sid, rows)
                    popup = view._dialog
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
                    view._dialog.editor.text = "edited first"
                pipe.send_text(" e")
                await eventually(lambda: any(op == "edit_turn" for op, _ in calls))
                self.assertIn(("edit_turn", (sid, "u0", "edited first", "u0")), calls)
                with set_app(app):
                    history.show(sid, rows)
                pipe.send_text(" r")
                await eventually(lambda: ("retry_turn", (sid, "u0", "r0")) in calls)
                with set_app(app):
                    history.show(sid, rows)
                    view._dialog.choices.current_values = ["u1"]
                pipe.send_text("r")
                await eventually(lambda: view._dialog is None)
                self.assertEqual(view.notice, view.t("history_retry_unavailable"))
                with set_app(app):
                    history.show(sid, rows)
                    view._dialog.buttons[-1].handler()
                self.assertIsNone(view._dialog)
            finally:
                app.exit()
                await task
                view.close()
