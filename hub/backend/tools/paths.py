"""Accept model-supplied absolute paths only inside the captured working root."""

from dataclasses import replace
from pathlib import Path

from llm.components.tools import ToolRegistry


def relative_path(root: Path, value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return value  # The backend validates invalid values and relative paths.
    try:
        return str(Path(value).relative_to(root))
    except ValueError:
        raise ValueError(f"Path must stay inside the working root ({root}); use a path inside this directory") from None


def bind_paths(toolkit):
    """Keep approvals/raw arguments intact; adapt paths at the execution boundary."""
    root = toolkit.files.root

    def handler(tool):
        async def invoke(arguments):
            args = dict(arguments)
            # Isolated workspace paths belong to that workspace, not the host root.
            if "workspace_id" not in args:
                keys = ("cwd",) if tool.name in ("process_start", "shell_execute") else (
                    ("path", "destination") if tool.name.startswith("file_") else ())
                for key in keys:
                    if key in args:
                        args[key] = relative_path(root, args[key])
                if tool.name == "test_workspace_create" and isinstance(args.get("source_paths"), list):
                    args["source_paths"] = [relative_path(root, value) for value in args["source_paths"]]
            return await tool.handler(args)
        return invoke

    tools = []
    for name in toolkit.registry.names():
        tool = toolkit.registry.get(name)
        if name.startswith("file_") or name in ("process_start", "shell_execute", "test_workspace_create"):
            contract = tool.contract
            tool = replace(tool, handler=handler(tool), contract=replace(contract,
                revision="hub-relative-paths-v1:" + contract.revision))
        tools.append(tool)
    toolkit.registry = ToolRegistry(tuple(tools))
