"""Project-scoped Tool management through public component CRUD and package codecs."""

from __future__ import annotations

from llm.core.implementation_parameters import ImplementationParameters

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def description(source):
    try:
        module = ast.parse(source)
        main = next((node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == "main"), None)
        return (ast.get_docstring(main) if main else ast.get_docstring(module)) or ""
    except SyntaxError:
        return ""


class ToolManagement:
    """Own one operation's Project/component handles and path/version context.

    A new instance is opened for each UI command so no Project settings or
    enabled list survives across commands. Backend component APIs keep authority.
    """

    def __init__(self, project: Any, data: Any, record: Any, paths: Any, *,
                 root: str | Path | None = None, builtin: Any = None) -> None:
        self.project = project
        self.data = data
        self.record = record
        self.paths = paths
        self.root = str(root) if isinstance(root, str) else root
        self.builtin = builtin

    @classmethod
    async def for_project(cls, project: Any, *, root: str | Path | None = None,
                          builtin: Any = None) -> "ToolManagement":
        from llm.components.tools.packages import ToolPaths
        from llm.components.tools.data import ToolData

        data = await project.components.aget("tools")
        if not isinstance(data, ToolData):
            raise ValueError("This manager requires the Python Tool package component")
        record = await project.aget_data()
        return cls(project, data, record, ToolPaths.for_project(record), root=root, builtin=builtin)

    async def list(self) -> list[dict]:
        packages = await self.data.alist()
        enabled = await self.data.aenabled()
        entries = []
        if self.builtin is not None and self.builtin.name in self.record.components:
            settings = await self.project.components.aget(self.builtin.name)
            snapshot = await settings.asnapshot()
            selected = ImplementationParameters.read(snapshot["data"]).config.get("enabled", [])
            for name in self.builtin.toolkit.registry.names():
                tool = self.builtin.toolkit.registry.get(name)
                entries.append({"name": "builtin:" + name, "label": name, "builtin": True,
                                "description": tool.description, "modified": "", "enabled": name in selected,
                                "version": snapshot["version"]})
        for name in packages:
            snapshot = await self.data.asnapshot(name)
            def modified(name=name):
                return max(path.stat().st_mtime for path in self.paths.package(name).iterdir())
            stamp = await asyncio.to_thread(modified)
            entries.append({"name": name, "description": description(snapshot["data"]["source"]),
                            "modified": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                            "enabled": name in enabled, "version": snapshot["version"]})
        return entries

    async def import_package(self, argument: str) -> list[dict]:
        from llm.components.tools.packages import read_source_package

        source = Path(argument).expanduser()
        if not source.is_absolute():
            source = Path(self.record.paths.root if self.root is None else self.root) / source
        # The component rejects links/extra files and copies under its lifecycle lock.
        package = await asyncio.to_thread(read_source_package, source)
        await self.data.acreate(package, identifier=source.name)
        return await self.list()

    async def delete(self, argument: str, expected_version: str | None) -> list[dict]:
        current = await self.data.asnapshot(argument)
        if current["version"] != expected_version:
            raise ValueError("Tool changed; reload the list before deleting")
        if argument in await self.data.aenabled():
            await self.data.adisable(argument)
        await self.data.adelete(argument, expected_version=expected_version)
        return await self.list()

    async def toggle_builtin(self, argument: str, expected_version: str | None) -> list[dict]:
        if self.builtin is None:
            raise ValueError("Built-in Tools can only be enabled or disabled")
        name = argument.removeprefix("builtin:")
        if name not in self.builtin.toolkit.registry.names():
            raise ValueError("Unknown built-in Tool")
        settings = await self.project.components.aget(self.builtin.name)
        current = await settings.asnapshot()
        if current["version"] != expected_version:
            raise ValueError("Tool settings changed; reload the list")
        values = current["data"]
        enabled = values.setdefault("config", {}).setdefault("enabled", [])
        enabled.remove(name) if name in enabled else enabled.append(name)
        await settings.aconfigure(values, expected_version=expected_version)
        return await self.list()

    async def open_path(self, argument: str) -> str:
        await self.data.aload(argument)
        return str(self.paths.package(argument))

    async def execute(self, action: str, argument: str = "", expected_version: str | None = None):
        if argument.startswith("builtin:"):
            if action != "toggle":
                raise ValueError("Built-in Tools can only be enabled or disabled")
            return await self.toggle_builtin(argument, expected_version)
        if action == "import":
            return await self.import_package(argument)
        if action == "delete":
            return await self.delete(argument, expected_version)
        if action == "open":
            return await self.open_path(argument)
        if action == "list":
            return await self.list()
        raise ValueError("Unknown Tool management operation")
