# 독립 Agent 도메인

Agent는 `project.agents`가 소유한다. 현재 구현은 [정의·필수 저장 envelope](../../core/agents.py),
[Repository·Manager·Handle](../../services/lifecycle/agents.py),
[Root 실행 binding](../../services/runtime/agents.py),
[Agent 실행](../../engines/agents.py), [agent_run Tool](../../engines/agent_tools.py)에 있다.
AgentComponent와 `project.components.agents` API는 제공하지 않는다.

## 저장과 명시적 선택

```python
agents = project.agents
await agents.acreate({
    "engine": "loop",
    "purpose": "주어진 문서의 오류를 검토한다",
    "system_prompt": "근거가 있는 문제만 설명하세요.",
    "engine_options": {"policy": {"max_iterations": 3}},
    "output_format": "text",
}, identifier="reviewer")
request = await session.run.submit("문서를 검토하세요.", recipients=["reviewer"])
```

선택 Engine은 Backend에 명시적으로 등록해야 한다. 저장 시 공통 정의와 선택 Engine의
`for_agent` 계약을 검증하며 모델·Tool·서버 실행을 시작하지 않는다. engine은 필수이고 purpose는
선택 메타데이터다. completion, tools, resources, policy의 생략·null·명시 값은 해당 소유자의
계약을 따른다. Backend는 Engine 이름에서 Agent를 자동 생성하지 않는다.

## 수정·삭제와 무결성

```python
before = await agents.asnapshot("reviewer")
await agents.aupdate("reviewer", {"description": "검토 담당"},
                     expected_revision=before["revision"])
```

수정·삭제에는 현재 snapshot의 expected_revision이 필수다. Project의 `agents/records`와
`agents/records.manifest.json`은 필수 저장 증거이며 Project ID·revision·실제 bytes digest와
목록을 함께 검증한다. 소실·손상을 빈 목록이나 기본값으로 복원하지 않는다.

저장 Agent가 참조하는 Skill·Prompt·MCP·RAG·Workflow 등의 삭제와 Component 선택 해제는
참조 계약을 따른다. 먼저 Agent 정의에서 참조를 명시적으로 해제해야 한다. Root Run·대기 요청·
Checkpoint가 참조하는 Agent의 삭제도 보호한다. Clone은 정의를 복제하고 새 Project identity를 기록한다.

## 실행과 재개

Run의 agent_id, 선택 Engine, 정의·revision binding은 필수 불변 증거다. 재개는 원본 요청·실행
binding·Checkpoint와 현재 선택 정의를 검증한다. 필수 증거가 없으면 현재 설정으로 보완하지 않는다.

Behavioral Agent의 지침은 참조 Prompt → Agent의 명시적 system_prompt → 선택 Skill 순서로 구성한다.
Loop의 config.system_prompt나 Project/Session Engine 설정에서 지침을 상속하지 않는다.
purpose를 시스템 프롬프트로 대체하지 않는다. 설정은 Project → Session → Agent 순서다.
Workflow Agent는 선택 Graph 소유자의 실행 계약을 사용한다.

Graph AgentNode와 agent_run은 같은 Run의 자식 Step·승인·효과 원장·누적 예산을 공유한다.
자식은 부모 권한·한도를 넓힐 수 없고 승인 응답은 실행을 뜻하지 않는다. 실제 재개를 명시해야 한다.
상세 계약은 [Agent 안내](../../../docs/llm/agents.md),
[현재 B/C 설계](../../../docs/llm/clean-refactor.md#bc-agent와-단일-실행-binding),
[데이터 수명](../../../docs/llm/data-lifecycle.md)을 따른다.
