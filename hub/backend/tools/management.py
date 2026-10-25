"""Project-scoped Tool management through public component CRUD and package codecs."""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm.core.editable_snapshot import EditableSnapshot

from llm.core.implementation_parameters import ImplementationParameters
from llm.components.tools.package_values import ToolPackageSource
from llm.components.tools.selection import ToolSelection
from llm.components.tools.packages import ToolPaths
from ..project.management_values import ManagementAction, ManagementEntry


class ToolManagementContract:
    ENTRY_POINT = "main"
    EMPTY = ""
    ABSENT = None
    WRONG_COMPONENT = "This manager requires the Python Tool package component"
    CHANGED = "Tool changed; reload the list before deleting"
    ONLY_TOGGLE = "Built-in Tools can only be enabled or disabled"
    UNKNOWN_BUILTIN = "Unknown built-in Tool"
    SETTINGS_CHANGED = "Tool settings changed; reload the list"
    UNKNOWN_OPERATION = "Unknown Tool management operation"


def description(source):
    try:
        module = ast.parse(source)
        main = next(
            (
                node
                for node in module.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == ToolManagementContract.ENTRY_POINT
            ),
            ToolManagementContract.ABSENT,
        )
        return (
            ast.get_docstring(main) if main else ast.get_docstring(module)
        ) or ToolManagementContract.EMPTY
    except SyntaxError:
        return ToolManagementContract.EMPTY


class ToolManagement:
    """Own one operation's Project/component handles and path/version context.

    A new instance is opened for each UI command so no Project settings or
    enabled list survives across commands. Backend component APIs keep authority.
    """

    def __init__(
        self,
        project: Any,
        data: Any,
        record: Any,
        paths: Any,
        *,
        root: str | Path | None = ToolManagementContract.ABSENT,
        builtin: Any = ToolManagementContract.ABSENT,
    ) -> None:
        self.project = project
        self.data = data
        self.record = record
        self.paths = paths
        self.root = str(root) if isinstance(root, str) else root
        self.builtin = builtin

    @classmethod
    async def for_project(
        cls,
        project: Any,
        *,
        root: str | Path | None = ToolManagementContract.ABSENT,
        builtin: Any = ToolManagementContract.ABSENT,
    ) -> "ToolManagement":
        from llm.components.tools.data import ToolData

        data = await project.components.aget(ToolPaths.COMPONENT)
        if not isinstance(data, ToolData):
            raise ValueError(ToolManagementContract.WRONG_COMPONENT)
        record = await project.aget_data()
        return cls(
            project,
            data,
            record,
            ToolPaths.for_project(record),
            root=root,
            builtin=builtin,
        )

    async def list(self) -> list[dict]:
        packages = await self.data.alist()
        enabled = await self.data.aenabled()
        entries = []
        if (
            self.builtin is not ToolManagementContract.ABSENT
            and self.builtin.name in self.record.components
        ):
            settings = await self.project.components.aget(self.builtin.name)
            snapshot = EditableSnapshot.read(await settings.asnapshot())
            selected = ToolSelection.read(
                ImplementationParameters.read(snapshot.data).config,
                scope=self.builtin.name,
            ).names
            for name in self.builtin.toolkit.registry.names():
                tool = self.builtin.toolkit.registry.get(name)
                entries.append(
                    ManagementEntry.builtin_tool(
                        name, tool.description, name in selected, snapshot.version
                    )
                )
        for name in packages:
            snapshot = EditableSnapshot.read(await self.data.asnapshot(name))
            stamp = await asyncio.to_thread(self.paths.modified_at, name)
            entries.append(
                ManagementEntry.tool(
                    name,
                    description(ToolPackageSource.read(snapshot.data).source),
                    datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    name in enabled,
                    snapshot.version,
                )
            )
        return entries

    async def import_package(self, argument: str) -> list[dict]:
        from llm.components.tools.packages import read_source_package

        source = Path(argument).expanduser()
        if not source.is_absolute():
            source = (
                Path(
                    self.record.paths.root
                    if self.root is ToolManagementContract.ABSENT
                    else self.root
                )
                / source
            )
        # The component rejects links/extra files and copies under its lifecycle lock.
        package = await asyncio.to_thread(read_source_package, source)
        await self.data.acreate(package, identifier=source.name)
        return await self.list()

    async def delete(self, argument: str, expected_version: str | None) -> list[dict]:
        current = EditableSnapshot.read(await self.data.asnapshot(argument))
        if current.version != expected_version:
            raise ValueError(ToolManagementContract.CHANGED)
        if argument in await self.data.aenabled():
            await self.data.adisable(argument)
        await self.data.adelete(argument, expected_version=expected_version)
        return await self.list()

    async def toggle_builtin(
        self, argument: str, expected_version: str | None
    ) -> list[dict]:
        if self.builtin is ToolManagementContract.ABSENT:
            raise ValueError(ToolManagementContract.ONLY_TOGGLE)
        name = argument.removeprefix(ManagementEntry.BUILTIN_PREFIX)
        if name not in self.builtin.toolkit.registry.names():
            raise ValueError(ToolManagementContract.UNKNOWN_BUILTIN)
        settings = await self.project.components.aget(self.builtin.name)
        current = EditableSnapshot.read(await settings.asnapshot())
        if current.version != expected_version:
            raise ValueError(ToolManagementContract.SETTINGS_CHANGED)
        parameters = ImplementationParameters.read(current.data)
        selection = ToolSelection.read(parameters.config, scope=self.builtin.name)
        selection = (
            selection.remove((name,), scope=self.builtin.name)
            if name in selection.names
            else selection.add((name,), scope=self.builtin.name)
        )
        await settings.aconfigure(
            parameters.with_config(selection.to_dict()).to_dict(),
            expected_version=expected_version,
        )
        return await self.list()

    async def open_path(self, argument: str) -> str:
        await self.data.aload(argument)
        return str(self.paths.package(argument))

    async def execute(
        self,
        action: str,
        argument: str = ToolManagementContract.EMPTY,
        expected_version: str | None = ToolManagementContract.ABSENT,
    ):
        from ..progress import OperationProgress
        OperationProgress.report(OperationProgress.management(action))
        if argument.startswith(ManagementEntry.BUILTIN_PREFIX):
            if action != ManagementAction.TOGGLE:
                raise ValueError(ToolManagementContract.ONLY_TOGGLE)
            return await self.toggle_builtin(argument, expected_version)
        if action == ManagementAction.IMPORT:
            return await self.import_package(argument)
        if action == ManagementAction.DELETE:
            return await self.delete(argument, expected_version)
        if action == ManagementAction.OPEN:
            return await self.open_path(argument)
        if action == ManagementAction.LIST:
            return await self.list()
        raise ValueError(ToolManagementContract.UNKNOWN_OPERATION)
