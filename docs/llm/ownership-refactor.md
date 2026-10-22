# 계약 소유권과 책임 경계의 후속 정리

## 상태와 범위

2026-10-22 `refactor/llm-clean`의 `488acbe9ffa12a89410cbf74179cdc890e3357a5`를 기준으로 한다.
최초 갱신은 [확정된 계약](contract-ownership.md)과 작업 목록을 문서화했다. 이후 사용자 요청으로
구현을 진행 중이며 [단계별 구현·검증 기록](ownership-verification.md)에 실제 결과와 남은 게이트를 남긴다.
최초 문서 검증 결과는 [갱신 기록](documentation-review.md)에 보존한다.

H1–H5 → P1 → A–F의 기존 기능·안전성 검증 기록은 보존한다. 그 결과를 의미 있는 리터럴과
책임 경계의 전수 정리 완료 증거로 승계하지 않는다. 기존 완료 안내에서 이 범위의 판단이
충분하지 않았다. 이번 구현은 아래 확인된 중복과 추가 공통 계약을 정리했으며,
최종 통합·원격 CI 게이트 결과는 별도로 기록한다.

구현 기준은 현재 작업 브랜치 하나다. main·refactor/llm-backend·Draft PR #1은 변경하지 않는다.
기존 작업을 초기화하거나 main에서 다시 시작하지 않는다. 과거 브랜치는 필요한 알고리즘·
실패 사례만 읽기 전용으로 참고하며 merge·무검증 cherry-pick·구형 계약 복원은 하지 않는다.

## 확인된 항목과 조사 후보

### 고정 구조 데이터 후속 보강 TC1–TC3

기준 `38e18566c8ea60143601bff161f27c37775c4d6b`. O0–O7와 Agent Skills 전환의 기능·안전성
통과는 보존하지만, 새로 명시한 [고정 구조 데이터 계약](contract-ownership.md)의 전체 적용
완료로 승계하지 않는다. 이번 변경·검증은 [TC 검증 기록](typed-contracts-verification.md)을 따른다.

| 단계 | Owner·실제 소비 경로 | 범위·완료 기준 |
|---|---|---|
| TC1 | `ModelUsageManifest` → 모든 Component 초기화/읽기/갱신, Skills `_state/usage` | 동일 저장 JSON, 누락·버전·identity·digest 거부, 변경 API와 중첩 사본 보호. 영수증·예산 의미와 쓰기 순서 보존 |
| TC2 | `FileEvidence`/`SkillFileEvidence` → Data 파일 digest·Skills 저장/import/export; `SkillSnapshot`/`views` → Skill Tool·resource 조회 | 필드 선언에서 Schema/직렬화 파생, 원문·파일 모드·revision 보존. Tool handler의 고정 dict 조립/조회 제거, 독립 wire·잘못된 입력·사본 시험과 S7 음성 시험 |
| TC3 | TC1–TC2 통합·전체 소비자 | 공통 사용량/파일 저장 영향이므로 Level 3 필수. 전체 LLM/Hub·R3·정적·문서·실제 환경 및 정확한 SHA 원격 CI |

추가 전환 대상은 별도로 남긴다. 이는 허용 예외나 구현 완료 목록이 아니다.

- `Component`의 일반 정의 inventory와 `SkillStorage`의 운영 catalog/entry: 현재 저장 Owner 내부
  Reader/Writer이며 소비자에 노출하지 않지만, 선언·생성·검증을 명시적 타입으로 더 묶을 수 있다.
- `SkillData._target/impact/dependencies`, `SkillComponent.references`, `ProjectReferences`:
  Component snapshot/삭제 참조의 공개 dict를 직접 해석·조립한다. 참조 프로토콜 Owner와
  전체 Component·Agent 삭제 소비자를 함께 전환해야 하며 Skills 전용 상수로 대체하지 않는다.
- 다른 Component Tool·서비스/Hub의 고정 응답과 설정 snapshot: 기존 O0–O7 후보·Owner 근거를
  재사용하여 실제 변경 경로별로 타입/Reader/Projection 연결을 검증한다. 이번 TC 범위 밖이다.

