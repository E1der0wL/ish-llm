from hub.ui.presentation import present
"""User-visible engine selection, steering, naming, Markdown and completion."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest

from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.input import create_pipe_input

from hub.ui.chat.completion import HubCompleter
from hub.locales import Language, en, ko
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.naming import TitleEngine, TITLE_ENGINE
from hub.ui.application import create_application
from llm.llm import LargeLanguageModel
from llm.components.tools import ToolComponent
from llm.engines.loop import LoopEngine
from examples.hub.preview import create_preview
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput


def chunk(content=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class FeatureRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_instruction_preserves_run_queue_and_reasoning(self):
        gate = threading.Event()
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                yield chunk(reasoning="**Consider** the request.")
                gate.wait(10)
            yield chunk(content=kwargs["messages"][-1]["content"], finish="stop")
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "chat", "openai/test", auto_title=False, project_config={"parameters": {"engines": {"chat": {'config': {'completion': {'model': 'openai/test'}}}, "review": {'config': {'completion': {'model': 'openai/test'}}}}}},
                engine_factories={"chat": lambda: LoopEngine(completion_fn=provider),
                                  "review": lambda: LoopEngine(completion_fn=provider)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                identifier = runtime.selected_id
                await runtime.submit(identifier, "first", "chat")
                async with asyncio.timeout(10):
                    while not any(m.role == "reasoning" for m in (await runtime.snapshot()).messages):
                        await asyncio.sleep(0.02)
                snapshot = await runtime.snapshot()
                self.assertNotIn("\n", present(snapshot, runtime.t).activity)
                self.assertIn("LLM completion", present(snapshot, runtime.t).activity)
                run_id, targets = await runtime.instruction_targets(identifier)
                instruction = await runtime.steer(identifier, run_id, "additional direction", [targets[0][0]])
                await runtime.submit(identifier, "second request", "review")
                self.assertEqual((await runtime.sessions[identifier].run.astatus()).queued_count, 1)
                with self.assertRaisesRegex(ValueError, "복제"):
                    await runtime.new_session("clone", "blocked", identifier)
                gate.set()
                await runtime.sessions[identifier].run.wait_idle()
                run = await runtime.sessions[identifier].run.aload(run_id)
                instructions = await run.ainstructions()
                self.assertEqual(instructions[0].id, instruction)
                self.assertEqual(str(instructions[0].status), "applied")
                self.assertEqual((await run.aresponse()).content, "additional direction")
                self.assertEqual((await run.aresult()).completions[0].reasoning_content, "**Consider** the request.")
                runs = await runtime.sessions[identifier].run.alist()
                self.assertEqual([(await r.aget_data()).engine for r in runs], ["chat", "review"])
                self.assertEqual(len(calls), 3)
                self.assertFalse(any(m.role == "reasoning" for m in (await runtime.snapshot()).messages))
                clone = await runtime.new_session("clone", "Named copy", identifier)
                self.assertEqual((await runtime.sessions[clone].aget_data()).title, "Named copy")
                self.assertFalse(await runtime.sessions[clone].run.alist())
                self.assertTrue(await runtime.sessions[clone].aconversation())
            finally:
                gate.set()
                await runtime.close()
            reopened = HubRuntime(config)
            try:
                await reopened.start()
                run = await reopened.sessions[identifier].run.aload(run_id)
                self.assertEqual((await run.aresult()).completions[0].reasoning_content, "**Consider** the request.")
            finally:
                await reopened.close()

    async def test_auto_title_is_isolated_persisted_and_named_clone_is_preserved(self):
        title_calls = []
        def answer(**kwargs):
            yield chunk(content="A useful answer", finish="stop")
        def title(**kwargs):
            title_calls.append(kwargs)
            yield chunk(content="Generated title", finish="stop")
        def factory(config):
            return LargeLanguageModel(config.workspace, components=[ToolComponent()], engines={
                "loop": LoopEngine(completion_fn=answer), TITLE_ENGINE: TitleEngine(completion_fn=title)})
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "openai/test")
            runtime = HubRuntime(config, factory)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                identifier = runtime.selected_id
                await runtime.submit(identifier, "Name this conversation")
                await runtime.sessions[identifier].run.wait_idle()
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual((await runtime.sessions[identifier].aget_data()).title, "Generated title")
                self.assertEqual(len(await runtime.sessions[identifier].aconversation()), 2)
                copy = await runtime.new_session("clone", "My name", identifier)
                await runtime.snapshot()
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual((await runtime.sessions[copy].aget_data()).title, "My name")
                unnamed = await runtime.new_session("clone", "", identifier)
                await until(lambda: not runtime.namer.tasks)
                self.assertEqual((await runtime.sessions[unnamed].aget_data()).title, "Generated title")
                self.assertEqual(len(title_calls), 2)
                self.assertEqual(len((await runtime.snapshot()).sessions), 3)
            finally:
                await runtime.close()
            reopened = HubRuntime(config, factory)
            try:
                await reopened.start()
                self.assertEqual(len(reopened.sessions), 3)
                self.assertEqual((await reopened.sessions[identifier].aget_data()).title, "Generated title")
            finally:
                await reopened.close()


class CompletionTests(unittest.TestCase):
    def test_language_packs_and_project_path_completion(self):
        self.assertEqual(set(ko.MESSAGES), set(en.MESSAGES))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.md").write_text("not read by completion")
            (root / "sub folder").mkdir()
            completer = HubCompleter(lambda: ("loop", "review"), lambda: root, Language("en"))
            def values(text):
                return list(completer.get_completions(Document(text), CompleteEvent(completion_requested=True)))
            self.assertEqual([v.text for v in values("/eng")], ["/engine"])
            self.assertEqual([v.text for v in values("/engine r")], ["review"])
            self.assertEqual([v.text for v in values("Please read @no")], ["notes.md"])
            self.assertEqual([v.text for v in values("@sub f")], ["sub folder/"])
            self.assertEqual(values("@../"), [])


class FeatureUITests(unittest.IsolatedAsyncioTestCase):
    async def test_arrows_select_tab_confirms_and_ctrl_space_inserts_newline(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / "notes.md").write_text("fixture")
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.show_preview = True
            view.file_root = root
            view.engines = ("loop", "review")
            calls = []
            view.on_steer = lambda *args: calls.append(args)
            view.on_submit = lambda *args: calls.append(args)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                for prefix, expected in (("/eng", "/engine"), ("/engine r", "review"),
                                         ("Read @no", "notes.md")):
                    view.composer.text = ""
                    pipe.send_text(prefix)
                    # PTK publishes an empty completion state before the threaded
                    # completer yields candidates, including for partial input.
                    await until(lambda: view.composer.text == prefix
                                and (state := view.composer.buffer.complete_state) is not None
                                and state.original_document.text_before_cursor == prefix
                                and bool(state.completions))
                    state = view.composer.buffer.complete_state
                    self.assertIn(expected, [item.text for item in state.completions])
                    self.assertIsNone(state.current_completion)
                    pipe.send_text("\x1b[B")
                    await until(lambda: view.composer.buffer.complete_state.current_completion is not None)
                    self.assertEqual(view.composer.buffer.complete_state.current_completion.text, expected)
                    self.assertIs(app.layout.current_control, view.composer.control)
                    chosen = view.composer.text
                    pipe.send_text("\t")
                    await until(lambda: view.composer.buffer.complete_state is None)
                    self.assertEqual(view.composer.text, chosen)
                    pipe.send_text("\x1b")
                    await until(lambda: app.layout.current_control == view._session_control)
                    self.assertEqual(view.composer.text, chosen)
                    self.assertIsNone(view.composer.buffer.complete_state)
                    pipe.send_text("\t")
                    await until(lambda: app.layout.current_control == view.composer.control)
                self.assertEqual(calls, [])
                # Multiple candidates: arrows choose, Tab accepts that exact item.
                view.composer.text = ""
                pipe.send_text("/")
                await until(lambda: view.composer.buffer.complete_state is not None)
                pipe.send_text("\x1b[B\x1b[B\x1b[A")
                await until(lambda: view.composer.buffer.complete_state.complete_index == 0)
                chosen = view.composer.text
                pipe.send_text("\t")
                await until(lambda: view.composer.buffer.complete_state is None)
                self.assertEqual(view.composer.text, chosen)
                # Tab without an arrow selection accepts the first displayed item.
                view.composer.text = ""
                pipe.send_text("/eng")
                await until(lambda: view.composer.buffer.complete_state is not None)
                pipe.send_text("\t")
                await until(lambda: view.composer.buffer.complete_state is None)
                self.assertEqual(view.composer.text, "/engine")
                # Newline cancels an unconfirmed suggestion and never sends a request.
                view.composer.text = ""
                pipe.send_text("/eng")
                await until(lambda: view.composer.buffer.complete_state is not None)
                pipe.send_text("\x1b[B")
                await until(lambda: view.composer.buffer.complete_state.current_completion is not None)
                pipe.send_text("\x00")
                await until(lambda: view.composer.text == "/eng\n")
                self.assertIsNone(view.composer.buffer.complete_state)
                view.composer.buffer.document = Document("firstsecond", cursor_position=5)
                pipe.send_text("\x00")
                await until(lambda: view.composer.text == "first\nsecond")
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertFalse(view.transcript.control.is_focusable())
                self.assertFalse(view.draft_preview.control.is_focusable())
                self.assertEqual(calls, [])
            finally:
                app.exit()
                await task

    async def test_live_enter_steering_follow_up_and_engine_command_use_backend(self):
        gate = threading.Event()
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                yield chunk(reasoning="Working on it")
                gate.wait(10)
            yield chunk(content=kwargs["messages"][-1]["content"], finish="stop")
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, "loop", "openai/test", language="en", auto_title=False, project_config={"parameters": {"engines": {"review": {'config': {'completion': {'model': 'openai/test'}}}}}},
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider),
                                  "review": lambda: LoopEngine(completion_fn=provider)})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("/new\r\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("start\r")
                await until(lambda: any(m.role == "reasoning" for m in view.transcript.control.messages))
                pipe.send_text("redirect\r")
                await until(lambda: view._dialog is not None)
                pipe.send_text("\x1b")  # Cancels the choice, not the running work.
                await until(lambda: view._dialog is None)
                self.assertEqual(view.composer.text, "redirect")
                state = await asyncio.wrap_future(controller.worker.call("submission_state", view.sessions[view.selected].id))
                self.assertTrue(state["run_id"])
                pipe.send_text("\r")
                await until(lambda: view._dialog is not None)
                pipe.send_text("\x1b[A\r")  # Select live instruction.
                await until(lambda: any(m.text == "redirect" for m in view.transcript.control.messages))
                await until(lambda: view.composer.text == "")
                pipe.send_text("/engine review\r")
                await until(lambda: view.engine == "review")
                pipe.send_text("next request\r")
                await until(lambda: view._dialog is not None)
                pipe.send_text("\r")  # Confirm follow-up.
                await until(lambda: view.composer.text == "")
                gate.set()
                await until(lambda: any(m.role == "assistant" and m.text == "next request"
                                        for m in view.transcript.control.messages))
                self.assertEqual(len(calls), 3)
                self.assertTrue(any(m.text == "redirect" and m.status == "applied"
                                    for m in view.transcript.control.messages))
                await until(lambda: "Idle" in view.sessions[view.selected].status)
                self.assertFalse(controller._submitting)
                pipe.send_text("keep instruction\x04")
                await until(lambda: view.composer.text == "keep instruction")
                pipe.send_text("\x00")  # Process a later key before checking the removed binding.
                await until(lambda: view.composer.text.endswith("\n"))
                self.assertEqual(len(calls), 3)
                view.composer.text = view.composer.text.rstrip("\n")
                self.assertEqual(view.composer.text, "keep instruction")
            finally:
                gate.set()
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_finished_run_during_choice_does_not_turn_instruction_into_follow_up(self):
        gate = threading.Event()
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            yield chunk(reasoning="Working")
            gate.wait(10)
            yield chunk(content="Done", finish="stop")
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, model="openai/test", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("/new\r\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("first\r")
                await until(lambda: any(m.role == "reasoning" for m in view.transcript.control.messages))
                pipe.send_text("keep draft\r")
                await until(lambda: view._dialog is not None)
                gate.set()
                await until(lambda: any(m.role == "assistant" and m.status == "completed"
                                       for m in view.transcript.control.messages))
                pipe.send_text("\x1b[A\r")
                await until(lambda: view._dialog is None and view.notice.startswith(view.t("error", error="")))
                self.assertEqual(view.composer.text, "keep draft")
                self.assertEqual(len(calls), 1)
                pipe.send_text("\r")  # Now idle: a normal request, without another chooser.
                await until(lambda: len(calls) == 2 and view.composer.text == "")
            finally:
                gate.set()
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_direct_scroll_dialogs_and_removed_ctrl_d(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput(rows=24))
            calls = []
            view.on_steer = lambda session, text: calls.append((session, text))
            view.on_new = lambda *args: calls.append(args)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                pipe.send_text("**draft**\x04")
                pipe.send_text("\x00")
                await until(lambda: view.composer.text == "**draft**\n")
                view.composer.text = "**draft**"
                self.assertEqual(calls, [])
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(view.draft_preview.control.messages[0].text, "**draft**")
                app.invalidate()
                await until(lambda: view.transcript.window.render_info is not None)
                pipe.send_text("\x1b\x1b[B")
                await until(lambda: view.transcript.window.vertical_scroll == 1)
                pipe.send_text("\x1b\x1b[A")
                await until(lambda: view.transcript.window.vertical_scroll == 0)
                app.layout.focus(view._session_control)
                pipe.send_text("c")
                await until(lambda: view._dialog is not None)
                pipe.send_text("Named session\r")
                await until(lambda: view._dialog is None)
                self.assertEqual(calls[-1][:2], ("new", "Named session"))
                self.assertEqual(view.composer.text, "**draft**")
                view.engines = ("loop", "review")
                pipe.send_text("\x05")  # Ctrl+E
                await until(lambda: view._dialog is not None)
                pipe.send_text("\x1b[B\t\r")  # review, Confirm
                await until(lambda: view._dialog is None)
                self.assertEqual(view.engine, "review")
                app.layout.focus(view._session_control)
                pipe.send_text("c")
                await until(lambda: view._dialog is not None)
                pipe.send_text("\x1b")
                await until(lambda: view._dialog is None)
                self.assertTrue(view.visible)
                self.assertFalse(task.done())
                app.layout.focus(view.composer)
                view.composer.text = ""
                pipe.send_text("/eng")
                await until(lambda: view.composer.buffer.complete_state is not None)
                self.assertEqual(view.composer.buffer.complete_state.completions[0].text, "/engine")
                pipe.send_text("\x1b[B\r")
                await until(lambda: view.composer.text == "/engine")
                self.assertIsNone(view._dialog)
                header = "".join(part[1] for part in view._header())
                self.assertEqual(header, " •  demo-project")
            finally:
                app.exit()
                await task
