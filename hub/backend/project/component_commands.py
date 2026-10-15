"""The public slash surface only opens component managers and their help."""

from ...components import COMPONENTS

ACTIONS = ("config", "help")
MANAGED_COMPONENTS = tuple(spec.name for spec in COMPONENTS)


def component_commands(names: tuple[str, ...]) -> dict[str, str]:
    return {name: name for name in MANAGED_COMPONENTS if name in names}


def parse_arguments(argument: str, language) -> str:
    parts = argument.split()
    if not parts:
        return "help"
    if len(parts) != 1 or parts[0] not in ACTIONS:
        raise ValueError(language("component_usage_error"))
    return parts[0]
