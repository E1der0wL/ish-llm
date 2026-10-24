"""Accept model-supplied absolute paths only inside the captured working root."""

from dataclasses import replace
from pathlib import Path
from llm.components.tools import ToolRegistry
from llm.components.tools.builtin.path_values import BuiltinPathArguments


class PathBinding:
    OUTSIDE_ROOT = "Path must stay inside the working root ({root}); use a path inside this directory"
    REVISION_PREFIX = "hub-relative-paths-v1:"


def relative_path(root: Path, value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return value
    try:
        return str(Path(value).relative_to(root))
    except ValueError:
        raise ValueError(PathBinding.OUTSIDE_ROOT.format(root=root)) from None


def bind_paths(toolkit):
    """Keep approvals/raw arguments intact; adapt paths at the execution boundary."""
    root = toolkit.files.root

    def handler(tool):
        async def invoke(arguments):
            args = BuiltinPathArguments.remap(
                tool.name, arguments, lambda value: relative_path(root, value)
            )
            return await tool.handler(args)

        return invoke

    tools = []
    for name in toolkit.registry.names():
        tool = toolkit.registry.get(name)
        if BuiltinPathArguments.adapts(name):
            contract = tool.contract
            tool = replace(
                tool,
                handler=handler(tool),
                contract=replace(
                    contract, revision=PathBinding.REVISION_PREFIX + contract.revision
                ),
            )
        tools.append(tool)
    toolkit.registry = ToolRegistry(tuple(tools))
