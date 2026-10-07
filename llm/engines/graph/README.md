# GraphEngine — 저장한 Workflow 실행

Workflow JSON을 LangGraph 실행 그래프로 구성합니다. 분기, 병렬 합류, 반복, Agent/Tool 노드와 중첩 실행을 지원합니다. Workflow와 Agent의 정의는 Component가 저장하며, 이 폴더는 실행만 담당합니다.

GraphEngine은 durable Workflow orchestration engine입니다. 같은 handler 환경의 중첩은
`workflow` 노드로, 다른 등록 GraphEngine/handler 환경의 선택은 Graph-backed Agent로 표현합니다.
Graph Agent는 behavioral Agent가 아닙니다. purpose/engine/engine_options만 실행 정의로 받고,
completion/system_prompt/tools/resources/policy/input_schema/output_schema/output_format은
존재 자체를 거부합니다. 부모 Run의 tools/tool_scope/capabilities를 그대로 전달합니다.
Workflow 노드의 입출력 매핑/schema는 유지하지만 Graph Agent 노드의 emit_text/output_format은
거부합니다. 상세 예시는 [중첩 Workflow](../../../docs/llm/nested-workflows.md)에 있습니다.

`max_nested_depth`는 None 또는 0 이상의 정수이며 임의 최대값은 없습니다.
`cleanup_timeout`은 명시했을 때만 취소 후 대기합니다. 미설정이면 남은 작업은
기존 PendingWork가 추적하며, 완료 전 Session 보호와 Tool scope revoke는 유지합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | GraphEngine, GraphNodeContext, GraphExecutionError 공개 import입니다. |
| [engine.py](engine.py) | Workflow 컴파일, 노드 판단과 실행, 스케줄링, 이벤트·체크포인트·재개를 구현합니다. |
| [agent.py](agent.py) | AgentNode가 저장된 Agent 정의를 등록된 Engine과 연결합니다. |
| [tool.py](tool.py) | ToolNode가 Tool 호출을 공통 ToolExecutor에 연결합니다. |
| [checkpoints.py](checkpoints.py) | 중첩 Engine의 체크포인트와 승인 요청을 부모 Graph 범위에 연결합니다. |

## 함께 읽을 코드

- [Workflow Component](../../components/workflows/README.md): 노드·간선·입출력 binding의 저장과 구조 검증.
- [Agent Component](../../components/agents/README.md): 목적, Engine, 모델, 리소스와 정책 정의.
- [Graph/RAG 실행 예제](../../../examples/llm/graph_rag.md): ToolNode 검색 → 같은 evidence를 사용한 답변 검증·수정 → publish.

등록 예시는 다음과 같습니다. 실제 Workflow의 action 이름과 handler 키가 일치해야 합니다.

```python
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.engines.graph.agent import AgentNode
from llm.engines.loop import LoopEngine

loop = LoopEngine()
engines = {
    "loop": loop,
    "graph": GraphEngine(handlers={
        "tool": ToolNode(), "agent": AgentNode(engines={"loop": loop}),
    }),
}
```

백엔드에 위 `engines`를 등록한 뒤 요청마다 저장된 Workflow ID를 선택합니다.

```python
request = await session.run.submit(
    "설정 파일을 검토해줘", engine="graph", engine_options={"workflow": "review"},
)
run = await request.wait()
```

생성자는 Workflow를 받지 않습니다. 요청의 `workflow`는 필수이며 기본값이나
Project 설정으로 추측하지 않습니다. `engine_options`는 JSON 객체이고 Graph 요청은
`workflow`만 받습니다. 시간·병렬 제한 등은 Project/Session 설정 경로를 사용하며 child는 부모 제한을 넓힐 수 없습니다.
요청 선택은 QUEUED 메시지와 Run에 저장되며 등록 Engine은 변경하지 않습니다.
정의는 Run 시작 시 읽습니다. 대기 중 편집은 다음 실행에 반영되고 시작한 실행은
자신의 스냅샷을 사용합니다. 존재하지 않는 ID나 잘못된 정의는 실행 전 검증에서 실패합니다.
resume은 원본 Run의 선택값을 복원하며 다른 Workflow로 변경할 수 없습니다.

Agent의 `engine`은 `AgentNode(engines=...)`에 전달한 맵/EngineRegistry에서 찾습니다. 같은 Engine을 최상위 요청에서도 선택하려면 백엔드에도 등록합니다. Agent용 Engine은 `for_agent(definition)`으로 호출별 실행기를 반환해야 하며, LoopEngine과 GraphEngine이 이 계약을 제공합니다. Workflow 선택과 실행 설정은 [Graph 개발 문서](../../../docs/llm/graph-engine.md)에 있습니다.

## 판단과 실행의 경계

`_branch_port`, `_loop_continues`, `_plan_node`는 입력 상태에서 다음 동작만 계산합니다. 파일 저장, 승인 생성, 모델·Tool 실행을 하지 않습니다. 실행부가 계획을 받아 체크포인트 저장 확인 → Step 시작 → 처리기 실행 → 완료 체크포인트 저장 확인 → Step 완료를 수행합니다.

LangGraph 스케줄링, 병렬 합류, 취소 정리와 이벤트 ACK는 실행부가 소유합니다. 일반 실행 timeout은 명시했을 때만 적용합니다. 순수 판단 결과는 별도 영속 상태나 승인 증명이 아닙니다.

## 중단·승인·재개

실행 중 추가 지시는 `run.ainstruction_targets()`에서 확인한 소비자 ID를 사용해
`session.run.steer(run.id, text, targets=[...])`로 전달합니다. Graph는 내용을 해석하지 않고
선택한 Agent Engine으로 전달하며, Loop는 다음 Completion 경계에서 반영합니다.
중첩·병렬·반복 실행마다 대상을 구분하고 대상별 반영 상태를 저장합니다. 미시작 Agent는
`run.ainstruction_routes()`의 경로를 `session.run.reserve_instruction(..., targets=routes)`로
예약합니다. 접수 이후 다음 실행 하나에만 결합하고 현재 실행에 소급하거나 다음 반복에
중복 전달하지 않습니다. 실행되지 않으면 unapplied로 종료하며 미사용 예약은 재개에
이전하지 않습니다. [공통 전달·UI 계약](../../../docs/llm/steering.md)을 참고하세요.

추가 지시를 소비하는 Agent Engine은 `checkpoint_name`과 동기 `validate_resume`을 제공해야 합니다.
AgentNode는 `for_agent()`가 반환한 실제 Engine을 사전 탐색에서 검사하여, 계약 오류가 있을 때
앞선 Tool이나 MCP 연결이 먼저 실행되지 않도록 합니다. 미지원 Engine의 기존 계약은 유지합니다.

`pause_before`는 노드 실행 전 대기 기록을 저장합니다. 노드 확인은 Tool 실행 승인을 대신하지 않으며 ToolRuntime가 ASK를 반환하면 별도 Tool 승인이 필요합니다.

명시적 재개는 새 Run을 만듭니다. 완료 노드는 저장 결과를 재사용하고, 시작했지만 완료되지 않은 동작은 명시적 `retry_nodes` 검증을 거칩니다. 중첩 체크포인트도 원래 Interaction과 binding을 검증합니다. [재개 계약](../../../docs/llm/graph-checkpoints.md)을 참고하세요.

[상위 안내](../README.md)
