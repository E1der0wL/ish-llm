"""Slash commands route to the same screen actions as keyboard input."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..view import HubView


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    handler: Callable[["HubView", str], object]
    arguments: Literal["engines"] | None = None

    @property
    def description_key(self) -> str:
        return "command_" + self.name


# Execution and completion consume the same ordered definitions. Keep the
# request-scoped engine/Workflow options available for the future Graph page.
COMMANDS = (
    CommandSpec("help", lambda view, argument: view.help_dialog()),
    CommandSpec("engine", lambda view, argument: view.choose_engine(argument), arguments="engines"),
    CommandSpec("new", lambda view, argument: view.session_dialog("new", argument)),
    CommandSpec("clone", lambda view, argument: view.session_dialog("clone", argument)),
    CommandSpec("stop", lambda view, argument: view.on_interrupt and view.on_interrupt(view.sessions[view.selected].id)),
    CommandSpec("details", lambda view, argument: view.toggle_details()),
)


def find_command(name: str) -> CommandSpec | None:
    return next((command for command in COMMANDS if command.name == name), None)


class HubCommands:
    def __init__(self, view):
        self.view = view

    def execute(self, text):
        view = self.view
        if not text.startswith("/"):
            return False
        command, _, argument = text.partition(" ")
        if command[1:] in view.component_commands:
            if view.on_component:
                view.on_component(command[1:], argument.strip())
            return True
        spec = find_command(command[1:])
        if spec is None:
            # Absolute paths and other unregistered slash input are user text.
            return False
        view.composer.text = ""
        spec.handler(view, argument.strip())
        return True

