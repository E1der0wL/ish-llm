# 초대·관측 캐시·진행 단계 계약 보강

2026-10-26 작업 기록. 기존 Agent UI와 초기 연결 단계 표시 변경을 포함하여 소비 경로를
점검했다. 제품 `llm/`은 수정하지 않았다. 이 기록은 전체 제품의 모든 동작에 대한 보증이
아니며, 아래 변경과 검증 범위를 구분한다.

## 수정과 Owner

| 대상 | 수정 | 권한과 소비 경계 |
|---|---|---|
| Agent 초대 | 역순으로 초대해도 정렬된 unique ID 저장, 기존 회원 재초대는 멱등 | `Session.agent_ids` 검증·저장은 backend, 표시 이름은 `AgentProfile`, Hub는 공개 Session API만 사용 |
| 완료 기록 조회 | 고정 FIFO 대신 현재 표시 대화의 Run 관측 유지, 삭제·세션 이동 시 정리 | backend 기록이 원본, `SnapshotReader`는 재생 가능한 조회 캐시만 소유 |
| Graph 관측 | Loop 완료 기록의 불필요한 Step 조회 제거, Graph 별칭도 mode로 판별 | `GraphStepMetadata`/`GraphScope`/`WorkflowGraph` 원본 계약 → `WorkflowObservation` → UI record |
| Graph 증거 | 필수 값 누락을 Step 이름·빈 graph로 대체하지 않음 | 단일 display adapter만 JSON text와 node 상태 투영. 실행·재개 권한 없음 |
| 진행 단계 | 명령 이름 접두어 추측 제거, 선언된 operation/action별 매핑 | `OperationProgress` → `BackendProgress` → `CommandCallbacks.stage` → UI loop → 기존 한 줄 `TaskProgress` |
| UI 선언 | sidebar 기본값, trace fence/inset, graph trace 식별자는 기존 해당 Owner에서 선언 | `TagBar`, `ParticipantBar`, `TranscriptLayout`, `PresentationFormat`, `MessageKey` 재사용 |

백엔드의 Session worker, 승인·취소·재개 정책, 영속 파일 형식과 UI 조작 방식은 변경하지
않았다. 초대는 idle 상태에서 runtime을 분리해 저장하고 다시 시작하며, 실행 중인 작업을
중단하여 초대하지 않는다. Graph 원본이 없으면 표시할 graph가 없는 것으로 처리하지만,
Graph 원본이 있는데 필수 증거가 없으면 정상 graph로 복구하지 않는다.

캐시의 크기는 현재 대화 이력에 비례한다. 전체 이력 순회 비용과 메모리가 상수가 된다는
뜻은 아니다. active Run의 Step 관측은 계속 갱신되며 완료 이력만 재사용한다.

## 개별 감사

`docs/llm/contract-coverage-reviews.json`에는 변경된 Hub 파일별 선언·Reader·Writer·동적
소비 경로와 테스트 근거를 기록했다. 기존 llm 리뷰는 그대로 유지했다. 기존 후보는
symbol/role/value와 실제 변경 경로를 대조하고, 신규 후보는 선언·Python 타입/지역 참조
표현을 구분했다. 통과를 위한 일괄 면제나 실패 assertion 삭제는 하지 않았다.

인덱스는 Linux/Git 체크아웃의 LF 바이트 기준으로 기록한다. Windows CRLF 파일을 같은
내용이라도 SHA 비교에서 자동으로 무시하는 예외는 검사기에 추가하지 않았다.

## 검증

집중 시험 50개가 통과했다. 역순 초대·재시작 후 회원 유지·전체 수신, 1,025개 이력의
반복 Run/Step 읽기 방지, 삭제 후 캐시 정리, 누락 Graph 증거, 진행 단계 분류와 UI loop
전달을 포함한다. 실행 기록은 `tests/hub/reports/ish-hub-contract-_3bx4e_x/focused.txt`이다.

