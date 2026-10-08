"""Tool package UI adapter: public component CRUD, no source imports or execution."""

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path


def description(source):
    try:
        module = ast.parse(source)
        main = next((node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == "main"), None)
        return (ast.get_docstring(main) if main else ast.get_docstring(module)) or ""
    except SyntaxError:
        return ""


async def manage_tools(project, action, argument="", expected_version=None, *, root=None):
    from llm.components.tools.packages import ToolPaths, read_package
    from llm.components.tools.data import ToolData

    data = await project.components.aget("tools")
    if not isinstance(data, ToolData):
        raise ValueError("This manager requires the Python Tool package component")
    record = await project.aget_data()
    paths = ToolPaths.for_project(record)
    if action == "import":
        source = Path(argument).expanduser()
        if not source.is_absolute():
            source = Path(root or record.paths.root) / source
        # The component's public package codec rejects links, extra files and
        # invalid requirements; acreate copies through the lifecycle lock.
        package = await asyncio.to_thread(read_package, ToolPaths(source.parent), source.name)
        await data.acreate(package, identifier=source.name)
    elif action == "delete":
        current = await data.asnapshot(argument)
        if current["version"] != expected_version:
            raise ValueError("Tool changed; reload the list before deleting")
        if argument in await data.aenabled():
            await data.adisable(argument)
        await data.adelete(argument, expected_version=expected_version)
    elif action == "open":
        await data.aload(argument)
        return str(paths.package(argument))
    elif action != "list":
        raise ValueError("Unknown Tool management operation")
    packages = await data.alist()
    enabled = await data.aenabled()
    entries = []
    for name in packages:
        snapshot = await data.asnapshot(name)
        def modified(name=name):
            return max(path.stat().st_mtime for path in paths.package(name).iterdir())
        stamp = await asyncio.to_thread(modified)
        entries.append({"name": name, "description": description(snapshot["data"]["source"]),
                        "modified": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                        "enabled": name in enabled, "version": snapshot["version"]})
    return entries
