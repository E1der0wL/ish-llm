# 현행 LLM 아키텍처

현재 main의 구현과 보존할 계약을 설명합니다. 리팩터링은 main에 병합되었으며
단계별 근거는 [전수 점검](contract-closure.md), 최신 변경과 검증 범위는 [인계](handoff.md)에 있습니다.
과거 누적 안내는 [보관 목록](documentation-history.md)에서 분리해 읽을 수 있습니다.

## 도메인과 책임

```text
Project ─┬─ Agents (독립 정의·필수 목록)
         ├─ Components
         └─ Sessions ─┬─ Messages
                      └─ Runs ─ Steps

현재 최상위 실행: 저장 Agent 요청 → RunManager → Agent binding → 선택 Engine
현재 중첩 실행: Graph AgentNode / agent_run → AgentExecution → 선택 Engine
```

Engine은 실행 전략이며 영속 소유 계층의 중간 객체가 아니다. Component는 Project의 기능과
자료를 소유한다. Run/Step의 생성·상태·출력·사용량·수명은 서비스가 저장한다. Component를 등록하거나
정의를 저장하는 것만으로 선택·설치·모델 호출·Tool 실행이 시작되지 않는다.

저장 Agent가 모델명·인증·추론 인자를 `completion`으로 소유한다. Loop는 반복·Tool·문맥 예산·
재시도 등 실행 방식을 소유하며 Project/Session의 Loop 설정에서 모델 값을 상속하지 않는다.
선택 Adapter가 SDK 인자를 검증·전달하고 Graph Agent는 모델 설정을 받지 않는다.
Agent revision과 admission snapshot이 모델 변경도 보호한다. [모델 설정 계약](agent-models.md).
Agent는 `system_prompt`와 선택한 Prompt·Skill 지침도 소유한다. Loop는 구성된 지침을
실행 입력과 승인·재개 binding에 사용하며 공개 `config.system_prompt`나 Project/Session
프롬프트 상속을 제공하지 않는다. Loop의 공개 `config`는 `buffer_size`만 포함한다.

Loop의 호출별 Tool 노출 선택은 Application이 등록한 `ToolSelector`가 수행한다.
Backend는 기존 Agent/Run 허용 범위의 부분집합인지 검증하고 선택된 정의만 모델에 전달한다.
노출은 권한이 아니며 기존 ToolExecutionScope를 바꾸지 않는다. 응답과 함께 저장한 노출 목록은
승인 재개에도 유지하고 미노출 Tool 호출은 효과 전에 거부한다. 선택기는 일반 completion 입력
가공과 분리하며 Scope·승인·재시도·저장은 기존 소유자에 남는다.

선택 Engine의 순수 동기 `describe_agent()`는 Agent 설정 폼과 binding 검증이 공유하는
스키마를 제공한다. Agent 서비스는 `describe(engine=...)`로 이를 전달하고 최상위 공통
계약을 확대하지 못하게 한다. Engine/Adapter가 내부 필드의 스키마를 소유하며 UI 배치·
preset·기본 입력값은 Application에 남는다. 조회가 실행·저장·설정 resolution을 대신하지 않는다.

| 경계 | 소유 코드 | 책임 |
|---|---|---|
| 공개 API·조립 | `llm/llm.py`, `services/api.py`, `services/composition.py` | 수명 관리와 핸들, 구현·저장소·Host 자원 연결 |
| 도메인·계약 | `core/` | Project/Session/Message/Run/Step, Schema, 출력·진단·계획의 데이터 계약 |
| 생성·참조·설정 | `services/lifecycle/` | CRUD, 접근·선택·CAS·clone·삭제 보호 |
| 대화·문맥·보관 | `services/history/` | Conversation 원본, 턴 선택·삭제, 복구·retention |
| 실행·승인·사용량 | `services/runtime/` | durable 요청, Run/Step, ToolExecutor, checkpoint·operation receipt |
| 저장·잠금·관찰 | `services/infrastructure/` | workspace ownership, StorageIO, transaction, 백업·로그·파생 index |
| 알고리즘 | `engines/`, `components/`, `policies/` | 선택 구현체의 실행 의미·자료·재사용 판단 알고리즘 |
| 외부 연결 | `providers/` | 실제 SDK/MCP Adapter, admission·retry·stream·usage·진단 |
| 제품 선택·UI | `hub/` | 생성 preset, 환경 캡처, 사용자 선택, 정규 API 소비와 화면 투영 |

