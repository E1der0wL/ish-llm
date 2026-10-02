"""실제 외부 효과 직후 프로세스를 종료하여 불확실한 작업 원장을 남긴다."""
import asyncio
import os
from pathlib import Path
import sys

from tests.llm.support.runtime_tools import RuntimeTools
from llm.components.tools import Tool, ToolComponent, ToolRegistry
from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.runtime.tools import ToolPolicy
from tests.llm.test_isolated_tools import ToolEngine


async def main():
    root, project_id, session_id = sys.argv[1:]
    async def effect(arguments):
        with (Path(root) / "external-effect.txt").open("ab") as stream:
            stream.write(b"effect\n")
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(23)
    tools = RuntimeTools(ToolRegistry([Tool("external", "external", {"type": "object"}, effect)]))
    async with LargeLanguageModel(root, components=[tools], engines={"tool": ToolEngine()},
            services=ServiceConfig(tool_policy=ToolPolicy(operation_key=lambda call: call.arguments["operation"]))) as app:
        session = await (await app.projects.aload(project_id)).sessions.aload(session_id)
        await (await session.run.submit("perform", engine="tool")).wait()


if __name__ == "__main__":
    asyncio.run(main())