TC3 통과도 저장소 전체 dict 전환 완료가 아니다. 위 대상은 필드/상태의 같은 철자만으로
공통화하지 않으며 각 계약의 전체 소비 경로와 Level 1–3 증거를 갖춰 후속 완료한다.

아래는 이번 질문에서 출발한 초기 목록이며 전체 발생 목록이 아니다. 문자열 접근 자체만으로
모두 위반이라고 분류하지 않는다. 확정된 중복과 소유·호출 관계를 더 확인할 항목을 구분한다.

| ID | 근거·대상 | 현재 판정과 필요한 작업 |
|---|---|---|
| OW-01 | `OutputJournal.__init__`, `RunRepository.__init__`, `BackendServices.output_index_stride` | **구현·집중 검증 통과, O7 로컬 전체 통과; 원격 CI는 Commit/Push 기록 참조**. 동일 기본값 128을 세 곳에서 선언. 인덱스 Owner의 기본값을 공유하고 명시 설정·0 비활성·검증 의미 보존 |
| OW-02 | `Project.validate_conversation_storage`, `services/schema.py:describe_project_config` | **구현·집중 검증 통과, O7 로컬 전체 통과; 원격 CI는 Commit/Push 기록 참조**. file/memory/None 허용값을 검증기와 Schema에서 각각 선언. 수명주기 속성 Owner 지정, 실제 저장소 선택 소비자도 대조 |
| OW-03 | `ProjectManager.describe_config`, `ProjectHandle._describe_config`, `services/schema.py` | **구현·집중 검증 통과, O7 로컬 전체 통과; 원격 CI는 Commit/Push 기록 참조**. 설정 스냅샷·편집 값·Schema를 별도로 조합. 공개 설명/Projection Owner 지정과 공통 선언 파생 범위 결정. 원본·사본·CAS·effective 값 비저장 보존 |
| OW-04 | `RunRepository.result_catalog`, `_usage_record`, `completion_records`; `CompletionResult`와 usage 소비자 | **구현·집중 검증 통과, O7 로컬 전체 통과; 원격 CI는 Commit/Push 기록 참조**. 결과·사용량 부분 투영과 metadata 컨테이너 접근의 책임 점검. CompletionResult 검증 위임·필수 컨테이너·중복 ID 거부 유지 |
| OW-05 | `tests/llm/check_architecture.py:scan_source` | **S7 보강·음성 시험 통과, 동적 감사 계속**. 기존 문법 규칙이 OW-01/02와 위 Project/Run 메서드의 일반 키 접근을 후보로 잡지 않음. 후보 수와 계약 적합성을 구분하고 참조·기본값·필드 집합·소비 경로 감사 보강 |
| OW-06 | 신규·중첩·재개·다중 Agent의 binding/승인, Hub 정책 소비, Adapter 경계 | **Owner 연결·집중 검증 완료, O7 로컬 전체 통과; 원격 CI는 Commit/Push 기록 참조**. ToolRegistry binding Projection·AgentBinding·Resume/Checkpoint envelope·오류 분류를 공통화. 승인/예산/효과는 실행 경계에서 같은 Owner로 최신 검증 |
| OW-07 | `tests/llm/fixtures/architecture_reviews.json`의 clean-refactor.md 참조 70건 | **159건 개별 재검토 완료, 현재 통합 게이트 대기**. 과거 개별 분류를 삭제·자동 갱신하지 않고, 강화된 계약과 해당 발생/선언/소비자를 대조해 유지·수정·미해결을 판정. 현재 정적 게이트 상태는 문서 갱신 기록에 별도 보고 |

각 항목은 해당 파일의 현재 심볼에서 조사하며 줄 번호를 고정 계약으로 삼지 않는다.
`RunStatus` 참조, Owner 내부의 필수 기록 검증, 진단용 ContextVar 이름 등은 의미에 따라 판정한다.
필수 기록 누락을 빈 컨테이너로 복구하거나 unknown usage를 0으로 만드는 변경은 허용하지 않는다.

## 작업 순서와 산출물

아래 표의 현재 상태와 [검증 기록](ownership-verification.md)을 함께 본다. 실제 안전성 결함이 발견되면 수정과
재검증을 우선한다. 각 소단계는 영향이 입증된 범위로 분리하고 관련 소단계를 상위 게이트로 묶는다.

