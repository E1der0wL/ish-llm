"""Process defaults scoped to a captured shell environment, never os.environ."""

from llm.components.tools.builtin.processes import ProcessTools


class ShellProcessTools(ProcessTools):
    def __init__(self, files, environment):
        super().__init__(files)
        self.environment = dict(environment)

    async def start(self, args):
        return await super().start({"env": dict(self.environment), **args})
