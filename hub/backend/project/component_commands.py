"""The public slash surface only opens component managers and their help."""

from ...components import COMPONENTS

BUILTINS = ("help", "engine", "new", "clone", "stop", "details")
ACTIONS = ("config", "help")
MANAGED_COMPONENTS = tuple(spec.name for spec in COMPONENTS)


def component_commands(names: tuple[str, ...]) -> dict[str, str]:
    commands = {name: name for name in MANAGED_COMPONENTS if name in names}
    for spec in sorted(COMPONENTS, key=lambda item: item.alias_order):
        if spec.name in commands:
            commands.update({command: spec.name for command in spec.commands})
    return commands


def parse_arguments(argument: str, language) -> str:
    parts = argument.split()
    if not parts:
        return "help"
    if len(parts) != 1 or parts[0] not in ACTIONS:
        raise ValueError(language("component_usage_error"))
    return parts[0]
