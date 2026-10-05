# Goal · 작업 상태 요약 · Refinement 구현 기록

2026-10-05. 실행 소유 계층과 Runtime의 책임은 변경하지 않았다.
`Project → Session → Run → Step`은 실행 원본이며 Goal/요약/Proposal은 Project의 보조 리소스다.

## 소유권과 계약

| 기능 | 소유자 | 하지 않는 일 |
| --- | --- | --- |
| 목표·진행·Run 연결 | GoalComponent/GoalData | Run 생성/종료/재개, 과거 Run 수정 |
| 모델 입력 압축 | MemoryProcessor/MemorySession | 원본 대화·Tool Step·checkpoint 변경 |
| 개선 제안·적용 영수증 | RefinementComponent/RefinementData | 숨은 모델 실행, 별도 승인/효과 재시도 |
| 승인·Tool 실행 | 기존 ToolExecutor/Interaction/Run | Component 내부로 책임 이전 |

공통 ComponentData에는 related/reference와 저장소 바인딩만 추가했다. 같은 Project와 선택된
컴포넌트인지 검사하며 기존 SessionManager/RunRepository/StepRepository를 사용한다.
RunManager는 이를 연결할 뿐 Goal/Refinement를 import하거나 해석하지 않는다.

## 변경 파일

추가:

- `llm/components/goals/{__init__,component,data,processing,tools}.py`, `README.md`
- `llm/components/refinement/{__init__,component,data,tools}.py`, `README.md`
- `llm/components/memory/work_state.py`
- `tests/llm/test_goals.py`, `test_work_state.py`, `test_refinement.py`
- 이 문서

수정:

- `llm/llm.py`: 제공 가능한 컴포넌트 등록과 API 설명. 선택/활성화는 호출자 소유.
- `llm/services/lifecycle/{components,projects}.py`, `llm/services/runtime/runs.py`: 공유 저장소 연결.
- `llm/components/memory/{component,data,processing}.py`: 형식·조건·Goal 참조·캐시 검사.
- `tests/llm/test_memory_processing.py`: 단위 fixture에 실제 EngineContext의 run.id/completion_policy 반영.
- `llm/README.md`, `llm/components/README.md`, `llm/components/memory/README.md`
- `docs/llm/{architecture,completion-processing,memory-processing,handoff}.md`

Core 도메인 모델, Engine 실행 전략, provider, ToolExecutor, 승인 영수증, ConversationStore의
저장 방식은 변경하지 않았다. Hub 코드도 변경하지 않았다.

## Goal

Project/Session scope, CRUD, version CAS, link/unlink Run, pause/resume/complete를 제공한다.
한 Run이 여러 Goal에 연결될 수 있고 존재하지 않거나 다른 Project의 참조는 거부한다.
읽기 Tool과 선택적 승인 대상 쓰기 Tool을 분리했다. 참고자료 주입은 명시적인 inject/priority,
중첩 Agent는 nested_agent_ids를 요구한다. clone은 Project 목표만 복사하고 Run 참조는 비운다.
자세한 API는 [Goal README](../../llm/components/goals/README.md).

## Compaction

기존 Memory 처리 경로에서 `summary_format="work_state"`를 선택할 수 있다. 문자열 content를
유지하며 작업 상태·출처·summary_format_version=1을 메타데이터로 제공한다.
대화의 ID 범위/coverage hash와 Tool 호출 ID/Run 참조는 코드가 기록한다.
긴 대화의 모든 ID를 파생 파일에 복제하지 않는다. 원본은 대화/Step에서 다시 조회한다.

토큰 조건은 실제 전체 요청 계산기를 사용하며 없으면 명시적 문자 조건으로 판단한다.
임의 토큰 추정은 없다. 최종 CompletionPolicy 검사는 유지한다. Goal 참조 버전 변경은
캐시를 무효화하고 모델 호출 도중 변경되면 publish를 거부한다. failure_mode=continue는
degraded Step을 남기고 원문을 유지한다. 최근 턴·현재 요청·steering·미완료 Tool 쌍은 보존한다.
설정과 제약은 [Memory 처리](memory-processing.md).

## Refinement

단일 대상 proposal을 먼저 저장한 뒤 validate/approve/apply로 진행한다. 모델 분석은 일반
Run/Tool이 수행하며 source에 분석 Run/Step을 연결한다. before/expected_version/evidence를
보존하고 대상 공개 API만 사용한다. 대상 변경과 proposal 상태는 같은 workspace transaction이다.

버전이 바뀐 target/rollback은 거부한다. 저장 오류는 기존 상태를 보존하고 호출자에게 전달한다.
`fail`은 UI/검증자가 제안을 명시적으로 종료하는 API다. Memory 적용은 candidate/replaces를
만들어 기존 review/consolidation에 넘긴다. 자동으로 확정 기억을 덮어쓰지 않는다.
자세한 API와 승인 계약은 [Refinement README](../../llm/components/refinement/README.md).

## 기존 기록과 호환성

새 컴포넌트는 선택한 Project에만 디렉터리를 만든다. 기존 Project 설정에 새 값을 채우거나
Goal 필드를 Run에 추가하지 않는다. Domain storage_version과 Workflow schema_version은 1이다.
기존 문자열 요약 API는 유지한다. 요약 profile 지문이 바뀌면 파생 요약을 원본으로부터 다시
계산하며 기존 저장 데이터를 일괄 변형하지 않는다. migration/legacy reader/별칭은 추가하지 않았다.

## 회귀 검사

새 테스트 23개는 다음을 검증한다.

- Goal 범위·CRUD·복수 참조·CAS·clone·재선택·미선택 동작·참고자료 주입·중첩 격리.
- 구조화 상태·출처·원본 보존, Goal 버전 무효화와 호출 중 충돌, 잘못된 모델 응답의 fallback,
  전체 요청 token counter와 명시적 char fallback, 미완료 Tool/steering 보호.
- Skill/Prompt/Agent 적용과 rollback, immutable proposal/evidence, 승인 이전 변경 금지,
  stale apply/rollback, 저장 실패 시 대상/영수증 동시 복원, Memory candidate/review/consolidation,
  cross-Project/권한 변경 거부, reopen/clone, 실제 ASK → resume 적용 1회.
- Goal → Tool Run → compaction → 실패 Run → 분석/제안 Run → 승인/Skill 변경 → 다음 Run 통합.
  원본 대화·과거 Run/Step을 비교하고 다음 Run에만 변경된 Skill이 사용됨을 확인한다.

기존 처리기·승인·재개·추가 지시·취소·Tool worker·저장 복구·설정 검사는 전체 suite에서 수행한다.
최종 명령과 결과는 [handoff](handoff.md)의 해당 날짜 항목을 참고한다.

## 한계

요약/제안의 사실성·효과는 모델 품질과 근거에 달려 있다. JSON/schema/CAS 검사는 의미적
정확성의 대체가 아니다. 실제 사내 모델 품질 및 수 시간 운용은 별도 실사용 검증 대상이다.
이번 검사는 fake model 응답으로 경계와 영속성을 재현했으며 외부 유료 모델은 호출하지 않았다.
자동 on-finish refinement, 복수 대상 변경, 새 UI 화면/명령, 의미 검색 기반 Goal 선택은 범위 밖이다.
Refinement clone은 원본 승인을 복사하지 않으며 새 Project에서는 새 제안을 만들어야 한다.