세부 판정 기준은 [계약 선언과 의미 해석의 단일 소유](contract-ownership.md)다. 서비스의 공개
작업 조율과 도메인 규칙의 직접 구현을 구분하고, Manager의 private 저장 형식 해석·Hub의 정책
재계산·SDK wire 유출·실행 경로별 binding/승인 복제를 점검한다. Owner는 여러 협력 객체로
구현할 수 있으며, 책임 분리 후에도 잠금·CAS·원자 확정·실행 직전 재검증·자원 수명을 보존한다.

## 설정·선언·이름

정규 계약은 [CONFIGURATION](../../llm/CONFIGURATION.md), [Project authority](project-authority.md),
[Schema ownership](schema-ownership.md), [Naming](naming-conventions.md)에서 소유한다.

- ProjectConfig는 `policies`, `parameters`, `data`로 닫혀 있다. 내장 정의의 확장 데이터는
  명시한 `metadata`에 둔다. SDK kwargs·선택 handler/client 인자는 해당 owner가 검증한다.
- Engine은 Project → Session → Agent 순으로 해석하며 child가 부모 제한을 확대하지 못한다.
  Component는 주입 client의 명시값 → Project 순서다. Session이 Component 설정을 덮지 않는다.
- 서비스 정책은 Run 시작 사본이다. Host는 구현·기술적 거부·공유 자원을 제공하며 제품 정책의
  마지막 override 층이 아니다. 일반 `resolve_config(host=...)` 관찰 기능과 혼동하지 않는다.
- Missing은 부재/상속, Null은 nullable 필드의 명시값, False/0/빈 배열도 값이다.
  필수 설정은 기능 실행 전에 검증한다. Schema/ParameterLayout은 실행 기본값 생성기가 아니다.
- 의미가 있는 값은 구현체별 단일 선언에서 Schema·validator·Reader/Writer·소비자로 연결한다.
  한 번 사용하는 계약 값·필드 집합·공개 Projection도 포함한다. 의미 해석·검증·전이 구현은
  Owner가 제공하고 각 신뢰 경계가 호출한다. 전역 상수 파일이나 고정 helper용 registry는 만들지 않는다.
- SDK native default, 프로토콜 불변식, CRUD 초기 형식, cache/backpressure sizing,
  Application 생성 preset과 숨은 실행 정책을 구별한다. 각 값의 의미와 소유자는 명시한다.

## 현재 요청과 실행 수명

`session.run.submit(content, recipients=[...], request_context=...)`는 durable QUEUED 입력을 저장하고
RequestHandle을 반환한다. `wait()`는 수신자별 종료 RunHandle 목록을 반환한다. 수신자가 하나여도 같은 목록 API다. 접수 ACK와 Run 생성·실행 시작은
별개 경계다. 접수 때 Agent 정의·revision·Engine binding을 고정하고 실행 전에 다시 검증한다.
request_context는 Host 환경이며 저장 정의에 합치지 않는다. 취소된 호출자가 같은 내용을 무조건 다시 제출하면 중복 요청이 될 수 있으므로
저장된 요청을 조회한다.

