"""Only registered slash commands intercept a request before submission."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.ui.application import create_application
from hub.ui.input.commands import HubCommands
from hub.backend.project.component_commands import ACTIONS, component_commands, parse_arguments
from hub.ui.chat.completion import HubCompleter
from hub.locales import Language
from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent
from llm.engines.loop import LoopEngine
from tests.hub.support import SizedOutput
from tests.hub.test_features import chunk
from tests.hub.test_live import until


class SlashCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_builtin_completion_and_dispatch_share_the_public_commands(self):
        language = Language("en")
        completer = HubCompleter(lambda: ("loop", "graph"), lambda: ".", language)
        suggestions = list(completer.get_completions(Document("/"), CompleteEvent()))
        cases = (
            ("/help", "help_dialog", ()),
            ("/engine graph", "choose_engine", ("graph",)),
            ("/new   New session  ", "session_dialog", ("new", "New session")),
            ("/clone Copy", "session_dialog", ("clone", "Copy")),
            ("/stop", "on_interrupt", ("session",)),
            ("/details", "toggle_details", ()),
        )
        self.assertEqual([item.text for item in suggestions], [text.split()[0] for text, _, _ in cases])
        for suggestion, (text, method, arguments) in zip(suggestions, cases):
            with self.subTest(command=text):
                self.assertEqual(suggestion.display_meta_text, language("command_" + suggestion.text[1:]))
                view = Mock(component_commands={}, selected=0, sessions=[SimpleNamespace(id="session")])
                view.composer.text = text
                self.assertTrue(HubCommands(view).execute(text))
                getattr(view, method).assert_called_once_with(*arguments)
                self.assertEqual(view.composer.text, "")
                view.on_submit.assert_not_called()
        self.assertEqual([item.text for item in completer.get_completions(
            Document("/engine gr"), CompleteEvent())], ["graph"])

    def test_only_managed_components_and_two_actions_are_exposed(self):
        commands = component_commands(("tools", "rag", "skills", "workflows", "agents", "memory", "goals", "refinement", "mcp"))
        self.assertEqual(set(commands), {"tools", "rag", "skills", "workflows", "agents", "mcp", "memory"})
        self.assertEqual(set(commands), set(commands.values()))
        language = Language("ko")
        completer = HubCompleter(lambda: (), lambda: ".", language, components=lambda: commands)
        suggestions = [item.text for item in completer.get_completions(Document("/"), CompleteEvent())]
        for alias in ("tool", "skill", "workflow", "agent"):
            self.assertNotIn("/" + alias, suggestions)
            view = Mock(component_commands=commands)
            self.assertFalse(HubCommands(view).execute(f"/{alias} config"))
            view.on_component.assert_not_called()
        for command in commands:
            self.assertEqual(suggestions.count("/" + command), 1)
            view = Mock(component_commands=commands)
            self.assertTrue(HubCommands(view).execute(f"/{command} config"))
            view.on_component.assert_called_once_with(command, "config")
            values = [item.text for item in completer.get_completions(Document(f"/{command} "), CompleteEvent())]
            self.assertEqual(values, list(ACTIONS))
        for action in ("list", "get id", "create id {}", "update id {}", "delete id", "settings", "install id", "config extra"):
            with self.assertRaises(ValueError):
                parse_arguments(action, language)
        self.assertEqual(parse_arguments("", language), "help")
    def test_registered_commands_keep_their_actions(self):
        view = Mock(component_commands={"rag": "RAG"})
        commands = HubCommands(view)
        self.assertTrue(commands.execute("/help"))
        view.help_dialog.assert_called_once_with()
        self.assertEqual(view.composer.text, "")
        self.assertTrue(commands.execute("/rag config"))
        view.on_component.assert_called_once_with("rag", "config")
        view.on_submit.assert_not_called()

    async def test_unknown_slash_input_reaches_provider_unchanged(self):
        requests = []

        def provider(**kwargs):
            text = kwargs["messages"][-1]["content"]
            requests.append(text)
            yield chunk(content=text, finish="stop")

        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, model="openai/test", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text("/new\r\r")
                await until(lambda: not view.no_sessions and view._dialog is None)
                for index, text in enumerate(("/tmp/my project/file.py 를 확인해줘",
                                              "/경로/파일.txt", "/not-registered list", "/", "/preview"), 1):
                    with self.subTest(text=text):
                        pipe.send_text(text + "\r")
                        await until(lambda: len(requests) == index and not controller._submitting
                                    and any(m.role == "assistant" and m.text == text and m.status == "completed"
                                            for m in view.output.transcript.control.messages))
                        self.assertEqual(requests[-1], text)
                        self.assertEqual(view.composer.text, "")
                        self.assertTrue(any(m.role == "user" and m.text == text
                                            for m in view.output.transcript.control.messages))
                self.assertEqual(len(requests), 5)
                self.assertEqual(view.composer.text, "")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
