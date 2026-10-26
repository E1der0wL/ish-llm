"""Explicit completion of UI commands and paths beneath a project."""

from hub.locales.keys import MessageKey

from pathlib import Path
import re

from prompt_toolkit.completion import Completer, Completion

from ..input.commands import (
    COMMANDS,
    CommandInput,
    CommandSyntax,
)


class HubCompleter(Completer):
    PATH_PATTERN = r"(?:^|\s)@([^\n]*)$"
    PATH_SEPARATOR = "/"
    EMPTY_PARTIAL = ""

    def __init__(self, root, language, *, components=lambda: {}) -> None:
        self.root, self.t = root, language
        self.components = components

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        parsed = CommandInput.parse(before)
        if parsed.partial_command:
            for command in COMMANDS:
                value = CommandSyntax.spelling(command.name)
                if value.startswith(before):
                    yield Completion(
                        value,
                        -len(before),
                        display_meta=self.t(command.description_key),
                    )
            for command, name in self.components().items():
                value = CommandSyntax.spelling(command)
                if value.startswith(before):
                    yield Completion(
                        value,
                        -len(before),
                        display_meta=self.t(MessageKey.COMMAND_COMPONENT, name=name),
                    )
            return
        match = re.search(self.PATH_PATTERN, before)
        if not match:
            return
        prefix = match.group(1)
        root = Path(self.root()).expanduser().resolve()
        candidate = root / prefix
        directory = (
            candidate
            if prefix.endswith(self.PATH_SEPARATOR)
            else candidate.parent if prefix else root
        )
        partial = (
            self.EMPTY_PARTIAL
            if prefix.endswith(self.PATH_SEPARATOR)
            else candidate.name if prefix else self.EMPTY_PARTIAL
        )
        try:
            if not directory.resolve().is_relative_to(root):
                return
            for path in sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())
            ):
                if path.name.startswith(partial) and path.resolve().is_relative_to(
                    root
                ):
                    value = path.relative_to(root).as_posix() + (
                        self.PATH_SEPARATOR if path.is_dir() else self.EMPTY_PARTIAL
                    )
                    yield Completion(
                        value,
                        -len(prefix),
                        display_meta=self.t(
                            MessageKey.DIRECTORY if path.is_dir() else MessageKey.FILE
                        ),
                    )
        except OSError:
            return
