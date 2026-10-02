# 프로젝트별 대화·Completion·Run 정책

사용자 정책은 `ProjectConfig.policies`의 JSON 값으로 저장한다. Project는 설정을 소유하고,
서비스의 ProjectPolicyResolver가 실행 객체를 구성한다. Project가 모델이나 Run을 실행하지 않는다.
Memory/RAG/Skill/MCP 설정은 계속 각 컴포넌트가 소유한다.

정책에는 tool_retry/provider_retry/usage/retention도 포함된다. 모든 사용량/보관 상한은
기본 null이고 공급자 재시도는 0회다. [장시간 실행 API](long-running.md)에 조건부 Tool
재시도와 보관 미리보기·적용 예제를 설명한다. UI는 configuration의 최신 policy_schema를 사용한다.

```python
from llm.llm import LargeLanguageModel, ProjectConfig, ServiceConfig

backend = LargeLanguageModel("./workspace")
project = await backend.projects.acreate("개발 작업", config=ProjectConfig(
    completion={"model": model_name},
    policies={
        "context": {"mode": "full"},
        "completion": {
            "max_tokens": 32000,
            "reserve_tokens": 4000,
            "counter": "model_default",
        },
        "run": {"max_queued": 20, "timeout_seconds": 1800},
    },
))
session = await project.sessions.acreate()
run = await (await session.run.submit("작업해줘", engine="loop")).wait()
```

completion의 model은 모델 호출 인자이며 policies.completion은 입력 선택 정책이다.
`max_tokens - reserve_tokens` 안에 최종 요청이 들어오도록 오래된 턴을 제외한다.
출력 여유분은 모델 출력 길이 설정을 대신하지 않는다. 원본 Conversation은 삭제하지 않는다.

## 저장 및 수정 API

```python
# 최신 저장값에 정책 일부만 병합한다. 다른 모델/엔진 설정은 보존한다.
policies = await project.aconfigure_policies({
    "context": {"mode": "recent", "max_turns": 10},
    "completion": {"max_tokens": 16000},
})

# 제한 해제: 기존 출력 여유분도 0으로 설정한다.
await project.aconfigure_policies({"completion": {"max_tokens": None, "reserve_tokens": 0}})

view = await project.aconfiguration()
saved = view["project"]["config"]["policies"]
schema = view["policy_schema"]
components = view["components"]
used = (await run.aget_data()).metadata["policies"]
```

동기 API는 `configure_policies`, `configuration`이다. ProjectManager에도 같은 API가 있다.
`project.save(config=...)`는 전체 ProjectConfig를 교체하므로 부분 수정에는 configure_policies가
적합하다. `ProjectConfig.policy_schema()`는 파일 접근 없이 필드/기본값/설명을 반환한다.
스키마와 추가 관계 검증을 저장 전에 수행하며 잘못된 값은 기존 파일을 덮어쓰지 않는다.

저장 전 설정 객체를 직접 구성할 때는 `ProjectConfig.configure_policies(changes)`를 사용한다.
JSON 검사·재귀 병합·전체 설정 검증을 후보 사본에 수행한 뒤 정책만 반영하므로, 실패하면
메모리의 기존 설정도 유지된다. 반환값은 입력 및 내부 설정과 분리된 정책 사본이다.

```python
config = ProjectConfig(completion={"model": model_name})
policies = config.configure_policies({"context": {"mode": "recent", "max_turns": 20}})
# 위 호출은 메모리만 변경한다. 기존 프로젝트 저장에는 project.aconfigure_policies를 사용한다.
```

ProjectManager는 잠금 아래 최신 Project를 조회하고 이 메서드에 변경을 위임한 뒤 저장한다.

새 Project에는 아래 기본값을 모두 명시해서 저장한다. ProjectConfig의 생략 필드는 고정
라이브러리 기본값으로 정규화하며 백엔드의 현재 정책으로 덮어쓰지 않는다. 추가적인 정책
namespace는 자유로운 JSON으로 보존한다. 내장 context/completion/run 내부의 오타는 거부한다.

| 영역 | 기본값 |
| --- | --- |
| context | mode=full, max_turns=10, max_chars=null |
| completion | max_tokens=null, reserve_tokens=0, counter=model_default |
| run | max_queued=null, timeout_seconds=null, max_capability_rounds=32 |

context mode는 full/recent/completed/recent_completed/budget을 제공한다. budget은 문자 수
max_chars를 요구한다. recent의 max_turns는 과거 턴 수이며 현재 입력은 별도로 유지한다.
Session config/session_defaults에는 policies를 지정할 수 없다. 프로젝트 정책을 Session나 Agent의
설정 병합으로 우회하지 않으며, 중첩 Agent/Graph는 소유 Run의 CompletionPolicy를 공유한다.

