"""Give the model actionable feedback for malformed sandbox commands before effects."""

from dataclasses import asdict, dataclass
from pathlib import Path
from llm.components.tools.builtin import TestWorkspaces
from llm.components.tools.builtin.workspace_values import WorkspaceExecuteArguments


@dataclass(frozen=True, slots=True)
class InvalidSandboxArguments:
    status: str = "invalid_arguments"
    executed: bool = False
    error: str = "argv must be a nonempty list of strings with an absolute executable at argv[0]."
    guidance: str = ("Correct the arguments and call test_workspace_execute again. "
                     "For a shell command use ['/bin/sh', '-c', 'your command']. "
                     "For Python use the actual absolute Python executable. No command was executed.")

    def to_dict(self):
        return asdict(self)


class HubTestWorkspaces(TestWorkspaces):

    async def create(self, args):
        # The sandbox source selection still uses root-relative paths. Normal
        # file Tools now delegate absolute paths and read grants to the backend.
        values = dict(args)
        if 'source_paths' in values:
            paths = []
            for value in values['source_paths']:
                path = Path(value)
                if path.is_absolute():
                    path = path.relative_to(self.source.root)
                paths.append(str(path))
            values['source_paths'] = paths
        return await super().create(values)

    async def __call__(self, args):
        from .advice import execution_arguments
        args = execution_arguments(args)
        if not WorkspaceExecuteArguments.valid_command(args):
            return InvalidSandboxArguments().to_dict()
        return await super().__call__(args)
