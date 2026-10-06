# Backend와 Application의 경계

Project → Session → Run → Step은 영속 소유 구조다. Engine은 실행 전략,
Component는 선택된 기능과 그 데이터를 소유한다. Hub는 생성 템플릿, 기본 선택,
모델·프롬프트 프리셋, 승인 UX와 개선 전략을 결정한다.

| 분류 | 소유자 | 예 |
|---|---|---|
| Contract | backend | JSON 형태, Tool 인자, 참조와 revision, 이벤트 |
| Invariant | backend | atomic/CAS, 정확한 evidence, 승인 binding, 권한 비상승 |
| Execution policy | 명시한 사용자/host, backend 집행 | timeout, retry, 허용 Tool, 보관 제한 |
| Product policy | Application | 무엇을 기억할지, 검색 전략, Skill 생성/분기 시점, 평가 기준 |

설정 부재를 제품 선택으로 바꾸지 않는다. 필요한 알고리즘 인자가 없으면 해당
기능 실행 시 configuration error를 반환한다. SDK 옵션 부재는 SDK에 위임한다.
UI의 effective values는 관찰 결과이며 새 사용자 설정으로 저장하지 않는다.

## 개발자 체크리스트

- Backend-owned contract는 기본적으로 닫고 unknown field를 거부한다.
- 각 설정의 semantic owner를 하나로 정한다. Parent는 child의 공개 계약만 사용하며 private 옵션을 해석하지 않는다.
- 교체 가능한 구현체가 자신의 설정을 검증한다. 고정 내부 helper를 위해 새 registry/adapter를 만들지 않는다.
- 열린 객체는 Application metadata, 선택 구현체/SDK/adapter 인자, 외부 결과 등 명시적 소유 경계만 허용한다. 열린 dict 자체가 설계 목표는 아니다.
- 제공하는 mechanism과 입출력 계약을 먼저 정의한다.
- 무결성 invariant와 조정 가능한 execution policy를 구분한다.
- 프롬프트/Skill의 내용은 권한이 아니다. Tool·MCP·RAG·Engine 권한을 늘리지 않는다.
- 모델 출력은 저장 전에 구조를 검증하며, 버전 확인과 승인은 별개다.
- host의 명시된 ceiling을 하위 설정이 완화하지 못하게 한다.
- 추천값·점수 임계값·자동 선택은 예제/Hub에 둔다.
- `tests/llm/audit_configuration.py`는 후보 수집기다. 검색 결과만으로 적합 판정하지 않는다.

## 감사 분류

Memory의 암묵적 keyword 검색과 양수 score 필터, Graph의 weight 우선 정렬,
직접 prompt_update Tool, 임의 파일/검색 개수 상한은 제품 선택으로 분류한다.
모델이 직접 영속 지침을 바꾸는 대신 Refinement 제안·평가·승인·CAS를 사용한다.

유지할 invariant: storage/schema version 1, 원자적 publish, CAS/checkpoint identity,
승인 receipt, 불확실한 효과의 자동 재실행 금지, 경로/심볼릭 링크 검사,
JSON/evidence 검증, IPC frame 형식, cancellation 전파와 미완료 작업 추적.
LiteLLM DEFAULT_MAX_RETRIES=0은 알려진 retry 호환성 문제를 막는다.
LITELLM_LOCAL_MODEL_COST_MAP=True는 초기화 외부 네트워크 접근을 격리한다.
Loop stream=True/n=1과 embedding no-cache/no-store는 실행/순서 계약이다.
이 값은 Project의 기본 설정이 아니며 사용자가 해제하는 옵션이 아니다.

## 상한과 정적 감사

감사 패턴은 기본값 함수, setdefault, 숫자/문자열 fallback, 생성자 기본값, schema maximum,
ORDER BY/score 비교, auto/fallback, 직접 영속 변경이다. 범위는 llm 전체 Python이며 테스트와
예제는 명시적 호출자로 구분한다. 후보 보고서는 tests/llm/reports 아래에 보관한다.

