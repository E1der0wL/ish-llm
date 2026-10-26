"""Singular manager commands; help belongs to the manager popup."""

from hub.locales.keys import MessageKey

from ...components import COMPONENTS, ManagedComponents




def component_commands(names: tuple[str, ...]) -> dict[str, str]:
    return {
        spec.tool_prefix: spec.name
        for spec in COMPONENTS
        if spec.name == ManagedComponents.AGENTS.name or spec.name in names
    }


def parse_arguments(argument: str, language) -> None:
    parts = argument.split()
    if parts:
        raise ValueError(language(MessageKey.COMPONENT_USAGE_ERROR))
