"""Builtin path argument projections; the caller owns conversion and validation remains with admission."""

from .file_values import FilePathArguments, FileMoveArguments
from .process_values import ProcessInvocation, ProcessStartArguments, ShellArguments
from .workspace_values import WorkspaceSelection, WorkspaceCreateArguments


class BuiltinPathArguments:
    FILE_PREFIX = "file_"
    PROCESS_NAMES = (ProcessStartArguments.NAME, ShellArguments.NAME)
    PROCESS_FIELDS = (ProcessInvocation.cwd.__name__,)
    FILE_FIELDS = (
        FilePathArguments.path.__name__,
        FileMoveArguments.destination.__name__,
    )

    @classmethod
    def adapts(cls, name):
        return name.startswith(cls.FILE_PREFIX) or name in (
            *cls.PROCESS_NAMES,
            WorkspaceCreateArguments.NAME,
        )

    @classmethod
    def remap(cls, name, arguments, convert):
        values = dict(arguments)
        if not WorkspaceSelection.supplied(values):
            names = (
                cls.PROCESS_FIELDS
                if name in cls.PROCESS_NAMES
                else cls.FILE_FIELDS if name.startswith(cls.FILE_PREFIX) else ()
            )
            for key in names:
                if key in values:
                    values[key] = convert(values[key])
            key = WorkspaceCreateArguments.source_paths.__name__
            if name == WorkspaceCreateArguments.NAME and isinstance(
                values.get(key), list
            ):
                values[key] = [convert(value) for value in values[key]]
        return values