| 항목 | 판단과 현재 동작 |
|---|---|
| RAG limit 100 / hops 5 / relations 1000 | 제품 상한 제거. 명시한 양의 정수로 실행 |
| embedding concurrency 32 | 임의 상한 제거. 명시한 worker 수와 host ProviderCalls 제한으로 실행 |
| chunk_size 최소 64 | 품질 취향 제거. 알고리즘에 필요한 최소 1만 유지 |
| provider attempts 10 | 임의 상한 제거. 명시한 시도 수, SDK retry 중첩 방지 유지 |
| file_read 5000 / file_list 1000 / file_search 500 / web_search 20 | schema 제품 상한 제거. host 파일/출력/시간 한도는 유지 |
| Memory Tool 원문 slice 64000 | 임의 상한 제거. 명시적 페이지 크기만 적용 |
| Tool worker IPC 8 MiB / stderr 1 MiB | worker IPC frame/capture 계약. 양쪽 wire 경계에서 초과 실패, 무음 자르기 없음 |
| interaction renewal 128 | 임의 chain 길이 상한 제거. visited identity로 cycle 검증 유지 |
| 이름 64 / operation key 512 / hash 길이 | 식별자·경로·프로토콜 형태. 실행 품질 튜닝이 아님 |
| crop 좌표 4개 / Tesseract PSM 0..13 | 외부 프로토콜의 유효값 |
| read block 8192 / catalog LRU / index stride / queue buffer | 결과 의미를 바꾸지 않는 구현 sizing. queue는 backpressure |
| observability label 128 / code 길이 96 / cardinality 128 | 임의 자르기·other 합산 제거. 허용 fact 필드와 code 문자형식은 유지 |
| observability recent 256 | 비영속 최근 관찰 projection의 중립 캐시 sizing. 누적 count, sink, 도메인 원본은 보존 |
| Graph cleanup 5초 | 자동 생성 제거. 명시 기간만 대기, 미설정은 revoke/cancel 후 PendingWork로 소유권 이전 |
| Memory/Goal close_timeout 5초 | 제거. builtin의 aclose는 무자원 no-op, custom processor는 명시한 None/유한 양수 사용 |
| process cleanup 5초 | 제거. SIGKILL/process group 종료는 유지하며 OS 회수를 기다림. 명시 timeout만 적용 |

나머지 literal은 schema/JSON 구조, offset/counter 초기값, 삭제/추가 실행의 opt-in,
명시한 알고리즘의 계산식, 출력 형식 계약, SDK 오류 분류, 버전/CAS, 중립적인 no-policy로
분류한다. Memory keyword의 `score>0`은 명시 선택한 알고리즘의 단어 매칭 조건이다.
host search_fn에는 이 필터를 적용하지 않는다. RRF 1/(k+rank)는 알고리즘이며 k는 필수 입력이다.
JSON mode fallback은 사용자가 auto를 선택한 경우에만 수행한다. 조회에서 삭제 자료를 숨기거나
maintenance를 preview로 시작하는 것은 비파괴 API 계약이며 제품 추천값을 저장하지 않는다.

## 변경 계약과 남은 선택

- Memory 검색 전략/추출 의미 prompt가 없으면 필요한 동작에서 설정 오류다. 자동 migration 없음.
- Graph API는 weight 대신 support_count를 반환한다. 기존 DB/JSON 값은 자동 변환하지 않는다.
- Agent inline prompt는 유지하며 참조 Prompt/Skill 버전은 기존 Step binding에 남긴다.
- Refinement의 Agent update는 purpose/description만 허용한다. resources.skills는 별도 bind_skills다.
- CREATE/FORK는 Skill만 지원하며 target 부재, 부모 CAS, lineage와 평가·승인 경계를 검사한다.
- require_evaluation은 기록 존재를 요구한다. 평가 결과의 합격 판정은 host가 소유한다.
- trusted Python CRUD는 관리 API다. 모델에게 내보낼 때에는 ToolPolicy/Refinement 경계를 사용한다.
- Skill의 열린 resources/추가 JSON은 데이터다. 이를 실행 권한으로 재해석하는 커스텀 소비자는
  자체 권한 검증을 구현해야 한다. 기본 AgentNode는 그 필드를 권한으로 읽지 않는다.

Application은 사용할 검색 전략·분할 크기·동시성, 기억할 사실의 범위, Agent 구성,
허용 proposal_operations, 평가자/회귀 기준, 승인 UX를 명시해야 한다. backend는 추천 점수,
자동 Skill 생성/분기 기준, 온보딩 템플릿, 모델 judge를 정하지 않는다.

## Graph와 Tool의 명시적 권한 경계

GraphEngine은 durable Workflow orchestration engine이고 WorkflowComponent는 정의 저장/
구조 검증을 소유한다. 같은 환경은 workflow 노드, 다른 Graph/handler 환경 선택은 Graph Agent다.
Graph Agent의 purpose/engine/engine_options만 실행 의미를 가지며 behavioral field는
GraphEngine.for_agent에서 리소스 조회 전에 거부한다. Tool scope를 새로 만들지 않고 부모 것을
전달한다. Workflow 노드의 JSON 입출력 계약과 Run/Step 이벤트 저장 경로는 유지한다.

Host가 ToolPolicy.argument_constraints를 지정하면 원본 schema와 교집합을 모델에 보여주고
ToolExecutor에서 다시 검증한다. fixed만 생략 인자에 **명시된 host 값**을 주입한다.
bounded/selectable은 값 추천이나 default를 만들지 않는다. child는 좁힐 수만 있고
effective 제약은 승인/checkpoint binding에 들어간다. [사용법](tool-constraints.md).

Memory 추출은 검색 전략 없이 record ID 순서로 실행한다. Skill dependencies는 Agent와
child lineage를 각각 원본 정의에서 조회하며 직접 삭제/CREATE·FORK rollback 모두 참조를
거부한다. 자동 cascade, migration, 승인 preset, 새 영속 도메인을 추가하지 않는다.
