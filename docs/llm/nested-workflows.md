# 중첩 Workflow와 Graph Agent

하위 Workflow는 부모 Run 안에서 LangGraph로 실행한다. Session/Run을 새로 만들거나 자식
Conversation을 저장하지 않는다. 최상위 GraphEngine이 이벤트 큐와 체크포인트 수명주기를
소유하고, 자식은 같은 이벤트 저장 경로와 실행 제한을 사용한다.

## Workflow가 다른 Workflow를 호출하기

`workflow`는 내장 노드 타입이다. `workflow` 필드에는 같은 Project에 저장된 정의 ID를 넣는다.
사용자가 사전에 정할 필수 항목은 호출할 ID와 전달할 입력/반환할 출력이다.
`inputs`/`outputs`를 생략하면 객체 전체를 전달/병합하므로 UI에서는 명시적 매핑을 권장한다.

```python
from llm.components.workflows import WorkflowGraph
from llm.engines.graph.agent import AgentNode
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine

agents = AgentNode(engines={"loop": LoopEngine()})
engine = GraphEngine(handlers={"agent": agents}, max_nested_depth=16)
# engine을 LargeLanguageModel(..., engines={"graph": engine})에 등록한다.
# Project에서 workflows/agents/tools를 선택하고 writer Agent를 별도로 저장한다.

child = (WorkflowGraph(entry="write", inputs={"request": "/request"},
                       outputs={"answer": "/answer"})
    .node("write", "agent", agent="writer", inputs={"request": "/request"},
          outputs={"answer": "/text"})
    .node("end", "end").connect("write", "end"))

parent = (WorkflowGraph(entry="delegate", inputs={"request": "/prompt"},
                        outputs={"answer": "/answer"})
    .node("delegate", "workflow", workflow="write_flow", inputs={"request": "/request"},
          outputs={"answer": "/answer"})
    .node("end", "end").connect("delegate", "end"))

workflows = await project.components.aget("workflows")
await workflows.acreate(child.to_dict(), identifier="write_flow")
await workflows.acreate(parent.to_dict(), identifier="main")
run = await (await session.run.submit("문서를 바탕으로 코드를 작성해줘", engine="graph", engine_options={"workflow": "main"})).wait()
steps = await run.steps.alist()
output = (await run.aresult()).output.data
```

직접 참조 노드는 부모 엔진의 handlers를 공유한다. 자식 입력은 부모 노드의 inputs 객체다.
자식 Workflow의 inputs 매핑이 있으면 그 객체에 매핑을 적용하고 initial_state에 합친다.
최상위 Workflow inputs의 원본은 여전히 `{prompt, message_id}`다. 자식 출력은 자식
Workflow의 outputs/schema 처리 후 부모 노드의 outputs/schema를 거쳐 병합한다.

등록되지 않은 처리기, 누락된 Workflow, 순환 참조, 깊이 초과는 실행 전 실패한다.
참조 목록 전체를 검사하므로 실행하지 않을 분기도 유효한 정의여야 한다. 같은 Workflow를
다른 노드/병렬 분기/유한 loop에서 여러 번 사용하는 것은 허용한다.

## Agent가 GraphEngine을 선택하기

하위 그래프에 다른 처리기 구성을 사용하려면 GraphEngine을 AgentNode에 등록한다.

```python
leaf_agents = AgentNode(engines={"loop": LoopEngine()})
review_engine = GraphEngine(handlers={"agent": leaf_agents})
coordinators = AgentNode(engines={"review_graph": review_engine})
root_engine = GraphEngine(handlers={"agent": coordinators})

await project.components.agents.acreate({
    "purpose": "검토 Workflow 수행",
    "engine": "review_graph",
    "engine_options": {"workflow": "review_flow", "max_steps": 100},
    "tools": [],
    "policy": {"timeout_seconds": 120},
}, identifier="reviewer")
# main의 노드는 type='agent', agent='reviewer'로 작성한다.
# 입력은 일반 Agent와 같고 Workflow 반환값은 결과의 data에 담긴다.
# 예: outputs={"answer": "/data/answer"}
```

`engine_options`는 workflow/max_steps/max_parallelism/timeout_seconds/max_nested_depth를
받는다. `workflow`는 반드시 명시하며 부모 요청이나 등록 객체에서 추측하지 않는다.
나머지 실행 설정은 기존 Project/Session/Agent/host 순서로 해석한다.
등록 객체를 변경하지 않고 호출별 복사본을 만든다.
Graph Agent는 조율 단위다. completion/system_prompt 및 resources.skills/mcp는 이 Agent에
설정하지 않고 실제 모델/연결을 사용하는 실행 노드에 둔다. 해당 필드를 Graph Agent에 넣으면
조용히 무시하지 않고 오류로 알린다. resources.rag는 Project의 검색 Tool 허용에 사용할 수 있다.

