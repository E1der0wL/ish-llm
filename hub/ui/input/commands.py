"""Slash commands route to the same screen actions as keyboard input."""

from hub.locales.keys import MessagePattern

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from enum import StrEnum
from hub.backend.session_contracts import SessionMode

if TYPE_CHECKING:
    from ..view import HubView


class CoreCommand(StrEnum):
    HELP = "help"
    AGENT = "agent"
    NEW = SessionMode.NEW.value
    CLONE = SessionMode.CLONE.value
    STOP = "stop"
    DETAILS = "details"


class CommandArguments(StrEnum):
    AGENTS = "agents"


class CommandSyntax:
    PREFIX = "/"
    SEPARATOR = " "
    NEWLINE = "\n"
    EMPTY = ""
    UNREGISTERED = None

    @classmethod
    def spelling(cls, name):
        return cls.PREFIX + name


@dataclass(frozen=True, slots=True)
class CommandInput:
    text: str
    command: str
    separator: str
    argument: str

    @classmethod
    def parse(cls, text):
        return cls(text, *text.partition(CommandSyntax.SEPARATOR))

    @property
    def prefixed(self):
        return self.text.startswith(CommandSyntax.PREFIX)

    @property
    def name(self):
        return self.command[len(CommandSyntax.PREFIX) :]

    @property
    def single_line(self):
        return CommandSyntax.NEWLINE not in self.text

    @property
    def partial_command(self):
        return (
            self.prefixed
            and CommandSyntax.SEPARATOR not in self.text
            and self.single_line
        )


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    handler: Callable[["HubView", str], object]
    arguments: str | None = CommandSyntax.UNREGISTERED

    @property
    def description_key(self) -> str:
        return MessagePattern.command(self.name)


# Execution and completion consume the same ordered definitions. Keep the
# saved Project Agent choices shared by execution and completion.
COMMANDS = (
    CommandSpec(CoreCommand.HELP.value, lambda view, argument: view.help_dialog()),
    CommandSpec(
        CoreCommand.AGENT.value,
        lambda view, argument: view.choose_agent(argument),
        arguments=CommandArguments.AGENTS.value,
    ),
    CommandSpec(
        CoreCommand.NEW.value,
        lambda view, argument: view.session_dialog(SessionMode.NEW.value, argument),
    ),
    CommandSpec(
        CoreCommand.CLONE.value,
        lambda view, argument: view.session_dialog(SessionMode.CLONE.value, argument),
    ),
    CommandSpec(
        CoreCommand.STOP.value,
        lambda view, argument: view.on_interrupt
        and view.on_interrupt(view.sessions[view.selected].id),
    ),
    CommandSpec(CoreCommand.DETAILS.value, lambda view, argument: view.toggle_details()),
)


def find_command(name: str) -> CommandSpec | None:
    return next(
        (command for command in COMMANDS if command.name == name),
        CommandSyntax.UNREGISTERED,
    )


class HubCommands:
    HANDLED = True
    UNHANDLED = False

    def __init__(self, view):
        self.view = view

    def execute(self, text):
        view = self.view
        if not text.startswith(CommandSyntax.PREFIX):
            return self.UNHANDLED
        parsed = CommandInput.parse(text)
        argument = parsed.argument
        if parsed.name in view.component_commands:
            if view.on_component:
                view.on_component(parsed.name, argument.strip())
            return self.HANDLED
        spec = find_command(parsed.name)
        if spec is CommandSyntax.UNREGISTERED:
            # Absolute paths and other unregistered slash input are user text.
            return self.UNHANDLED
        view.composer.text = CommandSyntax.EMPTY
        spec.handler(view, argument.strip())
        return self.HANDLED
