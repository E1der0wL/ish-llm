# Workflows — 실행 그래프 정의 저장

UI가 조립한 노드·간선·입출력 mapping을 JSON으로 저장하고 구조를 검증합니다. 이 폴더의 WorkflowGraph는 데이터 빌더이며 실제 실행기는 GraphEngine입니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | WorkflowComponent, WorkflowData, WorkflowGraph 공개 import입니다. |
| [component.py](component.py) | Workflow 정의의 Component 저장·조회와 검증 연결입니다. |
| [data.py](data.py) | 저장된 그래프의 검증·빌더 복원을 제공하는 WorkflowData입니다. |
| [graph.py](graph.py) | schema_version=1 그래프 빌더와 분기·병렬 합류·bounded loop 구조 검증입니다. |
| [bindings.py](bindings.py) | JSON pointer를 사용하는 입력·출력 binding과 schema 검증 계약입니다. |

## 가장 작은 그래프

```python
from llm.components.workflows import WorkflowGraph

graph = WorkflowGraph(entry="finish").node("finish", "end").to_dict()
# 열린 Project에서:
# workflows = await project.components.aget("workflows")
# await workflows.acreate(graph, identifier="empty")
```

실제 작업에는 action 노드를 추가하고 GraphEngine의 handler와 연결합니다. Agent 노드는 저장된 Agent ID를 참조할 수 있습니다. 입력은 `inputs`, 출력은 `outputs`의 JSON pointer로 공유 상태와 연결합니다.

## 저장·실행 계약

최상위 및 제어 노드는 알려진 필드만 허용합니다. UI 라벨 등은 `metadata` 안에 저장합니다.
action 노드의 `type`은 선택 handler를 식별합니다. 공통 binding/timeout 필드 이외의 설정은
handler의 `configuration_schema()` 또는 기존 `validate(node, context)`가 검증합니다.
GraphEngine은 handler 내부 옵션을 복제하거나 해석하지 않습니다. schema/validator가 없는
단순 함수 handler에는 공통 필드만 전달할 수 있습니다. AgentNode와 ToolNode는 닫힌 schema를 제공합니다.
[handler 확장 계약과 예](../../../docs/llm/schema-ownership.md)를 참고하세요.

- 정의는 `<project>/workflows/records/<id>.json`에 저장합니다. 함수·client·asyncio 객체는 저장하지 않습니다.
- `branch`는 조건별 port, `parallel`은 지정된 `join`, `loop`는 body와 명시적 max_iterations/on_limit을 사용합니다.
- 임의의 순환 간선은 거부합니다. 반복은 bounded loop 노드로 표현합니다.
- 현재 몇 번째 노드가 실행 중인지는 정의 파일에 쓰지 않습니다. 실행별 진행은 Run의 Step과 체크포인트에 저장합니다.
- `agraph(id)`로 받은 빌더는 사본입니다. 수정 후 `asave(id, graph.to_dict())`로 명시적으로 저장합니다.

[GraphEngine 안내](../../engines/graph/README.md), [입출력·검증 예제](../../../examples/llm/graph_rag.md)를 참고하세요.

[상위 안내](../README.md)
