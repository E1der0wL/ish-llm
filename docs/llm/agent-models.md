# Agent 모델·지침과 Loop 실행 설정

Agent는 모델을 선택하고 LoopEngine은 그 모델을 반복 호출하는 방법을 소유한다.
Agent의 `completion`은 선택 Adapter가 검증하는 SDK 인자다. Backend가 모델이나 인증값,
temperature, reasoning_effort를 대신 채우지 않는다.

```python
project = await backend.projects.acreate(config={
    "parameters": {"engines": {"loop": {
        "policy": {"max_iterations": 4, "request_timeout": 300}
    }}}
})
await project.agents.acreate({
    "engine": "loop",
    "completion": {"model": "openai/YOUR_MODEL", "api_base": "https://your-server/v1",
                   "api_key": "YOUR_API_KEY", "temperature": 0.2},
}, identifier="assistant")
```

위 값은 호출자가 고른 예이며 Backend 기본값이 아니다.

| 항목 | 소유자·설정 위치 |
|---|---|
| 모델명, 주소, 인증, temperature, reasoning_effort, SDK timeout/retry | Agent `completion`; 선택 Adapter가 검증·전달 |
| 시스템 지침과 Prompt·Skill 선택 | Agent `system_prompt`와 `resources`; Loop가 구성된 지침을 실행 입력으로 사용 |
| 스트리밍 큐 크기 | Loop `config.buffer_size` |
| 반복 횟수, Tool/Loop wrapper deadline, 출력/Tool 한도 | Loop `policy` |
| 입력 예산·턴 선택 | Loop `policy.completion`의 CompletionPolicy |
| 외부 재시도·wall deadline | Loop `policy.provider` |
| 스트림·Tool 프로토콜 | Loop와 Adapter의 기존 강제 계약 |
| RAG/Memory/Vision/요약 모델 | 각 Component의 기존 모델 설정 |

실행 설정은 Project → Session → Agent `engine_options`로 해석하고 부모 제한을 확대하지
않는다. 모델 인자는 이 상속에 참여하지 않는다. 선택 Agent의 값만 사용하며 생략된 SDK
옵션은 생략하고 명시적 null은 Adapter 계약으로 검증한다. SDK 자체 인증은 계속 사용할 수
있다. Graph-backed Agent는 `completion`을 허용하지 않는다.

Loop 등록 객체는 모델 값을 보유하지 않는다. 여러 Agent가 같은 Engine을 사용해도 각
실행에서 자기 정의를 복사한다. `policy.request_timeout`과 Agent `completion.timeout`은
독립적이다. UI의 Engine 설정 조회에는 모델 값이 없으며 Agent CRUD/snapshot으로 조회·
수정한다. Adapter의 공개 `describe_config()`가 모델 인자 Schema를 제공한다.
Application은 `project.agents.adescribe(engine="loop")`로 이 Adapter 스키마와 Loop 옵션을
포함한 Agent 폼 계약을 받는다. Graph 등 다른 Engine은 자기 Agent 계약만 반환한다.
실제 값은 Agent snapshot으로 읽으며 조회 결과로 미설정 값을 채우지 않는다.

시스템 지침도 Loop 설정으로 상속하지 않는다. 선택된 Prompt → Agent `system_prompt` →
Skill 순서로 구성하며, Agent의 인라인 지침이 missing/null/빈 문자열이면 그 부분만 넣지
않는다. missing과 명시적 null/빈 문자열은 Agent 저장값에서 구별되며 새로운 값으로
채워지지 않는다. 선택된 Prompt·Skill 지침을 null로 제거하지 않는다. Loop의 effective
configuration에 지침을 복제하거나 출처를 Engine 설정으로 표시하지 않는다.

`Agent.update/aupdate`는 최상위 필드 단위 갱신이다. `completion`을 수정할 때 기존 옵션도
유지하려면 snapshot의 해당 dict에 명시적으로 변경값을 합친 뒤 `expected_revision`과 함께
저장한다. Backend가 Project나 다른 Agent의 모델 옵션을 추가로 합치지 않는다.

## 저장·승인·재개

Agent definition/revision과 Run admission snapshot 계약을 유지한다. 모델이나 지침을 변경하면
Agent revision과 실행 binding이 바뀌므로 과거 승인·체크포인트를 변경된 모델로 재사용할 수
없다. 완료 Tool 영수증, 사용량, child scope 제한, 명시적 재개 절차는 바뀌지 않는다.

API 키를 직접 저장하면 Agent 레코드뿐 아니라 queued admission과 Run의 정의 snapshot에도
포함된다. 별도 secret store나 자동 마스킹을 추가하지 않았다. 진단/모델 메시지가 이 정의를
복사하는 것은 별개이며 회귀 검사로 보호한다. 저장하지 않는 인증이 필요하면 SDK가 지원하는
외부 인증을 사용한다.

## 수동 변경과 Application 영향

기존 `parameters.engines.loop.config.completion` 또는
`engine_options.config.completion`은 이제 검증 오류다. 필요한 값을 각 Agent의 `completion`으로
명시적으로 옮기고 원래 키를 제거한다. 자동 이관·기본 모델 선택·호환 alias는 없다.
`parameters.engines.<name>.config.system_prompt`와 Session의 동일 경로,
Agent `engine_options.config.system_prompt`도 거부한다. 필요한 인라인 지침은 해당 Agent의
`system_prompt`에 명시적으로 저장하고 이전 키를 제거한다. 공통 지침을 공유하려면 Agent의
`resources.prompt`로 저장 Prompt를 선택한다.
저장 형식 버전과 도메인 필드는 바꾸지 않았다.
이전 실행 binding과 같다고 간주하는 변환도 없다. 모델의 저장 위치나 binding이 달라진
과거 승인·체크포인트는 재개 검증에서 거부될 수 있다. 이미 수행한 효과를 확인한 뒤 새
요청을 명시적으로 제출해야 하며 Backend가 처음부터 자동 재실행하지 않는다.

`graph_rag.py` 예제 설정은 `project_config`와 `agent.completion`을 분리한다.
`configuration_workflow.py`, `developer_assistant.py`도 같은 분리를 사용한다.
`ish_loop.py`와 CLI의 모델 인자는 저장 Agent에 전달한다.

Hub product 코드는 이번 범위에서 수정하지 않았다. 현재 Hub는 저장 Agent의 모델·지침을
사용한다. 사용자 설정에 이전 Loop config 경로가 남았다면 새 Agent 경로로 수동 수정해야
하며 Backend에 Hub 전용 fallback을 두지 않는다.
