"""The public slash surface only opens component managers and their help."""

from hub.locales.keys import MessageKey

from ...components import COMPONENTS, ManagedComponents
from enum import StrEnum


MAX_ARGUMENTS = 1


class ComponentAction(StrEnum):
    CONFIG = "config"
    HELP = "help"


ACTIONS = tuple(action.value for action in ComponentAction)
MANAGED_COMPONENTS = tuple(spec.name for spec in COMPONENTS)


def component_commands(names: tuple[str, ...]) -> dict[str, str]:
    return {
        name: name
        for name in MANAGED_COMPONENTS
        if name == ManagedComponents.AGENTS.name or name in names
    }


def parse_arguments(argument: str, language) -> str:
    parts = argument.split()
    if not parts:
        return ComponentAction.HELP.value
    if len(parts) != MAX_ARGUMENTS or parts[0] not in ACTIONS:
        raise ValueError(language(MessageKey.COMPONENT_USAGE_ERROR))
    return parts[0]
