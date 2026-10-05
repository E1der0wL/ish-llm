"""All registered public engines are available without an activation checklist."""


def selected_engines(config, registered) -> tuple[str, ...]:
    return tuple(name for name in registered if not name.startswith("_hub_"))