최종 통합 검증은 Linux 파일시스템의 별도 스냅샷에서 Python 3.12.14와 고정 의존성,
CI의 ish host revision `d833790959a76ac457fdeea3f21a6bea1eecfe12`로 수행한다.
스냅샷 원본/LF hash, 명령과 원래 exit, 전체 테스트 및 정적 감사 결과는
`tests/hub/reports/ownership-review/`에 보존한다.

### 최종 결과와 증거 재사용

| 실행 | 결과 |
|---|---|
| Hub 전체 발견·실행 | 발견 609, 실행 600, 통과 596. 시간 표시 fixture 4개에 필수 `Run.engine` 누락, X11 class setup 실패로 9개 미실행. 원래 exit 1과 오류 기록 보존 |
| Hub 최종 영향 범위 16개 모듈 | 117/117 통과. 위 13개와 완료 Step 캐시·Graph·다중 Agent·승인/재개·UI 전달 회귀 포함. 실패·Skip·누락·중복 0 |
| Hub 범위 대조 | 전체 발견 ID 609개 모두 통과 근거 존재. 기존 전체 실행을 성공으로 바꾸거나 두 실행을 하나의 전체 실행으로 표현하지 않음 |
| llm 최종 영향 범위 11개 모듈 | 143/143 통과. 계약/아키텍처/문서/의존성, 실제 host loader, Hub를 소비하는 공개 API와 checkpoint/장기 작업 시험 포함 |
| 기존 llm 전체 근거 | `tests/llm/reports/tool-scope-20261025/full.json`의 2,510/2,510 통과를 영향 없는 범위에 재사용. 이번에 전체를 새로 실행한 것은 아님 |
| 환경·문서·S1–S7 | 모두 exit 0, 문서 오류·미검토 아키텍처 후보 0 |
| 계약 전체 감사 | 643파일, 15,305후보. 미분류·stale·미완료 계약·동적 경로 미검토·위반 0 |

기존 llm 근거와 현재 소스를 비교한 `backend-equality.json`은 제품 379파일과 예제
30파일이 byte-identical임을 확인한다. llm 시험 258파일 중 변경은 개별 아키텍처 리뷰
fixture뿐이며, docs/llm 변경은 계약 리뷰·인덱스뿐이다. Python 고정값, 의존성 lock,
pyproject, host 입력도 동일하다. 달라진 감사 자료와 Hub 소비 경로는 위 143개에서
재실행했다. 전체 발견 ID 목록도 기존 2,510개와 동일하다.

Hub 전체 실행 이후 제품 변경은 `SnapshotReader`의 선택된 terminal Run Step 캐시다.
해당 경로는 최종 117개에서 다시 검증했다. 실행/일시정지 상태에서는 매번 최신 Step을
읽으며, 완료 상태에서는 반복 조회가 없는지 직접 검사했다. 시간 표시 fixture에 실제
필수 필드를 추가했으며 기존 시간·실패·누락 이력 assertion은 유지했다.
X11은 기존 격리 Xvfb/xclip 경로와 공유 라이브러리 경로를 사용했다. 제품 기능이나
필수 시험을 끄지 않았고 시스템 패키지·사용자의 데스크톱 클립보드는 변경하지 않았다.

명령은 `python -m tests.llm.run_regression --suite hub`의 전체 실행 후 정확한 모듈 목록을
지정한 `--modules` 재검증, `--suite llm --modules` 영향 범위, `check_environment`,
`check_docs`, `check_architecture --require S1 ... --require S7`,
`check_contract_coverage --require-complete`를 사용했다. 실제 인자와 결과는
`validate_full.py`, `hub.json`, `hub-recheck.json`, `llm-affected.json`, `summary.json`에 있다.
`snapshot.json`과 `snapshot-final.json`은 첫 전체 실행과 최종 보강의 차이를 보존한다.

현재 작업은 로컬 변경·검증까지이며 commit, push, 원격 CI는 수행하지 않았다.