Session은 실제 저장한 `agent_ids`를 참여자로 보유한다. 명시한 `recipients="all"`만 이 목록을
접수 시점에 확장한다. 각 요청은 하나의 User, 완전한 Agent→Run map과 동일한 history snapshot을
원자적으로 저장한다. 같은 Session·Agent는 FIFO, 서로 다른 Agent는 병렬 실행한다.
`active_run_ids`와 공개 `active_runs`는 실행 중인 모든 Run을 나타내며 임의 대표 Run을 선택하지 않는다.
중첩 Agent/Workflow는 부모 Run의 Step·Tool scope·사용량·checkpoint 경계를 공유한다. 선택된 Agent의
정의·참조·Engine identity를 승인/실행 전에 고정하고 실제 선택된 자식만 재개 binding에서 검증한다.
미선택 Agent의 private 설정을 이유로 다른 분기 실행을 막지 않는다.

취소를 무시하는 작업은 PendingWork가 추적한다. 실제 정리 전 Session/workspace ownership을 풀거나
다음 작업에 권한을 넘기지 않는다. 관찰 구독은 실행을 소유하지 않는다. 자세한 확장 계약은
[Engine README](../../llm/engines/README.md), [공통 경계](shared-contracts.md)를 따른다.

## Tool 승인·효과·재개