Graph Agent의 tools는 하위 작업에도 적용되는 상한이다. 자식 Agent가 더 많은 Tool을
선택해도 권한이 확대되지 않는다. 현재 Graph Agent의 허용 목록은 Project에 구성된 Tool을
선택하므로 동적으로 발견하는 MCP Tool을 Graph Agent 경계 밖에서 허용하는 기능은 없다.
MCP Agent를 포함한 Workflow를 재사용할 때는 직접 workflow 노드로 호출할 수 있다.
임의 Tool은 공통 ToolExecutor를 통해야 승인/한도/원장이 적용된다.

## 실행 제한과 중단

- max_nested_depth 기본값은 16, 허용 범위는 0~32다. 0이면 하위 Workflow를 거부한다.
- max_steps에는 자식의 제어/작업 노드도 포함한다. 부모와 자식의 제한을 모두 적용한다.
- 실제 작업 처리기는 조상 순서로 실행 슬롯을 얻는다. workflow/Graph Agent 조율 노드는
  슬롯을 차지하지 않으므로 max_parallelism=1에서도 자식을 기다리며 교착되지 않는다.
- 부모 실행 시간, 호출 노드 timeout, 자식 Graph timeout, Agent policy가 함께 적용된다.
- ToolPolicy와 작업 원장은 같은 Run에서 공유한다. Agent 정책은 추가 제약이다.
- 중단/시간 초과는 실행 중 자식까지 전달하고 정리를 기다린다. 대기 중인 다음 요청은 보존한다.

명시적 resume은 새 Run이다. Run 단위 시간/호출 한도는 새 시도의 한도이며, Graph Agent의
Tool 시도/성공 횟수는 체크포인트에서 복원하여 이미 수행한 업무가 Agent 한도에 반영되게 한다.
승인된 Tool은 효과 실행 전 Agent 사용량도 저장한다. 재시도를 승인해도 이 한도는 초기화되지 않는다.
예산 소진 시 정의를 바꾸고 기존 체크포인트를 이어 붙이는 대신 새 요청으로 시작한다.

## 체크포인트와 UI

저장 위치는 기존 `Run.state/checkpoints/graph` 하나다. 자식마다 체크포인트 파일 집합이나
별도 Run을 만들지 않는다. 실행 키 예시는 다음과 같다.

```json
["delegate", "workflow", "write_flow", "write"]
```

병렬 분기와 반복 회차도 키에 포함한다. UI는 키를 직접 조립하지 않고 조회 결과를 사용한다.
자식 그래프 Step은 kind=subgraph이며 parent_step_id/workflow_id/path로 연결한다.
작업 Step에는 node_checkpoint_key/node_path, Agent 자식에는 agent_step_id도 남는다.
일반 LLM/Tool 사용량은 부모 Run에 계속 집계된다.

자식의 pause_before는 전체 Run을 PAUSED로 끝내고 Session을 반환한다. 재개는 동일한
`session.run.resume(run.id, engine="graph", retry_nodes=...)` API를 사용한다.
완료된 자식 노드는 출력만 재사용하고, 미완료 wrapper는 자식 상태를 복구한다.
체크포인트의 container=true는 엔진이 계산한 조율 노드 표식이며 재시도 승인 대상에서 제외한다.
커스텀 처리기의 graph_engine 선언만으로는 이 표식을 부여하지 않는다. 외부 효과를 전부
자식 노드로 옮긴 순수 조율 처리기만 resumable_container=True를 선언할 수 있다.
그 외 처리기는 자식을 호출했더라도 미완료 상태에서 명시적 재시도 승인이 필요하다.

```python
snapshot = await run.acheckpoint()
controls = {"branch", "parallel", "join", "loop", "end"}
uncertain = {
    key: record for key, record in snapshot["records"].items()
    if record["status"] == "started"
    and record["node_type"] not in controls
    and not record.get("container", False)
}
# UI에서 uncertain 작업의 외부 효과를 확인하고 승인받은 키만 retry_nodes에 전달한다.
```

정의·설정·어댑터 revision 검사는 참조한 자식 전체에 적용한다. 변경한 자식 정의를 이전
완료 결과에 섞어 재개하지 않는다. 효과 직후 프로세스가 종료된 작업은 여전히 불확실하며
명시적으로 재실행하면 그 효과가 중복될 수 있다. 완료 체크포인트가 있는 앞선 노드는 재실행하지 않는다.
LangGraph 네이티브 interrupt(), Python 실행 프레임 저장, 자동 재시도는 추가하지 않는다.
