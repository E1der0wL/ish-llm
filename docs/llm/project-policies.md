# Project 공통 정책과 구현체 설정

ProjectConfig는 설정의 저장 원본이다. `policies`에는 서비스가 집행하는 공통 정책을,
`parameters.engines`와 `parameters.components`에는 해당 구현체가 해석할 인자를 저장한다.
정책마다 클래스를 만들지 않는다. 실제 처리 알고리즘이나 검증된 실행 계약이 필요한
경우에만 클래스를 유지한다.

| 위치 | 책임 |
| --- | --- |
| policies.context | Run에 전달할 대화의 공통 선택. ContextPolicy/ConversationContextBuilder |
| policies.run | 대기열·실행 기한·capability 확장 한도. RunPolicy/RunManager |
| policies.approval, tool_retry | 공통 승인과 조건부 Tool 재시도. 기존 ToolExecutor 계약 |
| policies.usage | Run/Project의 누적 호출·토큰 사용량. 입력 선택과 독립된 counter |
| policies.retention | 영속 기록의 명시적 보관·정리 |
| policies.output | 서비스의 델타 저장 batching |
| parameters.engines.&lt;이름&gt;.policy.completion | Loop가 해석하는 모델 입력 예산. CompletionPolicy |
| parameters.engines.&lt;이름&gt;.policy.provider | Loop 스트리밍의 명시적 외부 시도/기한 |
| parameters.components.memory.policy.processing.provider | Memory 자체 요약·추출 모델의 외부 시도/기한 |
| parameters.components.rag.policy.provider / vision.policy.provider | 각 Component의 모델 호출 설정 |

`policies.completion`과 `policies.provider_retry`는 거부한다. 자동 변환·기본값 복원·호환
별칭은 없다. 사용자 extension namespace는 계속 JSON으로 보존한다.

## 설정 예

```python
from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig

backend = LargeLanguageModel("./workspace", engines={"assistant": LoopEngine()})
project = await backend.projects.acreate("개발 작업", config=ProjectConfig(
    policies={
        "context": {"mode": "full"},
        "run": {"max_queued": 20, "timeout_seconds": 1800},
        "usage": {"max_tokens": 200000, "counter": "model_default"},
    },
    parameters={"engines": {"assistant": {
        "config": {"completion": {"model": model_name, "max_tokens": 4000}},
        "policy": {
            "completion": {"max_tokens": 32000, "reserve_tokens": 4000, "counter": "model_default"},
            "provider": {"max_attempts": 3, "delay_seconds": 1, "max_delay_seconds": 10},
        },
    }}},
))
session = await project.sessions.acreate()
run = await (await session.run.submit("작업해줘", engine="assistant")).wait()
```

숫자는 이 예제에서 명시한 선택이다. 빈 설정은 빈 상태로 유지된다. policy.completion의
max_tokens-reserve_tokens가 입력 예산이며 SDK의 출력 max_tokens를 대신 설정하지 않는다.
CompletionPolicy는 메시지와 Tool 정의를 계수하고 오래된 턴을 제외한다. 시스템 지시,
현재 요청·Tool 교환·추가 지시는 보존한다. 현재 작업 자체가 넘치면 호출 전에
context_budget_exceeded로 실패한다. 저장된 Conversation은 변경하지 않는다.

usage.counter는 누적 사용량 예약용 계산기다. Engine의 입력 계산기·예산을 바꾸거나
해제해도 공통 usage 상한을 우회하지 않는다. 토큰 사용량 제한을 켜면 SDK 출력 상한도
명시해야 한다. 계산기는 ServiceConfig.token_counters에 이름으로 등록한다.

## 수정과 통합 조회

```python
# Project 공통 정책만 부분 수정한다.
await project.aconfigure_policies({"run": {"timeout_seconds": 900}})

# 구현체 설정은 동일 ProjectConfig 원본에 저장한다. UI는 편집 버전을 함께 전달한다.
view = await project.aconfiguration()
config = view["project"]["config"]
config["parameters"]["engines"]["assistant"]["policy"]["completion"] = None
await project.asave(config=config, expected_version=view["config_version"])

# Component 전문 API도 같은 설정 원본에 쓴다.
# await project.components.memory.aconfigure({"policy": {"processing": {"provider": {"max_attempts": 2}}}})
```

Engine 상속은 **Project → Session → Agent → 명시적 host** 순서다. missing은 상위의
명시값을 상속한다. Loop policy.completion/policy.provider 전체의 null은 상위 설정을 해제한다.
provider.wall_timeout=null은 시간 제한만 해제한다. 미설정 SDK 옵션은 전달하지 않는다.
각 Engine의 values/sources/overridden/editable과 schema x-host-override는 같은 계약을 따른다.
공통 policies와 Component 설정은 Session/Agent가 덮어쓰지 못한다.

## 실행 객체와 수명

Run 시작 시 공통 정책을 Run.metadata.policies에 복사하고 ProjectPolicyResolver가
`(ContextPolicy 또는 None, RunPolicy)`를 만든다. Engine 입력 정책과 재시도는 생성하지 않는다.
각 Loop는 해당 실행의 설정 사본에서 CompletionPolicy를 만들고 처리기에 전달한다.
Graph Agent도 선택한 Engine의 설정을 해석하므로 서로 다른 모델 예산을 사용할 수 있다.
현재 Run은 설정 변경의 영향을 받지 않고, 대기 요청은 시작 시 최신 설정을 사용한다.

Memory가 Loop 요청에 기억을 추가할 때는 전달받은 completion_policy로 예산을 확인한다.
Memory의 자체 요약·추출 호출은 config.processing.completion과 policy.processing.provider만 사용한다. Loop의
provider 설정은 자동 상속하지 않는다. 공통 Run 기한·승인·Tool 효과·사용량 계약은 유지한다.

BaseEngine.stream_completion(request, provider=...)는 명시된 호출 설정만 사용한다.
max_attempts는 최초 호출을 포함한다. 미설정은 최초 호출만, wall_timeout 미설정은 자체
기한 없음이다. SDK retry가 명시되어 활성화되면 외부 시도는 1회로 제한한다. 첫 chunk를
받은 이후와 취소는 자동 재시도하지 않는다. 이벤트 저장 중인 소비자를 기한 타이머로
취소하지 않으며 다음 Engine 진행 시 같은 절대 기한을 확인한다.

변경한 입력/provider 설정은 기존 checkpoint binding에 포함되므로 옛 설정의 명시적
재개를 조용히 실행하지 않는다. checkpoint/Run/Step 저장 책임은 변경하지 않는다.
CompletionPolicy는 [policies/completion.py](../../llm/policies/completion.py), RunPolicy는
[services/runtime/policies.py](../../llm/services/runtime/policies.py)에 있다.
