"""Asynchronous path completion with the same confirmation keys in all popups."""

from prompt_toolkit.completion import PathCompleter, ThreadedCompleter

from ..controls import TextArea
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction


class PathInput(TextArea):
    COMPLETION_ATTRIBUTE = "hub_complete_path"
    DIRECTORIES_ONLY = False
    MULTILINE = False
    HEIGHT = 1
    WHILE_TYPING = True
    EXPAND_USER = True
    COMPLETES_PATHS = True
    NO_PATH_COMPLETION = False

    @classmethod
    def accepts(cls, control):
        return getattr(control, cls.COMPLETION_ATTRIBUTE, cls.NO_PATH_COMPLETION)

    def __init__(self, root, *, directories=DIRECTORIES_ONLY):
        super().__init__(
            multiline=self.MULTILINE,
            height=self.HEIGHT,
            complete_while_typing=self.WHILE_TYPING,
            completer=ThreadedCompleter(
                PathCompleter(
                    only_directories=directories,
                    expanduser=self.EXPAND_USER,
                    get_paths=lambda: [str(root() if callable(root) else root)],
                )
            ),
        )
        registry = ShortcutRegistry()

        def confirm(event):
            state = self.buffer.complete_state
            if state and state.completions:
                self.buffer.apply_completion(
                    state.current_completion or state.completions[0]
                )

        registry.add(
            [KeyName.TAB],
            ShortcutLabel.TAB,
            ShortcutAction.CONFIRM,
            confirm,
            when=lambda: bool(self.buffer.complete_state),
        )
        self.control.key_bindings = registry.bindings
        registry.attach(self.control)
        setattr(self.control, self.COMPLETION_ATTRIBUTE, self.COMPLETES_PATHS)
