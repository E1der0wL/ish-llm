"""Process defaults scoped to a captured shell environment, never os.environ."""

from llm.components.tools.builtin.processes import ProcessTools
from llm.components.tools.builtin.process_values import ProcessInvocation


class ShellProcessTools(ProcessTools):
    def __init__(self, files, environment):
        super().__init__(files)
        self.environment = dict(environment)

    async def start(self, args):
        return await super().start(ProcessInvocation.with_environment(args, self.environment))
