"""Explicit completion of UI commands, engine names and paths beneath a project."""

from pathlib import Path
import re

from prompt_toolkit.completion import Completer, Completion

from ...backend.component_commands import ACTIONS, BUILTINS


COMMANDS = BUILTINS


class HubCompleter(Completer):
    def __init__(self, engines, root, language, *, components=lambda: {}) -> None:
        self.engines, self.root, self.t = engines, root, language
        self.components = components

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        if before.startswith("/") and " " not in before and "\n" not in before:
            for command in COMMANDS:
                value = "/" + command
                if value.startswith(before):
                    yield Completion(value, -len(before), display_meta=self.t("command_" + command))
            for command, name in self.components().items():
                value = "/" + command
                if value.startswith(before):
                    yield Completion(value, -len(before), display_meta=self.t("command_component", name=name))
            return
        command, separator, prefix = before.partition(" ")
        if separator and command[1:] in self.components() and before.startswith("/"):
            for action in ACTIONS:
                if action.startswith(prefix):
                    yield Completion(action, -len(prefix), display_meta=self.t("component_action_" + action))
            return
        if before.startswith("/engine ") and "\n" not in before:
            prefix = before[len("/engine "):]
            for name in self.engines():
                if name.startswith(prefix):
                    yield Completion(name, -len(prefix), display_meta=self.t("engine"))
            return
        match = re.search(r'(?:^|\s)@([^\n]*)$', before)
        if not match:
            return
        prefix = match.group(1)
        root = Path(self.root()).expanduser().resolve()
        candidate = root / prefix
        directory = candidate if prefix.endswith("/") else candidate.parent if prefix else root
        partial = "" if prefix.endswith("/") else candidate.name if prefix else ""
        try:
            if not directory.resolve().is_relative_to(root):
                return
            for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if path.name.startswith(partial) and path.resolve().is_relative_to(root):
                    value = path.relative_to(root).as_posix() + ("/" if path.is_dir() else "")
                    yield Completion(value, -len(prefix), display_meta=self.t("directory" if path.is_dir() else "file"))
        except OSError:
            return