Run에 연결된 Tool 실행은 서비스가 전달한 `ToolExecutionScope`를 반드시 유지한다.
정책값 미설정은 정상적인 빈 정책 scope이며, scope 객체 자체의 누락은 실행 계약 오류다.
중첩 Agent도 부모 scope를 새 기본 객체로 대체하지 않는다. 공통 검증과 독립 호출의
구분은 [필수 실행 객체의 전달](project-authority.md#필수-실행-객체의-전달)을 따른다.

1. Tool 선택·최종 인자/제약 검증 → 분류·trusted contract → 승인/원장 검사 → handler 실행 순서다.
2. Host technical deny는 저장된 approve로 해제되지 않는다. 분류·모델 설명·Skill 지침은 권한이 아니다.
3. 승인 대기는 durable InteractionRequest로 저장한다. PAUSED 확정 후 정책/사용자 응답을 저장하며,
   **응답 저장만으로 실행하지 않는다**. 실행은 명시적 Resume admission을 다시 통과한다.
4. Resume는 원본 Run을 보존하고 새 Run을 만든다. 완료 Tool 결과는 재사용한다. 시작했지만 결과가
   확정되지 않은 효과는 자동 반복하지 않는다. 동일 business operation key의 영수증도 검사한다.
5. 설정·인자·Tool contract·환경·선택된 Agent/handler·checkpoint source가 바뀌면 과거 승인/결과를
   조용히 새 정의에 맞추지 않는다. owner의 binding·revision 검사를 적용한다.
6. Run 단위 예산과 Project 기간 예산, 논리 Agent/Tool의 누적 한도·예약·retry 복원은 구분한다.
   Resume라고 모두 초기화하거나 모두 같은 누적 범위로 합치지 않는다.
7. `resume_plan`은 조회이며 실행 허가의 영구 증명서가 아니다. 실행 때 잠금 안에서 재검증한다.
   `restart`는 dispatch 이전 중단의 영속 증거와 revision이 있을 때만 허용한다.

세부 계약: [승인](interactions.md), [재시도](retry.md), [체크포인트 수명](checkpoints.md),
[Tool 격리와 효과 원장](process-isolation.md), [공통 완료/receipt 경계](shared-contracts.md).

## 저장·복구와 관찰

WorkspaceOwnership 아래의 TransactionManager가 쓰기 전 undo, fsync, commit marker, rollback/recovery를
관리한다. 잠금 진입 후 읽기 전에 미완료 journal을 복구한다. Run 시작/종료, 출력 journal과 Conversation,
승인 응답, 연결된 Step 완료/checkpoint receipt는 서비스가 정의한 저장 단위로 확정한다.
성공 알림은 commit 뒤에만 전달한다. 첨부 checkpoint가 있는 STEP_COMPLETED는 둘이 함께 확정된다.

외부 Tool 효과와 로컬 저장은 하나의 원자 작업이 아니다. 효과 뒤 저장 실패는 started/uncertain으로
보호한다. 외부 결과 확인은 명령 재실행이 아니다. 파일 부재나 Step 부재로 효과 없음이라고 추론하지 않는다.

- Conversation은 대화 원본, Run/Step은 실행 원본, Memory 요약·Activity·출력 index는 파생 자료다.
- file/memory 대화의 재시작 수명은 다르다. memory 자료를 디스크에서 추측해 복구하지 않는다.
- 삭제·retention·abandonment는 계획/CAS·참조 보호·트랜잭션을 사용한다. 삭제된 재개 원본을 복원하지 않는다.
- 현재 형식의 backup/restore는 checksum·Component 검증·staging publish를 유지한다.
  필수 Agent 목록도 검증하며 clone은 Project identity를 다시 결합한다. 자동 migration은 없다.
- LogConfig는 기록/조회 활성화만 설정한다. 기본 파일은 추가 기록만 하며 회전 크기/개수는 Application sink가 소유한다. 자동 보존값은 없다.
- 출력/관찰 이벤트 유실 시 저장된 Run/Step/journal을 조회한다. UI snapshot과 알림은 원본이 아니다.

정확한 범위와 한계는 [트랜잭션](storage-transactions.md), [저장 운영](operational-storage.md),
[대화 저장](conversation-storage.md), [출력](engine-output.md)에 있다.

## Provider·Processor·Component 경계

Completion/Processor/Tool/RAG/Vision/worker/Hub는 공통 모델을 소비하며 실제 Adapter가
SDK 호출·wire 해석·stream 조합·retry·usage를 소유한다. A의 실제 HTTP 횟수와
전체 회귀 증거는 [Provider 검증](provider-refactor-verification.md)에 기록했다.

부분 응답 뒤 자동 provider retry는 금지한다. 명시 SDK retry와 외부 retry를 중첩하지 않는다.
LiteLLM retry guard·bundled cost map·stream/n·embedding no-cache는 실제 프로토콜 불변식으로 유지한다.
unknown usage를 0으로 만들어 예산을 넓히지 않는다.

CompletionPipeline은 Run별 session을 만들고 prepare → policy → completion → after_completion →
최종 응답의 finish 순서를 사용한다. after_completion은 관찰이며 응답 변조/자동 재실행 권한이 아니다.
finish는 영속 Run 완료 알림이 아니다. 명시적으로 활성화한 review_final은 최종 후보를 검토하는
별도 계약이며 [최종 검토](completion-review.md)의 verdict·예산·checkpoint 규칙을 따른다.

RAG는 분할·검색·세대 공개를, 선택 model client는 SDK 인자·벡터 의미를 소유한다. 현재 embedding은
청크별 요청이며 ordinal을 애플리케이션이 대응시킨다. Memory·Goal·Refinement·Skill·Data·Vision은
각자의 자료·참조·generation/revision·승인 경계를 유지한다. 중앙 실행/승인 저장소를 새로 만들지 않는다.

Builtin 명령의 선택적 영속 출력은 Builtin Component가 관측 레코드·파일 참조를,
선택한 file Component가 원문을 소유한다. 참조와 파일은 기존 workspace transaction으로
확정하며 Run/Step/operation receipt를 복제하지 않는다. 읽기 grant는 작업 cwd와 분리하고
쓰기·sandbox 권한을 확대하지 않는다. [대용량 파일 계약](large-data-files.md)을 참고한다.

## 검증 상태

현재 기준선은 [작업 인계](handoff.md), 문서 변경은 [검토 기록](documentation-review.md), 운영상 한계는
[운영 준비 상태](production-readiness.md)를 본다. 과거 기록의 시험 수나 문서 예제의 문법 검사를
제품 구현·실모델·CI 성공으로 해석하지 않는다. 핵심 승인·한도·중복 효과·저장 회귀가 해결되지 않으면
다음 구현 단계로 넘어가지 않는다.
