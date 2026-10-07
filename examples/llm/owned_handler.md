# 선택 handler가 자신의 옵션을 검증하는 Workflow

아래 코드는 열린 `backend`에 `GraphEngine(handlers={"review": ReviewNode()})`가 등록되어
있을 때 사용할 정의 예시다. 실제 개발에서는 handler를 백엔드 생성자에 등록한다.
공통 입력·출력·시간 제한은 Graph 계약이고, `review_mode`는 ReviewNode만 해석한다.

```python
from llm.core.schema import object_schema
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph
from llm.llm import LargeLanguageModel

class ReviewNode:
    @staticmethod
    def describe_config():
        return object_schema({"review_mode": {"enum": ["syntax", "logic"]}},
                             required=["review_mode"])

    async def __call__(self, node):
        return {"selected": node.definition["review_mode"]}

async def run(workspace):
    async with LargeLanguageModel(workspace, engines={
        "graph": GraphEngine(handlers={"review": ReviewNode()})
    }) as backend:
        project = await backend.projects.acreate("Review", components=["workflows"])
        graph = (WorkflowGraph(entry="review", metadata={"ui": {"label": "Review"}})
                 .node("review", "review", review_mode="syntax")
                 .node("done", "end").connect("review", "done").to_dict())
        await project.components.workflows.acreate(graph, identifier="review")
        session = await project.sessions.acreate()
        handle = await session.run.submit("Review", engine="graph",
                                         engine_options={"workflow": "review"})
        return await handle.wait()
```

`review_mode="unknown"`이나 `future_option=True`는 실행 준비에서 거부되며 handler를 실행하지 않는다.
Application 표시는 `metadata` 안에 둔다. `metadata.engine`이나 `metadata.tools`를 넣더라도
Engine 선택이나 Tool 권한으로 해석하지 않는다. 새 옵션은 handler schema/구현에 추가하며
GraphEngine에 옵션별 분기를 추가하지 않는다. 실행 재개는 기존 definition binding을 따른다.