| 단계 | 작업 | 산출물·완료 판단 | 현재 상태 |
|---|---|---|---|
| O0 범위·Owner 목록 | 기존 감사·38개 데이터군·검증 기록 재사용. OW-07의 근거 재검토와 변경·미분류 범위의 선언/검증/저장/소비 연결·책임 혼재 조사 | 계약별 구체적 Owner·실제 소비자·확정 중복·조사 후보·검증 범위. 기존 근거를 재사용하고 필요한 범위만 재검토 | OW-01–07·추가 공통 계약의 Owner/소비 연결과 기존 감사 대조 완료. 정적 미검출 경계는 기록 |
| O1 선언·제약 | OW-01/02부터 기본값·허용값·필드 집합·경로의 정규 선언 연결. 한 번 쓰는 계약 값도 포함 | Schema·Reader/Writer·소비자 연결, 명시값·M/N/V·현재 형식 보존 시험 | 기본값·종류·경로·필드 선언 및 직접 소비 연결 완료, Level 1/2 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O2 설명·조회·저장 경계 | OW-03/04의 공개 설명과 Projection Owner 확정. Manager/Facade의 private 저장 해석을 담당 Owner로 이관 | 필요한 조회 투영과 명시 변경 API, 사본·CAS·필수 증거·unknown usage·조회 비용 검증 | 영수증·설정 설명·Hub view·Resume/Restart Projection 구현과 Level 1/2 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O3 도메인 전이·검증 | 도메인별 중복 규칙과 내부 직접 수정 조사. 선언·전이·검증 구현 공유, 경계별 재검증 유지 | 실제 중복의 제거 또는 정당한 차이의 근거, 저장·실행·재개 최신 상태 검증 | 정책·RAG·Agent binding·Checkpoint·Workflow 제약 정리 및 Level 1/2 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O4 실행·자원 경계 | 신규/중첩/Resume/다중 Agent의 binding·승인·예산·retry·효과·취소·cleanup 경로 대조 | 같은 계약의 동일 Owner 호출, 경로별 차이·불변식·작업/자원 수명·원자성 증거 | ToolRegistry binding 공통화·신규/중첩/재개 경계 대조, 관련 안전성·R3 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O5 Hub·Adapter·확장 | Hub의 Backend 정책 재계산·오류 문자열 해석, SDK wire 유출, 내장 구현 전제 조사/정리 | 공개 계획·구조화된 오류·공통 모델 소비, custom Repository/Engine/Component/Adapter 계약 검증 | 공통 오류 분류·표시 종류·설정 view·Adapter Schema·확장 소비 정리 및 Level 1/2 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O6 감사 도구 | OW-05의 누락 패턴과 책임 경계 감사를 작은 규칙으로 추가. 각 구현 단계부터 관련 규칙·음성 사례도 함께 보강 | 정상 Owner 선언/참조와 외부 재선언을 구분하는 회귀, 기존 S1–S6/R3 유지, 동적 미검출 범위 명시 | S7·S6와 음성 시험 보강, 159건 개별 대조 후 현재 137건 정적 검토 통과; O7 로컬 전체 통과; 원격 CI는 검증 기록 참조 |
| O7 통합 완료 | 남은 후보·Owner/소비 연결·영향 범위를 확인하고 현재 통합 소스의 Level 3 실행 | 전체 LLM/Hub·정적 감사·R3·문서·필요 실환경, Push SHA의 실제 CI와 미검증 범위 기록 | 로컬 전체 LLM/Hub·R3·정적·문서·환경 통과. 커밋 작성 시점에는 정확한 Push SHA 원격 CI 확인 전 |

O1–O6의 관련 소단계를 묶는 게이트와 O7에서는 Level 3를 수행한다. 공유 저장·승인·예산·효과·
공개 계약 변경의 영향이 제한됨을 입증하지 못하면 소단계에서도 Level 3로 승격한다.
각 단계의 전체 완료 선언에는 현행 검증 정책의 상위 게이트 조건을 적용한다.

