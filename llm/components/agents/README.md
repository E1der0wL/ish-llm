# Agents — 재사용할 업무 정의

특정 목적의 업무를 Engine, 모델, 프롬프트, 리소스, 입출력 계약의 조합으로 저장합니다. 실행은 Graph의 AgentNode가 담당하고 실행 상태는 소유 Run/Step에 남습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | AgentComponent와 공개 데이터 핸들을 노출합니다. |
| [component.py](component.py) | Agent JSON schema, 필수 purpose/engine, 참조·정책 검증과 정의 지문을 계산합니다. |
| [data.py](data.py) | AgentData가 잠금·버전 검사를 유지하며 프롬프트 변경 등의 전용 API를 제공합니다. |

## 정의 저장 예

선택된 `agents` Component 핸들에 저장합니다. `loop`는 AgentNode에 등록된 Engine 이름이어야 합니다.

```python
agents = await project.components.aget("agents")
await agents.acreate({
    "purpose": "주어진 문서의 오류를 검토한다",
    "engine": "loop",
    "system_prompt": "근거가 있는 문제만 설명하세요.",
    "engine_options": {'policy': {'max_iterations': 3}},
    "output_format": "text",
}, identifier="reviewer")
```

`completion`은 모델 옵션, `tools`는 허용 Tool 이름, `resources`는 Skill/RAG/MCP 연결입니다. `policy`로 require_tool/max_tool_calls/timeout_seconds를 명시할 수 있습니다. JSON 결과 검증에는 output_format과 output_schema를 사용합니다.

`resources.prompt="saved-prompt-id"`로 선택한 Prompt를 참조할 수 있습니다.
모델 실행 Agent의 조합 순서는 **참조 Prompt → 명시 inline/상속 system_prompt → resources.skills 순서**입니다.
Prompt messages의 role은 지침 안의 라벨로 보존합니다. purpose는 업무 메타데이터이며
자동 시스템 프롬프트 대체값이 아닙니다. host의 고정 system_prompt는 기존 우선순위를 유지합니다.
Graph를 실행하는 조율 Agent는 하위 Agent의 지침을 덮어쓰지 않습니다.
Graph-backed Agent는 다른 GraphEngine/handler 환경의 선택만 소유합니다. 위 모델 설정은
behavioral Agent에만 해당합니다. Graph Agent는 purpose/description/engine/engine_options와 비실행
metadata만 허용하고 completion/system_prompt/tools/resources/policy/input_schema/
output_schema/output_format은 빈 값도 거부합니다. Component가 Engine 이름을 검사하지 않고
GraphEngine.for_agent가 리소스 조회 전에 검증합니다. 같은 환경은 workflow 노드로 중첩합니다.
Agent·Prompt·Skill의 임의 Application 필드는 `metadata` 안에서만 허용하며 Tool/MCP/RAG 권한이나 Engine 설정으로 해석하지 않습니다.
Run의 capability 사본과 Agent Step binding에 정의와 revision을 기록합니다.
실행 중 저장소 수정은 다음 Run에만 반영되며, 바뀐 정의로 과거 checkpoint를 재개하지 않습니다.

저장 위치는 `<project>/agents/records/<id>.json`입니다. 레코드를 저장한다고 실행되지는 않습니다. Workflow 노드에서 Agent ID를 참조하고 [AgentNode](../../engines/graph/README.md)가 등록된 Engine을 실행합니다. 중첩 Graph도 같은 소유 Run 안에서 실행됩니다.

Agent의 Tool 허용 목록은 Run의 ToolPolicy나 승인 검사를 우회하지 않습니다. 정의·리소스 변경은 재개 binding에 영향을 줄 수 있습니다. [Agent 상세 계약](../../../docs/llm/agents.md)을 참고하세요.

[상위 안내](../README.md)
