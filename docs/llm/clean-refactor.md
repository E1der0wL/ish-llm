# 클린 리팩터링 설계와 구현 게이트

**상태: P0·P1-a/b 검증 완료, P1-c 진행 중, A–F 미착수.** 기준은 main
`94229cb865374d99f20ec48c5e48269488041a4e`이며 `refactor/llm-clean`에서 시작한다.
기존 `refactor/llm-backend`의 코드/커밋을 cherry-pick·merge하지 않는다. main과 기존 Draft PR #1은
변경하지 않는다. [현행 구조](architecture.md)와 이 목표 구조를 구분한다.

## 필수 원칙

사용자 필수 설계 원칙·구현체별 단일 선언 → 현재 Naming 의미·명시적 설정 → A–F 기능/안전성 →
과거 기록 순으로 적용한다. 충돌과 근거는 [검토표](documentation-review.md)에 남긴다.

- 구 API·저장 형식·자동 migration·호환 Facade를 만들지 않는다. 현재 Reader/Writer 하나로
  검증하고 지원하지 않는 자료는 실행·쓰기 전에 명시 거부한다. 원본을 자동 삭제/변환하지 않는다.
- 필수값 누락은 거부한다. Missing/Null/Value와 선택 기능의 비활성·추가 제한 없음은 필드 owner가
  명시한다. `x or fallback`으로 False/0/빈 객체/잘못된 타입을 같은 값으로 바꾸지 않는다.
- Hub가 소유한 생성 초기값은 허용한다. 새 Project에 저장하고 사용자 명시값을 우선하며,
  조회·편집 가능해야 한다. 기존 Project 재열기/실행 시 재주입하지 않는다. 매번 사용자에게
  선택을 요구하지 않으며 Backend는 Hub의 프리셋 이름·선택 규칙을 알지 못한다.
- 버전·상태·이벤트·오류 코드·정책·경로·한도는 해당 구현체가 단일 선언으로 소유한다.
  Schema·검증기·Reader/Writer·소비자는 그 선언을 사용한다. 기존 값도 전수 목록에서 정리한다.
- 전역 상수 파일에 몰거나 고정 helper를 위해 새 추상 계층을 만들지 않는다. 실제 Provider
  Adapter는 유지한다. 외부 SDK 불변식과 현재 sync/async API를 구형 호환 경로로 오판하지 않는다.
- 기존 Hub·CLI·예제·테스트는 정규 API로 직접 이관한다. wrapper로 옛 실행 경로를 남기지 않는다.
- Tool 승인·권한·한도·Checkpoint·Resume·효과 영수증·동시성·자료 참조 안전성을 약화하지 않는다.

