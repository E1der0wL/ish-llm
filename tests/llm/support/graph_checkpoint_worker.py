"""강제 종료된 그래프의 영속 체크포인트 복구 검사용 별도 프로세스."""

import asyncio
import os
from pathlib import Path
import sys

from llm.components.workflows import WorkflowGraph
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel


async def main(root):
    async def work(node):
        with (root / "effects.txt").open("a", encoding="utf-8") as stream:
            stream.write(node.node_id + "\n")
        if node.node_id == "second":
            os._exit(23)  # Run/Step 종료 처리 없이 실제 프로세스를 종료한다.
        return {"first_done": True}

    async with LargeLanguageModel(root, engines={"graph": GraphEngine("flow", handlers={"work": work})}) as app:
        project = await app.projects.acreate("crash", components=["workflows"])
        graph = (WorkflowGraph(entry="first").node("first", "work").node("second", "work")
                 .node("end", "end").connect("first", "second").connect("second", "end").to_dict())
        await (await project.components.aget("workflows")).acreate(graph, identifier="flow")
        session = await project.sessions.acreate()
        await (await session.run.submit("original", engine="graph")).wait()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
