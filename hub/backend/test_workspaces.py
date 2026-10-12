"""Give the model actionable feedback for malformed sandbox commands before effects."""

from pathlib import PurePosixPath
from llm.components.tools.builtin import TestWorkspaces


class HubTestWorkspaces(TestWorkspaces):
    async def __call__(self, args):
        from .tool_advice import execution_arguments
        args = execution_arguments(args)
        argv = args.get("argv")
        if (not isinstance(argv, (list, tuple)) or not argv or not argv[0]
                or any(not isinstance(part, str) or "\0" in part for part in argv)
                or not PurePosixPath(argv[0]).is_absolute()):
            return {"status": "invalid_arguments", "executed": False,
                    "error": "argv must be a nonempty list of strings with an absolute executable at argv[0].",
                    "guidance": "Correct the arguments and call test_workspace_execute again. "
                                "For a shell command use ['/bin/sh', '-c', 'your command']. "
                                "For Python use the actual absolute Python executable. No command was executed."}
        return await super().__call__(args)