세부 설정 의미는 [CONFIGURATION](../../llm/CONFIGURATION.md), 소유권은
[Project authority](project-authority.md)와 [Schema ownership](schema-ownership.md), 이름은
[Naming](naming-conventions.md)을 따른다. 문서의 예제 숫자는 제품 기본값 선언이 아니다.
Hub 초기값의 단일 선언·M/N/V·검증 경계는 [생성 프리셋 계약](../hub/presets-and-resume.md#hub-초기값의-소유권과-저장-계약)에 있다.

## 목표 구조와 단일 경로

```text
Project ─┬─ Agents
         ├─ Components
         └─ Sessions ─┬─ Messages
                     └─ Runs ─ Steps

Session.agent_ids → Project Agents
User.recipients → 접수 때 동결한 대상
N Runs.input_message_id → 하나의 User Message
Run → agent_id + 정의 revision + Engine/정책/리소스 binding

durable admission → Dispatcher → Agent binding → Engine
                                     ├─ Provider Adapter
                                     └─ 공통 ToolExecutor
```

Agent는 Project 소유 정의이며 Session 참여와 Run 실행 상태를 저장하지 않는다.
Engine은 실행 전략이다. 별도 AgentRun/RequestGroup/UI 실행 저장소를 만들지 않는다.
새 공개 submit은 Agent 대상을 받고 root/nested가 같은 선택·narrowing·binding을 사용한다.
`engine=`을 받아 임시 Agent를 생성하거나 엔진 hash로 ID를 만드는 경로는 없다.

Agent 공통 필수 실행 선택은 engine이다. purpose는 업무 설명이며 공통 필수 정책으로 강제하지 않는다.
선택 Engine의 model/workflow 등 구동 필수값은 그 Engine이 검증한다. Graph-backed Agent와 behavioral
Agent의 의미 차이는 유지하며, Graph 조율에 불필요한 모델·Tool 정책을 복제하지 않는다.

## 선언 소유권

| 계약 | 정규 소유자 | 소비 원칙 |
|---|---|---|
| Presence/계층 합성 | core configuration; 필수/nullable는 필드 owner | Engine Project→Session→Agent, Component client→Project, 서비스 Run 시작 사본 |
| 구현 옵션·narrowing | 해당 Engine/Component/handler/client | 동일 선언에서 Schema·검증·설명. parent는 child private 설정 미해석 |
| Agent 정의·revision·참조 | 독립 Agent 도메인·lifecycle | Component/Hub가 저장 원본·수명을 복제하지 않음 |
| 실행 binding | 단일 Agent 해석 경계 | root/nested/Resume에서 같은 정의·권한·환경·한도 증거 |
| 모델 의미 | 공통 request/response/content/tool/usage 계약 | SDK wire/서명·opaque 해석은 실제 Provider Adapter |
| Tool 실행 요구 | ToolContract·정규 Tool 정의 | 등록 시 trusted 명시 선택, worker/승인 지문/인코더가 같은 정의 소비 |
| 승인·Tool budget·retry | Project 정책과 ToolExecutor/Scope | 부모 한도 확대 금지, Host technical deny 유지 |
| 상태·이벤트·오류 | 해당 도메인/실행/실패 경계 | 소비자가 문자열 목록을 재선언하거나 오류 분류로 권한 생성하지 않음 |
| 버전·경로·저장 키 | 해당 record/Repository/저장 구현 | 정확한 타입·필수 필드·현재 형식 Reader/Writer. 모든 버전을 전역 숫자로 합치지 않음 |
| checkpoint | 공통 envelope 저장소 + 내부 의미 Engine/handler | 완료 receipt·source digest·선택 binding·abandonment/CAS 유지 |
| 효과 원장 | OperationRepository/ToolExecutor | Tool call ID와 business key 구분, uncertain 자동 반복 금지 |
| 앱 preset·UI·환경 | Hub의 해당 기능/프리셋 구현체 | 생성 초기값을 정규 설정으로 저장, 사용자 값 우선·재주입 금지, 자료 형식 변환과 숨은 runtime fallback 제거 |

정적 감사는 literal/default/branch/Schema/call/storage 목록과 실제 호출 관계를 결합한다.
일치하는 문자열 개수만으로 위반/완료를 판정하지 않는다. 변경 owner의 미분류 후보는 남기지 않으며,
기술 sizing·외부 프로토콜·앱 선택·표현 값의 예외는 개별 근거를 갖춘다.

## 구현할 정적 감사 규칙

아래는 정적 감사의 목표 범위다. P0에서 추가한 검사와 제한된 자동 검출 범위,
남은 후보는 [P0 실행 기록](p0-verification.md)을 따른다. 새 위반 차단을 전체 위반 해결로 해석하지 않는다. AST로 선언·참조·호출을
추적하고 Schema/Reader/Writer/테스트를 함께 검증한다. 허용 사례는 owner·의미·근거를 기록한다.

| 규칙 | 검출·판정 기준 |
|---|---|
| S1 현재 경로 하나 | 제거 inventory의 API/import/구 저장 필드/변환 Reader와 모든 실제 소비자를 추적. wrapper/alias/옛 형식 분기 잔존 시 실패 |
| S2 Presence와 필수값 | 타입 검증 전 `or {}`/`or default`, 정책 `get`/`setdefault`/생성자 기본값을 분류. M/N/V·False/0/빈 컬렉션을 잃거나 Backend 조회/실행 중 숨은 정책을 생성하면 실패. Hub owner의 새 Project 생성 프리셋은 저장·사용자 값 우선·검증 경계로 판정 |
| S3 단일 선언 | 버전·상태·이벤트·오류·필드·경로·한도의 owner 선언과 Schema/validator/Reader/Writer/소비 참조를 대조. 별도 허용 목록·literal 재선언은 제거 |
| S4 형식·Schema | version은 정확한 타입+지원 값, 조건부 필수 필드·nullable·closed object 검사. open boundary에는 실제 child/SDK/metadata owner 필요 |
| S5 네이밍·책임 | config/policy/runtime/settings, id/key/revision/version의 의미와 저장·실행 owner 대조. 과거 digest 동결을 현재 계약 시험으로 대체 |
| S6 Provider 경계 | SDK import/wire 필드 해석은 Adapter에 한정. Engine/Processor/worker/Hub의 공통 모델 사용을 호출 경로로 확인; opaque 운반과 해석 구분 |
| S7 필수 검증 배정 | discovery test ID를 CI job별 집합과 대조. host/X11/sandbox 필수 시험 누락·중복 효과 assertion 약화·Skip 확대를 완료로 허용하지 않음 |

숫자/문자열 0건을 목표로 하지 않는다. 프로토콜 불변식·CRUD 초기 상태·명시 Tool Python 인자·
Hub 생성 프리셋·UI 표시·기술 cache sizing은 소유자가 선언할 수 있다. 검출하지 못한 동적 호출·미분류 후보는
보고서에 남기며 정적 검사만으로 실제 승인/효과 안전성이 입증됐다고 보고하지 않는다.

## 단계별 의존성과 완료 조건

기존 브랜치의 완료 상태는 승계하지 않는다. [P0 SDK 검증](p0-sdk-transition.md) 이후
[P1 실행 기록](p1-verification.md)을 이어간다. 각 단계는 집중 계약 시험,
관련 회귀, 실제 안전성 gate, 전체 LLM/Hub, 정적 감사와 diff를 확인한 뒤 커밋·작업 브랜치 Push한다.
정상 완료 후에는 별도 승인 대기 없이 다음 단계로 진행한다. 필수 검증이 실패하거나 미확인인 상태에서
다음 단계의 완료를 선언하지 않는다.

| 단계 | 변경 경계 | 완료 gate |
|---|---|---|
| P0 기준선 | host 테스트 메서드명, CI·정적 규칙·test ID 배정, 승인된 SDK/MCP 전환 | 실제 host·MCP 연결/효과/재개, 의존성 lock·선언 일치, 기존 assertion 보존, 격리 job 필수·전체 발견 테스트 누락 없음 |
| P1-a 타입·저장 | configuration, Tool scope, checkpoint/operation/Vision/workspace/Hub Reader | 잘못된 falsey 객체·bool/float 버전 거부, 정상 M/N/V·current roundtrip·실패 원본 보존 |
| P1-b 호환 제거 | Hub preference/theme/profile, backup upgrade, legacy callback·실제 소비자 | 옛 값 명시 거부, 현재 backup/restore/clone·UI·subscription 유지, wrapper 없음 |
| P1-c 선언 정리 | 각 owner의 버전/상태/이벤트/오류/정책/경로/한도, Hub 생성 프리셋 | Schema/Reader/Writer/소비 일치, 생성값·유효한 falsey 값 저장/조회/편집·재열기 보존·검증 우회 금지, 작은 계약군별 검증·커밋 |
| A Provider | 공통 모델 → Completion/Processor/Tool → RAG/Vision/operation → worker/Hub | 비 OpenAI Fake Adapter로 실제 Tool 효과·결과·최종 응답, SDK wire 유출 없음, opaque/usage/retry/취소/승인 보존 |
| B Agent | 독립 record·Repository/Manager/Handle, 참조·CAS | CRUD·선택 Engine 검증·수정/삭제 보호·clone/backup·Skill/Workflow/Refinement 참조 |
| C 실행 | root/nested 단일 binding·Run API·Hub/CLI/예제 | 자동 Agent/engine 호환 경로 없음, 환경·정의·한도 보존, R3 실제 효과 시험 |
| D1 1:N | recipients·동일 User 연결·durable admission·조회/삭제/보관 | ACK 전후·부분 fan-out crash, 중복 접수·대상 동결·파일/메모리 대화·참조 보호 |
| D2 병렬 | 단일 Dispatcher·Run별 task/scope·Session active 집합 | 같은 Session/Agent FIFO, 다른 Agent 실제 동시 barrier, history snapshot·shared quota·취소 격리 |
| E Session 전송 | 정규 admission·권한·correlation/idempotency·deadline/링크 | 같은 key 같은 결과, 다른 payload 충돌, cycle·target 변경·outbox/ACK crash·권한 비상승 |
| F 최종 검토 | review_final·verdict·Processor/Loop/Graph·checkpoint/출력 | accept/continue/block, 명시 활성·예산, 미승인 후보 final 공개 없음, 재개/취소/실제 효과 중복 없음 |

B의 순수 저장/해석 계약은 먼저 독립 검증할 수 있다. **외부 API 전환 완료는 B/C와 Hub 소비자가
모두 일관된 시점**으로 잡는다. 중간 제품을 성립시키기 위한 AgentComponent façade는 만들지 않는다.
D1의 직렬 fan-out 검증을 D2 실제 병렬 완료로 보고하지 않는다.

## A–F의 구체적 실행 계약

### A: 모델과 실제 Adapter

공통 message/content/tool definition/call/result/request/response/usage/종료/opaque 계약을 최소로 정의한다.
영속 Message와 실행 승인용 ToolCall snapshot의 의미를 구분한다. Provider kwargs는 선택 Adapter가
소유하며 Engine/Processor/Memory/RAG/Vision/worker/Hub는 SDK 필드를 해석하지 않는다.
SDK callable/decoder의 구 API 진입점은 정규 Adapter로 직접 이관한다.

signed reasoning·opaque state·Tool ID와 pair, 부분 args·finish 검증, unknown usage, 토큰 예약,
부분 응답 후 retry 금지, SDK retry 중첩 방지, admission·취소·진단을 유지한다.
RAG의 청크 ordinal/벡터·캐시·Job fingerprint와 Vision의 명시 OCR/VLM도 검증한다.

### B/C: Agent와 단일 실행 binding

Project.agents가 정의·revision·참조를 소유한다. Run은 agent_id와 실행 시 불변 증거를 기록한다.
Resume는 저장된 증거와 현재 실행 가능성을 검증하고 빠진 정책을 최신 정의로 메우지 않는다.
상위 제한은 하위에서 좁힐 수만 있다. Hub 제목 생성·일반 대화·자원 생성·Graph 위임은 명시
생성·선택한 실제 Agent를 사용한다. 요청 cwd/env와 turn provenance는 Agent 정의에 섞지 않는다.

### D: 요청 1:N과 병렬성

User.recipients를 접수 때 검증·동결한다. all은 명시 요청일 때 Session 참여자 snapshot으로
확장하며 누락을 all로 해석하지 않는다. 하나의 User에 N Run과 각 Assistant를 연결한다.
단일/다중 모두 같은 정규 결과 API를 사용하며 count에 따른 옛 1:1 반환 분기를 두지 않는다.

같은 Session·같은 Agent는 FIFO, 다른 Agent는 병렬이다. 별도 scheduler를 복제하지 않는다.
한 fan-out의 history snapshot을 고정하고 Run별 Processor·Tool scope·취소를 분리한다.
shared Project quota는 경쟁 중에도 원자적으로 예약한다. Session 상태는 전체 Run 상태에서 계산한다.

### E: Session 간 요청

source/target/agent/message·correlation/idempotency·deadline·result reference의 단일 현재 형식을 둔다.
ID를 안다는 이유만으로 다른 Session/Project의 입력·조회·취소 권한을 주지 않는다.
기존 admission과 transaction 또는 명시 outbox 증거로 전송·target 접수·링크를 연결한다.
즉시 반환 후 재조회, duplicate/cycle/cancel/target 삭제·변경을 검증한다. source의 승인·Tool 권한을
target으로 자동 승계하지 않는다.

### F: 최종 후보 검토

review_final은 accept/continue/block과 feedback/provenance/revision을 반환하는 명시 계약이다.
미설정은 문서화된 비활성이다. 활성 기능의 필수 예산·feedback bound가 없으면 거부하고 숨은 횟수를 만들지 않는다.
prepare → completion → after_completion → 후보 검토 → accept일 때 finish 순서를 사용한다.
continue는 같은 Run의 논리 iteration/Tool/usage/timeout 제한을 유지한다. verdict 우선순위는 한 owner가 선언한다.
현재 관찰 hook을 응답 변조/자동 재실행 API로 바꾸지 않는다. 검토 전 후보를 finalized Assistant로 공개하지 않는다.

## 안전성·실패 주입

| 경계 | 실제 관찰할 증거 |
|---|---|
| R3 `max_tool_calls=1` | root/nested binding·scope·checkpoint limit=1, 첫 실제 효과 1회·두 번째 handler/효과 0회. 필드 전달 누락 mutation에서 시험 실패 |
| 승인→Resume→재시작 | 승인 전 worker/handler 0, 결정·예약 이중 소비 없음, 완료 효과 재실행 없음 |
| 효과 직후/receipt 직전 crash | 외부 효과와 예상 process exit 확인, 새 프로세스에서 started/uncertain 보호·자동 replay 0 |
| Step+첨부 checkpoint commit | fsync/rename 실패 시 old/new 일관성, 두 성공 알림 부분 발행 없음 |
| plan/삭제·포기 경쟁 | stale revision·source 삭제·marker 모순 거부, 새 submit 우회나 원본 부활 없음 |
| fan-out·quota | ACK 전후·부분 생성 crash, 실제 동시 barrier·FIFO, 중복 Run/Tool 없음 |
| transfer | source/outbox/target/link 경계 crash와 재접속, key 충돌/cycle/권한 검증 |
| 최종 검토 | verdict 저장 실패·continue 뒤 승인/재시작, 후보 final 공개·효과 중복 없음 |

기존 os._exit/fsync crash·외부 파일 효과·원장·선택 binding 회귀를 예외 mock만으로 대체하지 않는다.
Resume는 새 Run이며 원본은 보존한다. Run 사용량, Project 기간 quota, 논리 Agent/Tool·retry 누적은
각자의 복원 의미를 유지한다. PendingWork 정리 전 ownership을 해제하지 않는다.

실제 인증 모델, 커널 short-write, 전원 손실·손상 디스크는 별도 미검증 항목이다.
실행 정책 우회·한도 소실·중복 효과·자료 손실 위험·미해결 핵심 회귀가 발견되면 다음 단계로 가지 않는다.

## CI 복구와 기록

main 감사 당시에는 원격 bwrap 실패·후속 Skip과 raw log 접근 Forbidden을 기록했다.
이후 [P0 검증](p0-sdk-transition.md)에서 패키지 bwrap에만 적용하는 AppArmor profile과
전역 제한 유지·일반 unshare 거부를 실제 CI로 검증했다. [P1-a 원격 검증](p1-verification.md)도
두 job 모두 통과했다. core, 실제 ish/X11, sandbox는 계속 필수이며 전체 test ID를 배정한다.
후속 커밋의 결과는 각 실행의 실제 증거로 확인하며 이전 성공을 승계하지 않는다.

네트워크 공유·비격리 강등·sudo로 실행 의미 변경·전역 보안 완화·Skip 확대·continue-on-error로 통과시키지 않는다.
network deny·mount/capability·승인 전 spawn 금지·process 종료·효과 후 crash 보호를 실제 제품 runner로 검증한다.
로컬 bwrap 성공은 GitHub 성공 증거가 아니다.

각 단계는 변경 파일/근거, 완료·미완료, 정확한 명령·exit·pass/fail/error/skip, 소스/host/Python SHA,
실패 주입 도달 증거·효과 수, 커밋/Push, 남은 위험을 기록한다. 실증하지 않은 동작은 통과로 보고하지 않는다.