O0에서는 기존 목록에 변경분만 추가한다. 한 Owner에서 시작해 선언과 실제 소비자를 끝까지
연결한 뒤 다음 범위로 이동한다. 파일 수를 줄이거나 거대한 공통 클래스를 만드는 것을 목표로
삼지 않으며, 독립적인 계약을 겉모양만으로 합치지 않는다. 새로운 호환 계층도 만들지 않는다.

## 구현 중 보호할 계약

- 원본 요청·저장 설정과 명시적 SDK 재시도/주입 클라이언트 계약을 보존한다. Backend
  max_attempts 생략은 기존 RetryPolicy의 총 1회, SDK 재시도 생략은 Adapter 전송 경계의 0회다.
  명시 SDK retry와 Backend retry 중첩 금지, 부분 응답 이후 자동 retry 금지·취소·사용량을 유지한다.
- 상태 전이·Writer/Reader·승인·binding의 공통화 후에도 저장·실행 직전·Resume에서 최신
  원본·revision·만료·취소·권한·예산·Checkpoint·효과 영수증을 확인한다.
- 잠금·CAS·트랜잭션·예약·확정·외부 효과의 현재 순서를 보존한다. 승인 전 효과 0, 불명확한
  효과 재실행 금지, 원본 소실 시 fail-fast, 실제 중단 후 원본 또는 완전한 새 상태를 검증한다.
- 캐시·조회 Projection·오류 진단·UI 계획은 권한 원본이 아니다. 구조화된 결과를 제공하면서
  실행 판단을 중복하거나 전역 캐시로 최신 검증을 생략하지 않는다.
- 호출 한도·예약·누적 사용량은 기존 scope를 보존한다. Resume·삭제·재시작을 이유로 보호해야
  할 예산을 초기화하지 않으며, 서로 다른 Run/Project/논리 호출 scope를 무조건 합치지도 않는다.
- 외부 SDK·HTTP·저장 형식의 독립 Fixture와 실패 재현 Assertion은 보존한다. 과거 분류·성공
  결과를 새 소유권 규칙의 통과 증거로 자동 승계하지 않는다.

## 검증과 기록

실행 절차의 정규 기준은 [검증 정책](verification-policy.md)이다. 새 테스트 실행 체계나
고정된 전체 테스트 수를 만들지 않는다. 소단계 기록에는 그 정책의 보고 항목과 함께 다음을 남긴다.

- 작업 ID·계약·변경 전후 Owner 심볼·선언과 실제 소비자·이관한 의미/검증/변경 책임.
- 발견 범위와 미분류·확정 위반·Owner의 적법한 선언·비계약 표현을 구분한 판단 근거.
- 선언·허용값·필수 필드·공개 Projection의 일치, 직접 소비자와 확장 구현, 실행 경로별 검증.
- 실제 명령·exit code·발견/실행/통과/실패/오류/Skip·누락/중복, 소스/환경 동일성, 실패 증거.
- Level 3 수행 또는 유예 이유, 남은 위험·다음 통합 게이트·commit/Push/정확한 SHA의 CI 상태.

Level 1/2는 영향받는 Owner와 소비자에 집중한다. 실제 파일 효과·장애 주입·재개/삭제 안전성·
R3 호출 한도 검증은 해당 영향 범위에 유지한다. 안전성 실패가 남거나 계약 충돌로 새 결정이
필요하면 후속 기능으로 넘어가지 않고 근거와 선택지를 보고한다.

변경 문서에 고정된 정적 검토 hash가 있으면 해당 발생의 의미와 근거를 재검토한 뒤 필요한
항목만 갱신한다. 일괄 면제·자동 baseline 갱신·검사 비활성화로 통과시키지 않는다. 정적 후보의
개별 분류는 전체 책임 경계 검증을 대신하지 않는다.

검증된 소단계는 독립 추적하고, 관련 소단계의 전체 게이트 성공 뒤 작업 브랜치에 Push한다.
GitHub Actions의 전체 LLM/Hub 필수 Job은 유지하고 실제 결과를 확인해야 원격 통과를 선언한다.
이번 문서 갱신은 제품 소단계나 O7 전체 검증의 완료가 아니다.
