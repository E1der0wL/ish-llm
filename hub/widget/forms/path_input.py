"""Asynchronous path completion with the same confirmation keys in all popups."""

from prompt_toolkit.completion import PathCompleter, ThreadedCompleter

from ..controls import TextArea
from ...ui.input.registry import ShortcutRegistry


class PathInput(TextArea):
    def __init__(self, root, *, directories=False):
        super().__init__(multiline=False, height=1, complete_while_typing=True,
                         completer=ThreadedCompleter(PathCompleter(
                             only_directories=directories, expanduser=True,
                             get_paths=lambda: [str(root() if callable(root) else root)])))
        registry = ShortcutRegistry()

        def confirm(event):
            state = self.buffer.complete_state
            if state and state.completions:
                self.buffer.apply_completion(state.current_completion or state.completions[0])

        registry.add(["tab"], "Tab", "confirm", confirm,
                     when=lambda: bool(self.buffer.complete_state))
        self.control.key_bindings = registry.bindings
        self.control.hub_shortcuts = registry
        self.control.hub_complete_path = True
