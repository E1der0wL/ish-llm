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
engine = GraphEngine(handlers={"agent": agents})
# 깊이 제한이 필요하면 Project parameters.engines.graph.policy.max_nested_depth에 명시한다.
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
    "engine_options": {'policy': {'max_steps': 100}, 'workflow': 'review_flow'},
}, identifier="reviewer")
# main의 노드는 type='agent', agent='reviewer'로 작성한다.
# 입력은 일반 Agent와 같고 Workflow 반환값은 결과의 data에 담긴다.
# 예: outputs={"answer": "/data/answer"}
```

`engine_options`는 workflow, config.buffer_size/cleanup_timeout,
policy.max_steps/max_parallelism/timeout_seconds/max_nested_depth를 받는다.
GraphEngine의 SettingsLayout을 그대로 사용하며 별도 schema를 복제하지 않는다.
`workflow`는 반드시 명시하며 부모 요청이나 등록 객체에서 추측하지 않는다.
나머지 실행 설정은 Project/Session/Agent 순서로 해석하며 child가 부모 한계를 확대하지 못한다.
Host 생성자는 handler environment와 기술 identity만 제공한다.
등록 객체를 변경하지 않고 호출별 복사본을 만든다.
Graph Agent는 behavioral Agent가 아닌 **다른 GraphEngine/handler environment의 이름**이다.
같은 환경에서 중첩하려면 workflow 노드를 사용한다. Graph Agent 정의에는 purpose, description, engine,
engine_options와 비실행 metadata만 둔다. completion/system_prompt/tools/resources/policy/
input_schema/output_schema/output_format은 빈 객체나 null이어도 존재 자체가 오류다.
검증은 Prompt/Skill/MCP/RAG 조회 전에 수행한다. Workflow Agent-node의 inputs/outputs/
input_schema/output_schema는 유지하고 emit_text/output_format은 거부한다.
부모의 tools/tool_scope/capabilities를 그대로 전달하며 추가 allow-list나 Agent 예산을 만들지 않는다.
RAG는 내부의 Loop Agent resources.rag 또는 rag_search Tool 노드로 선언한다.
임의 Tool은 공통 ToolExecutor를 통해야 승인/한도/원장/인자 제약이 적용된다.

## 실행 제한과 중단

- max_nested_depth는 미설정/None이면 추가 제한이 없다. 0 이상의 정수를 허용하고 0은 중첩을 거부한다.
- max_steps에는 자식의 제어/작업 노드도 포함한다. 부모와 자식의 제한을 모두 적용한다.
- 실제 작업 처리기는 조상 순서로 실행 슬롯을 얻는다. workflow/Graph Agent 조율 노드는
  슬롯을 차지하지 않으므로 max_parallelism=1에서도 자식을 기다리며 교착되지 않는다.
- 부모 실행 시간, 호출 노드 timeout, 자식 Graph timeout은 명시했을 때 함께 적용된다.
- ToolPolicy와 작업 원장은 같은 Run에서 공유한다. behavioral Agent만 추가 Agent 정책을 적용한다.
- 중단/시간 초과는 실행 중 자식까지 전달한다. cleanup_timeout 명시 시 그 기간을 기다리며,
  미설정이면 남은 정리를 기존 PendingWork로 즉시 넘긴다. Tool scope를 먼저 revoke하며
  정리 완료 전 Session 소유권과 후속 실행 보호를 유지한다. 다음 queued 요청은 보존한다.

명시적 resume은 새 Run이다. Run 단위 시간/호출 한도는 새 시도의 한도다. Graph Agent에는
별도 Tool 사용량/예산이 없다. behavioral Loop Agent의 사용량은 기존대로 체크포인트에서
복원한다. 완료 노드/Tool receipt는 재사용하고 불확실한 작업은 명시한 retry_nodes만 실행한다.
예산/인자 제약 등 binding을 바꾸고 기존 체크포인트를 이어 붙일 수 없다.

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