## 실행 시작 시점의 정책

Run 시작 시 최신 프로젝트 정책의 JSON 사본을 `Run.metadata.policies`에 기록한다.
그 사본으로 ContextPolicy, CompletionPolicy, RunLimits를 구성한다. 실행 중 설정 변경은
현재 Run을 바꾸지 않으며 대기 중 요청은 실행 시작 시 최신 정책을 사용한다.
대기열 상한은 새 요청을 받을 때 최신 프로젝트 값으로 검사한다. 이미 저장된 대기 요청은
한도가 낮아져도 지우지 않는다. 정책 변경 때문에 실패한 실행을 자동 재시도하지 않는다.

Graph 명시적 재개는 기존 checkpoint 설정 일치 검증을 그대로 따른다. 프로젝트 정책을
변경했다면 기존 체크포인트와 설정이 달라 재개가 거부될 수 있다. 원래 설정으로 복원하거나
새 작업을 제출한다. 재개에서는 기록된 원본 메시지 ID를 복원하며 ContextPolicy로 다시 자르지
않는다. CompletionPolicy는 이어지는 모델 호출에도 적용된다.

Memory가 오래된 대화 전체를 요약하게 하려면 context.mode=full을 유지한다. recent 등으로
먼저 제외한 메시지는 Memory에 전달되지 않는다. Memory의 keep_turns/요약 모델/주입 예산은
`ProjectConfig.component_configurations.memory`에서 관리한다. policies와 중복 저장하지 않는다.

## 토큰 계산기와 실행 구현

기본 model_default는 요청의 model/messages/tools/tool_choice를 LiteLLM token_counter에
전달한다. 공급자별 토큰 수의 정확성은 해당 tokenizer 지원에 달려 있으며 문자열 길이를
토큰 수로 추정하지 않는다. 다른 공급자/별도 계산법은 이름으로 등록한다.

```python
backend = LargeLanguageModel("./workspace", services=ServiceConfig(
    token_counters={"company_model": count_request_tokens},
))
# 이 backend에서 읽은 ProjectHandle에 적용한다.
await project.aconfigure_policies({
    "completion": {"max_tokens": 32000, "counter": "company_model"},
})
```

계산기는 동기 함수 counter(request)로 음수가 아닌 int를 반환하며 모델/Tool 정의/포맷 비용을
포함해야 한다. 계산은 기존대로 이벤트 루프 밖에서 수행한다. 동시에 호출될 수 있으므로
스레드 안전해야 한다. 함수는 JSON에 저장하지 않는다. 지정한 이름이 등록되지 않았으면
제한이 켜진 요청을 QUEUED로 저장하기 전에 `policy_unavailable`로 거부한다. 복제/백업/재시작은
정책과 이름을 보존하며 새로운 백엔드에서도 같은 이름의 구현을 등록해야 한다.
저장된 이름만으로 함수 구현의 동일성까지 검증하지는 않는다. 계산 방식을 변경한다면
`company_model_v2`처럼 별도 이름을 등록해서 적용 이력을 구분하는 것이 적절하다.

`ServiceConfig.policy_resolver`로 다른 실행 구현을 주입할 수 있다. `resolve(settings)`는
`(ContextPolicy, CompletionPolicy 또는 None, RunLimits)`를 반환하는 동기·비차단 함수다.
정책은 호출별 사본으로 구성해야 한다. 주입 context_builder의 for_run은
`for_run(messages, input_message_id, *, policy=...)` 계약으로 프로젝트 선택 정책을 받는다.
같은 builder의 for_clone은 기존 저장소/복제 경로에서 계속 사용한다.

ServiceConfig에는 token_counters/계산기 resolver/저장소/연결·승인·실행 어댑터/관찰자와 공유
자원 설정이 남는다. ProviderLimits는 프로젝트들이 공유하는 실제 provider 슬롯 한도이며
프로젝트 설정으로 늘리지 않는다. ToolPolicy의 호스트 승인/허용 범위, OutputPolicy의 저장
배치와 로그·캐시 설정도 백엔드 운영 구성으로 유지한다. 모든 Python 객체를 JSON에 저장하거나
모든 운영 설정을 프로젝트로 이동한 것은 아니다.

기존 클래스는 `CompletionPolicy`, EngineContext 필드는 `completion_policy`로 통일했다.
ServiceConfig의 context_policy/completion_policy/run_limits 인자는 제거했으며 호환 별칭은 없다.
해당 값은 ProjectConfig.policies로 옮겨야 한다. 직접 요청 선택기를 만들 때는
`CompletionPolicy(max_tokens, counter=..., reserve_tokens=...)`를 사용할 수 있다.
