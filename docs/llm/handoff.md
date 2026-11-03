# 최신 작업 인계 — 2026-11-04

## 미검토 파일 후속 보강·조회 최적화

명령 시작 후 첫 결과를 넘기기 전에 취소·조회 실패가 발생하면 자식 프로세스를 회수하도록
보강했다. Run 참조 검사는 Session이 선택한 Conversation을 사용한다. 메모리/custom 저장소의
생존 참조도 무결성 증거이며, 백업 검증은 archive owner가 별도 store를 명시한다. 파일 저장소는
캐시 적중 시에도 symlink를 거부한다. 주입 저장소의 연결 계약은
[서비스 확장 계약](service-extensions.md)에 명시했다.

문자 페이지 결과·출력 설정/binding·입력 영수증 종류는 각 Owner 선언을 소비하도록 정리했다.
문자 조회는 전체 hash/UTF-8 검사를 한 번의 순회로 묶고, Tool 요약 목록은 한 잠금 작업 안에서
검증한 inventory를 재사용한다. 승인·명시적 재개·Tool 효과 중복 방지, 저장 형식과 설정 소유권은
유지했으며 숨은 기본값이나 자동 migration은 추가하지 않았다.

`0b02fb7706b57b23bf194fe25b208a8c2d8d3603`의 Hub와 현재 LLM 변경을 분리한 Linux Python
3.12.14 검증에서 집중 **109/109**(Hub 공개 API 24 포함)를 통과했다. LLM 전체는
**2,859 통과 / 1 실패 / 오류·skip 0**이다. 실패는 변경 전 HEAD에도 존재하는 Hub 감사
기록 12개의 해시 불일치다. 이 기록을 임의 승인하지 않았으며 전체 suite 통과로 보고하지 않는다.
제품·테스트 Python 636개가 검증 소스와 일치한다. Hub·Dok의 별도 미커밋 작업은 이 결과의
대상이 아니며, Hub 전체와 원격 CI를 다시 실행한 결과도 아니다.

동일 Linux 데이터의 7회 중앙값으로 2.6MB 파일 5문자 조회는 **114.26→10.97ms**,
Tool 요약 20개 목록은 **217.08→100.13ms**, inventory 검사는 **21→1회**다.
이는 개별 조회 측정이며 모델 도달 시간이나 Hub 전체 지연을 뜻하지 않는다.
원자료와 실패 근거는 `tests/llm/reports/unreviewed-fixes-20261104/results.md`에 보관한다.
S1–S7 strict 검사는 통과했으나 전체 파일 계약 감사의 미검토 항목은 여전히 별도 과제다.

## 비동기 Provider·파일 취소 — 후속 검증

기본 LiteLLM 스트림을 `acompletion`으로 연결하고 요청·읽기·닫기를 같은 Task에서 관리한다.
취소 뒤 정리가 끝나야 admission을 반환한다. 직접 주입한 동기 호출은 기존 worker 경로를
유지한다. 기본 파일 Tool은 준비·읽기·검색·복사의 블록 사이에서 취소를 확인하고, 게시가
시작되면 fsync와 결과 정리를 마친다. 승인·명시적 재개·불확실 효과 재실행 금지 계약과
Run 전체 중단을 유지한다. 숨은 제한이나 저장 버전 변경은 없다.

main `ec424e17551f3d46a988516547354746c45feb71`에 이번 변경만 적용한 독립 소스에서
Linux Python 3.12.14로 LLM 전체 **2,851/2,851**, 집중 검사 **123/123**를 통과했다.
집중 검사에는 Hub 공개 API 연결 **51개**가 포함된다. 실패·오류·skip·누락·중복은 모두 0이다.
Hub 제품 코드와 다른 미커밋 Hub·Dok 변경은 건드리지 않았으며, 이번에 Hub 전체를 다시
실행한 것은 아니다. 제품·테스트 Python 소스의 검증 후 일치를 확인하고 문서·감사 근거만
갱신했다. 원자료는 `tests/llm/reports/cancellation-20261103/`에 보관한다.

상세 보장과 한계는 [방향 전환 평가](direction-changes.md)에 있다. 원격 서버의 계산 중단,
동기 SDK/OS 작업의 강제 종료까지 보장하지 않는다. 기존 전체 계약 소유권 감사의 미검토
129개는 남아 있으며 회귀 통과를 전체 감사 또는 원격 CI 성공으로 해석하지 않는다.

## 안전한 실행 중 방향 전환 — 최종 업로드 검증

영속 지시 접수 뒤 선택한 invocation만 취소하는 `redirect()`, 미선택 지시의 철회·대체를
추가했다. Run/저장 소비자 Task와 승인·효과 영수증 owner는 유지한다. 완료 Tool 결과는
재사용하고, 취소된 효과가 불확실하면 자동 반복하지 않는다. 새 Run 상태·숨은 설정·저장
버전 변경·자동 migration은 없다. 지원 범위는 [방향 전환 평가](direction-changes.md)에 있다.

main `e3d38fea04a01a312f1bc8636432311ab7d5d2d4` 기준으로 llm과 관련 문서·테스트만 분리한 소스를
Linux Python 3.12.14에서 다시 검증했다. LLM **2,839/2,839**,
Hub **1,050/1,050**, 집중 검사 **58/58**가
통과했다. 각 실행의 실패·오류·skip·누락은 0이다. 다른 미커밋 Hub·Dok 작업은 이 결과의
대상이 아니다. 원자료는 `tests/llm/reports/steering-release-20261103/`에 보관한다.

전체 계약 소유권 감사의 미검토 129개는 별도 잔여 작업이다. 최신 업로드 후보의
stale/violation/issue는 0이지만 CI의 `Check complete contract ownership` 완료 조건은
충족하지 않는다. 아래에는 이전 단계의 검증 결과와 조건을 보존한다.

## 검증 호스트 기준 — ish 1.2.0

사용자 결정에 따라 현재 검증 기준은 ish **1.2.0**, commit
`1459abbc271e272e74c909d0696e938113f07d97`이다. CI와 테스트 잠금의 참조 소스를
이 commit에 맞췄다. 로컬 `ish.platform/` 내용도 해당 릴리스와 일치함을 확인했다.
잠금은 기존 136개 패키지 버전을 제약으로 재생성했으며 버전 변경 없이 설치 환경 검사도 통과했다.
아래 과거 단계에 기재된 호스트 revision은 그때의 기록으로 보존한다.

## 최종 전체 회귀 — ish 1.2.0

Linux Python 3.12.14, Linux 파일시스템의 해시 확인 소스, private `/tmp`·`/dev`의
Xvfb 환경에서 다시 실행했다. 최종 LLM **2,772/2,772**, Hub **985/985**가 각각
전체 suite 실행으로 통과했다. 실패·오류·skip·누락·중복은 모두 0이다.
LLM 1,569.062초, Hub 1,305.072초이며 성능 벤치마크 수치가 아니다.

1.2.0에서 이름이 바뀐 Host 내부 함수를 로더 테스트에 반영했다. OS 셸을 시작하지 않는
공유 UI fixture에는 실제 자동완성기의 경로 공급자만 명시했고 기존 호출 형태를 유지했다.
LLM 통과 후 바뀐 파일은 이 Hub fixture 하나뿐이다. 제품 코드·LLM 테스트가 동일함을
확인한 뒤 Hub 전체를 재실행했으며, 개별 실패 테스트 결과를 합산한 통과 주장이 아니다.
최종 소스 manifest 1,410개와 작업 파일의 일치를 확인한 뒤 이 결과 문서만 갱신했다.
원자료는 배포하지 않는 `tests/llm/reports/final-feature-regression-20261102/`에 보관한다.

문서 209개와 S1~S7 strict 검사는 오류 없이 통과했다. 별도의 전체 파일 계약 감사는
721개 중 **107개 미검토**, stale/violation/issue 0으로 유지한다. 이는 회귀 통과와
다른 조건이며 CI의 `Check complete contract ownership` 완료 조건은 아직 충족하지 않는다.
미검토 항목을 일괄 승인하거나 CI 조건을 완화하지 않았다.

## Hub 기능 확장과 음성 연결

Workflow 무효과 초안 검사/편집, Tool 사이 추가 지시, Graph 경로 예약, 출력 근거 조회,
이미지 팝업, 로컬/서버 음성 연결을 llm·Hub에 구현했다. 계약은
[interactive-contracts.md](interactive-contracts.md), 사용법은
[Hub 기능](../hub/interactive-features.md)에 있다. 로컬 검증 원자료는
`tests/llm/reports/feature-expansion-20261101/results.md`에 보관하며 배포에 포함하지 않는다.

음성 입력/출력은 독립 선택이며 혼합할 수 있다. 모델 다운로드나 서버 선택은 자동으로 하지 않는다.
기존 ProviderCalls/Host 진단 설정, Project 정책, 승인·명시적 재개·효과 영수증을 사용한다.
호출자 key 접수 영수증은 기존 ConversationStore transaction에 함께 기록하며 별도 원장을 만들지 않는다.
storage/schema version과 재시작 시 무조건 재실행하지 않는 계약은 유지한다.

앞선 단계의 Linux 3.12.14 전체 실행에서는 LLM 2768/2771 통과, Hub 970/975 실행 통과였고 X11 9개는
환경 초기화 문제로 미실행이었다. 발견된 소스 감사 근거·Skill 메타데이터·UI 배치/기대값을
수정한 뒤 영향 범위 64/34/19개 및 격리 X11 9개를 통과했다. 새 두 검사를 포함한 현재
3757개 전체 항목의 통과 근거를 ID로 확인했다. 이 앞선 결과를 전체 무실패 실행으로 해석하지 않는다.
이후 실제 전체 재실행 결과는 위의 최종 전체 회귀 절을 따른다.
승인 재개 예산을 제거하는 mutation은 두 실제 효과 검사에서 탐지됐다.

전후 각 8회 동일 로컬 HTTP fixture에서 backend 도달 중앙값 0.589→0.558초,
fsync 4회로 동일했다. 저장 변동이 있어 개선의 인과 관계나 실제 음성 지연을 주장하지 않는다.
실제 음성 모델/서버/장치와 Kitty 수동 QA는 별도로 필요하다. 과거 단계 기록은 아래에 보존한다.

## 순수 스키마 재사용과 준비 조회 병합 — 최신 후속 단계

후속 후보 1·2번 중 안전한 범위만 적용했다. `ParameterLayout`은 같은 schema/path 내용의
순수 변환 결과 하나를 재사용하고 반환값을 분리한다. 입력이 변경되거나 JSON 이외의 확장값이면
다시 계산한다. 현재 설정값·승인·권한·사용량 판단이나 구현체 hook을 생략하지 않는다.

요청의 읽기 전용 사전 검사와 runtime 조회는 한 `StorageIO` 호출로 묶었다. QUEUED 접수
transaction 안의 최종 검증은 독립적으로 유지한다. 한 context를 만드는 동안 같은
Run 정책 snapshot을 반복해서 해석하던 부분도 한 번으로 줄였다. 취소 경계가 다른 준비 hook,
외부 효과와 저장 확정은 합치지 않았다. 이 단계의 Hub production 변경은 없다.

측정 조건은 Linux Python 3.12.14, native filesystem, SDK/Session 사전 준비, 실제 LiteLLM과
로컬 HTTP fixture다. backend submit → 서버 수신을 전/후 각 16회 비교했다.

| 항목 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| 중앙값 | 0.812초 | 0.788초 |
| 평균 | 0.904초 | 0.975초 |
| 범위 | 0.664–1.505초 | 0.577–2.794초 |
| 호출 전 fsync | 4회 | 4회 |

중복 계산/StorageIO 진입 감소는 집중검사로 확인했지만 **전체 지연 개선은 입증하지 못했다.**
모든 표본을 보존했고, 과거 다른 부하에서 측정한 0.601초와 직접 비교하지 않는다.
원본과 검증 이력은 `tests/llm/reports/preparation-reads-20261031/results.md`에 있다.

전체 회귀는 Linux Python 3.12.14에서 새로 실행했다. 첫 전체 시도는 WSL 종료/재시작으로
미완료였고, 같은 snapshot으로 다시 실행한 결과는 LLM **2,743 통과 / 3 실패**, Hub
**918 통과 / 1 실패**였다(skip·누락·중복 0). 실패 네 건은 개별 정적 검토 근거 누락,
준비 중 기록을 소유권 없이 직접 조회한 테스트 두 건, 기존 환경 준비 호출을 세지 않은
Hub mock 기대값이었다. 조회 테스트와 Hub 실패는 최적화 전 snapshot에서도 재현했다.

검토 기록/테스트만 수정한 뒤 관련 LLM 모듈 **86개**, Hub 모듈 **17개**가 모두 통과했다
(실패·skip 0). 원래 실패 로그를 보존했고 전체 suite가 한 번에 모두 통과했다고 기록하지
않는다. 전체 실행 후 제품 코드 hash는 동일하다. 접수 최종 검증, 승인·명시적 재개·효과 중복
방지, 취소와 저장 경계는 유지했다. 이번 단계에서 Hub 제품 코드 수정·커밋·업로드는 하지 않았다.

## 실행 준비와 실행 허용 결합 — 이전 단계

기존 미커밋 상태를 기준으로 binding 준비와 첫 모델 Step/호출 예약을 묶었다.
`prepares_execution` / Adapter의 `pure_preparation`은 Host 구현의 명시적 무효과 준비 계약이며
사용자 설정값이 아니다. 미선언 구현, MCP, 입력 정책/counter 및 외부 selector/processor에는
필요한 강한 경계를 유지한다. 승인·명시적 재개·Tool 효과 영수증의 기존 owner와 저장 버전은 같다.
서비스가 임의 Engine 이벤트를 앞당겨 실행하지 않으며 기존 공개 Step/Completion 관찰은
결합 저장을 확정한 뒤 각각 발행한다. 이번 후속 단계에서 Hub 코드는 수정하지 않았다.

Linux Python 3.12.14, native filesystem, SDK/Session 사전 준비, 실제 LiteLLM/로컬 HTTP fixture에서
backend submit → 서버 수신을 전/후 각 16회 측정했다. 전체 회귀와 측정을 동시에 실행하지 않았다.

| 항목 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| 평균 | 0.720초 | 0.601초 |
| 중앙값 | 0.717초 | 0.576초 |
| 호출 전 fsync | 6회 | 4회 |
| 프로세스별 첫 요청 2회 | 0.718 / 0.720초 | 1.046 / 0.544초 |

평균 16.4% 단축이다. 긴 첫 표본도 포함했으며 모든 요청이 0.8초 아래라고 보장하지 않는다.
Hub 전체/사내 endpoint/차가운 SDK 초기화의 측정값이 아니다. 원본과 상세 결과는
`tests/llm/reports/execution-groups-20261031/results.md` 및 같은 폴더의 trace/manifest에 있다.

단계별 집중 검사: 44, 243, 124, 52개 모두 통과(각 실패/skip 0).
중복 제외 LLM 337개와 Hub 공개 API 검사 26개다. 최종 Python 소스 hash가 측정본과 일치한다.
초기 신규 timeout 검사 1개는 오류 문구 가정이 틀려 실패했고, 안정적인 오류 코드 검사로
수정한 뒤 통과했다. 원래 실패 보고서도 보존했다.
이전 단계에서는 전체 LLM/Hub 회귀를 속도 결과를 보고 사용자가 결정하기로 하여 실행하지 않았다.
커밋·업로드도 이번 요청에는 포함하지 않았다.

## 저장 확정과 Hub 준비 경로 — 이번 작업

기준 commit은 `e9fc844c78c219ef51742411d95d6aaa12bd66ba`다. 이번 작업은 아직
커밋하지 않았다. 저장 계약은 [storage-transactions.md](storage-transactions.md)에 정리했다.

- Run 시작과 실행 진입 허용 증거를 한 journal 확정으로 묶었다. 실제 권한·기한 검증은
  실행 직전에 유지한다. 살아 있는 scheduler가 미진입을 확인한 취소만 명시적 restart의
  기존 조건을 유지하며, 프로세스 재시작 후 Step 부재로 안전성을 추측하지 않는다.
- Activity의 비권한성 append를 핵심 기록과 같은 frame에 보존한다. 핵심 파일 다음에
  반영하고, 파생 인덱스 장애가 실행 성공을 바꾸지 않는다. 부분 append는 복구 시 중복하지 않는다.
- Hub는 명시적 셸 환경별 Toolkit 자원을 준비한다. 실제 Tool binding·현재 권한·승인은
  재사용하지 않는다. 추가 지시 변경은 Run·대화만 갱신하며 승인·종료·손실 재조정은 유지한다.
- 새 timeout·retry·캐시 크기·flush 간격을 만들지 않았다. QUEUED 접수와 실행 허용,
  승인·Tool 효과 영수증은 여전히 영속 확정 뒤에만 후속 실행을 허용한다.
- Skill의 같은 준비 작업에서 반복하던 inventory/참조 검사를 줄였다. public resolve hook은
  유지하며 외부 파일 변경과 Data/Tool 참조 revision은 다음 준비에서 다시 검사한다.
- admission JSON의 순수 해석은 같은 동기 준비 scope의 동일한 JSON 내용에 대해서만 재사용한다.
  Message 소유권·상태·Run 연결 및 현재 실행 권한은 캐시의 판단 대상이 아니다.
- 초기 checkpoint·지시 채널 OPEN·첫 SELECT를 한 저장 단위에 묶었다. 실제 pending 선택은
  서비스가 수행한다. 첫 선택 이후 processor 준비 중 도착한 지시는 다음 경계에서 처리한다.
  중첩 checkpoint envelope·Tool 사용량·재개 선택은 기존 형식을 유지한다.

Project/Session/Run/Step과 승인·checkpoint의 저장 버전은 유지한다. 내부 record manifest에는
파생 append가 추가되므로 기존 프로세스를 정상 종료한 후 배포한다. 미완료 구 journal을
자동 변환하거나 버리는 호환 경로는 없다.

현재 작업의 재현 자료는 `tests/llm/reports/request-boundaries-20261031/`에 있다.

### 준비 기록과 실행 허용의 저장 경계

후속 절충안은 dispatch, 초기 OPEN과 부분 출력을 `prepare_records()`로 준비한다.
최신 단계는 명시적 준비 계약의 실행 binding도 이 경로에 포함한다.
완전한 journal frame을 flush하고 잠금 안의 조회 view에 함께 공개하지만, 아직 원본 파일을
덮어쓰지는 않는다. 따라서 공개 조회는 최신 내용을 볼 수 있고 전원 장애로 최근 준비가
유실될 수 있다. Run 시작·외부 실행 경계·Step 실행·승인·예약·receipt의 `run_records()`가 선행
준비까지 fsync한 뒤 원본 파일에 반영한다. Run 종료·pause·마지막 소유권 해제 역시 강하게 확정한다.

Step보다 먼저 외부 hook을 호출하는 Engine은 공통 `execution_boundary()` ACK를 기다린다.
Loop의 외부 tool selector/입력 처리기도 이 경계를 사용한다. Engine 종류별 저장 예외나
배경 flush worker, 임의 대기 간격은 없다. 대화 캐시의 공개 후 stamp도 같은 준비 view를 따른다.
실시간 조회는 소유권을 사용하는 공개 API를 거쳐야 하며, 외부의 직접 파일 읽기는 늦을 수 있다.

이번 후속 작업의 측정 원본은 `tests/llm/reports/request-boundaries-20261031/prepared-records-comparison/`
아래에 있다. 해당 단계의 전체 실행은 사용자 요청으로 중단되었으며 성공으로 간주하지 않는다.
최신 후속 단계의 집중검사/측정 결과는 위 절을 따른다.

### 절충안 이전 단계의 속도 측정

`reuse-selection-results.md`와 원본 trace에 변경 전/후/반복 표본을 모두 남겼다.
Linux Python 3.12.14, native Linux filesystem, 실제 fsync, 로컬 HTTP fixture이며
앱·Session 사전 준비와 모델 답변 생성 시간은 제외한다. 사내 endpoint 검증은 아니다.

| 백엔드 submit → 모델 서버 | 변경 전 | 변경 후 | 같은 변경 후 재측정 |
|---|---:|---:|---:|
| 첫 요청 | 0.857초 | 0.679초 | 1.064초 |
| 8건 중앙값 | 0.876초 | 0.969초 | 0.837초 |
| 범위 | 0.792–1.151초 | 0.679–1.270초 | 0.706–1.140초 |
| 호출 전 fsync | 8회 | 7회 | 7회 |
| fsync 시간 중앙값 | 0.505초 | 0.415초 | 0.435초 |

Hub의 첫/다음 짧은 입력은 2.073/2.422초 → 2.127/1.844초였다. 읽기 작업은
2.888/3.514초 → 2.255/2.375초였으나 긴 입력 표본은 더 느린 경우도 있었다.
호스트 부하를 통제하지 못했으므로 총 지연 감소율을 일반화하지 않는다. fsync 1회 감소는
반복 확인했지만 모든 요청의 1초 미만 도달을 보장하지는 않는다.

아래 항목은 이번 작업 전의 검증 이력이다.

## 이전 작업 — 요청 준비 경로 정리와 검증

기준 commit은 `a99dbe888a6c35774ae8df6b4b11ee8feeab73b4`이며, 이번 변경은 아직
커밋하지 않았다. 사용자는 필요한 Hub 연결 수정을 허용했고, 최종적으로는
안전하게 제거할 불필요한 연산이 더 없으면 1초 미만이 아니어도 완료하도록 정했다.

- Native Provider 초기화에서 선택된 SDK의 completion 관련 모듈을 미리 준비한다.
  실제 모델 요청·외부 네트워크 호출·사용자 설정 생성은 하지 않는다. 필수 SDK import
  오류는 전달하고, 선택적 사전 import 실패는 실제 호출 경로의 SDK 판단에 맡긴다.
- Tool 등록 자료는 변경 전까지 공유하고 노출·수정 시 분리한다. 사용자 정의
  `register`/`copy`와 Session `load` hook, 실제 handler 검증은 계속 호출한다.
- 동일 schema의 구문 검사와 마지막 Project/Agent 내용의 구조 해석만 재사용한다.
  파일 존재·원문 hash·현재 정책·권한·참조 검사는 유지하고, 반환되는 수정 가능한 값은 분리한다.
- Runtime 준비 시 다음 저널과 Activity의 빈 파일 이름을 준비한다. 빈 파일은 기록이나
  승인 영수증이 아니다. 실제 journal frame과 Activity 기록은 기존 `fsync`를 유지한다.
- 필요한 정책 section만 검사하고 같은 잠금 범위의 중복 Project 조회 및 경로 계산을 줄였다.
  Hub 준비 표시의 timer는 표시되는 시간·재시도 문구가 바뀔 때 갱신하며 실제 이벤트는 즉시 반영한다.

저장 형식, 설정 우선순위, 승인·명시적 재개·Tool 효과 영수증 계약은 변경하지 않았다.
검토한 요청 준비 경로에서 추가로 안전하게 제거할 연산은 찾지 못했다. 전체 저장소나
모든 입력 규모에서 최적이라는 의미는 아니다.

### 최종 측정

Linux Python **3.12.14**, Linux 파일시스템, 실제 `fsync`와 LiteLLM→로컬 HTTP fixture를
사용했다. 양쪽 모두 Adapter 초기화와 `session.run.start()` 이후의
**Backend submit → 모델 서버 요청 수신**을 측정했으며 모델 생성 시간은 제외한다.

| 동일한 8개 요청 | 기준 코드 | 최종 코드 |
|---|---:|---:|
| 첫 요청 | 3.535초 | 1.356초 |
| 중앙값 | 1.299초 | 1.193초 |
| 범위 | 1.091–3.535초 | 1.079–1.356초 |
| 요청 도달 전 fsync | 주로 11회 | 10회 |

최종 측정은 1초 미만이 아니다. 초기화 비용을 미리 수행하며 없애는 것은 아니고,
호스트 부하·실제 저장 장치·입력 규모에 따라 시간이 달라진다. 이전 반복 측정은 중앙값
1.071–1.104초였으나 이를 최종 측정으로 대체하지 않는다. 별도의 Hub 입력 fixture에서는
첫/다음 요청 도달이 2.525/2.590초였고, 앱 준비에는 22.969초가 걸렸다.

### 검증 결과

| 실행 | 실제 결과 |
|---|---|
| 전체 LLM (`ish-provider-pg4sx9ua`) | 2,705개 실행: 2,702 통과, 검토 기록 검사 3개 실패, skip 0 |
| 기록 갱신 후 architecture/contract 재검증 (`ish-provider-hnevaddw`) | 31 통과, 실패·skip 0 |
| 전체 Hub (`ish-provider-hnevaddw`) | 907개 실행: 897 통과, UI 대기 검사 1개 오류, X11 의존성 미설치로 9 skip |
| UI 대기 기준 보완 후 관련 검사 (`ish-provider-9cew4252`) | 22 통과 |
| private X11 재검증 (같은 최종 소스) | 9 통과, 실패·skip 0 |

검토 기록은 실제 변경을 재검토하여 hash/표현 위치를 갱신했고 검사 규칙·예외는 늘리지 않았다.
UI 오류는 설정 로딩 완료에 키 입력용 3초 제한을 사용한 검사였다. 연결·schema 조회만
기존 `backend_ready`로 기다리며 키 입력·자동완성 제한과 검증 내용은 그대로다.
변경 전 코드도 처음 연결에서 기존 10초 제한을 넘는 것을 확인했다.

WSL에는 CI의 Xvfb/xclip 의존성을 추가했다. WSLg의 공유 `/tmp/.X11-unix`는 수정하지
않았다. X11 검사는 Bubblewrap의 private `/tmp`와 network namespace, 표준 `/dev`·`/proc`
마운트 안에서 별도로 실행해 실제 데스크톱 클립보드와 분리했다.

제품 소스 hash와 test ID를 대조해 **LLM 2,705개 + Hub 907개 모두의 통과 결과를 확보**했다.
기록·테스트만 수정한 뒤 전체를 재실행한 것은 아니다. 원래 전체 실행의 오류·skip 기록은 보존했다.
상세 조건·한계·실행 흔적은
로컬 전용 `tests/llm/reports/request-preparation-20261031/review.md`와 같은 폴더의
`verification.json`, 각 snapshot의 `snapshot.json`/결과 JSON에 있다. 보고서는 자동 발견·배포에서 제외된다.
실제 사내 모델/외부 endpoint 및 원격 CI는 실행하지 않았다.

---

# 이전 작업 인계 — 2026-10-26

## Loop 공개 프롬프트 설정 제거

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
작업 시작 시 존재하던 변경을 보존했다. 이번 실행 코드 변경은
`llm/engines/loop/configuration.py`, `llm/engines/loop/engine.py`에 한정된다.
Hub 제품 코드는 수정하지 않았고 관련 Hub 테스트 두 파일의 이전 설정 예시만 갱신했다.

- Loop의 공개 `config`는 `buffer_size`만 제공한다. Project/Session 및 Agent
  `engine_options.config.system_prompt`는 검증 오류이며 자동 이관·alias는 없다.
- 시스템 지침은 참조 Prompt → Agent의 인라인 `system_prompt` → 선택 Skill 순서로
  구성한다. Loop는 Agent 지침을 실행 입력으로 사용하고 Engine effective configuration에
  복제하지 않는다. missing/null/빈 문자열은 인라인 지침을 만들지 않으며 선택한 자료는 유지한다.
- `_configure()`의 프롬프트 상태와 Project/Session 프롬프트 상속 경로를 제거했다.
  구성된 지침은 기존 실행 binding에 계속 포함한다. 승인 후 지침이 달라지면 재개를 거부하며
  정상 재개는 완료된 모델 응답·Tool 영수증을 재사용한다. 저장 형식 버전은 바꾸지 않았다.
- `bleach_research`, `developer_assistant` 예제를 Agent 지침 경로로 수정했다.
  사용자 지침이 없는 경우 예제 코드도 임의의 지침을 채우지 않는다.
- Graph의 `cleanup_timeout`은 변경하지 않았다. 취소한 노드의 정리를 명시된 시간만 기다리고
  미완료 작업은 PendingWork가 추적하는 Graph 설정이다. Loop에 같은 옵션을 추가하지 않았다.

### 이번 검증 결과

Linux Python **3.12.14**, Linux 파일시스템 사본에서 실행했다. 겹치는 suite는 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 초기 지침·설정·장시간 실행 focused | 58 | 0 | 0 | 0 |
| Agent·Graph·승인·설정 focused | 162 | 0 | 0 | 0 |
| 전체 LLM — 예제 보완 전 | 2,558 | 0 | 1 | 0 |
| 예제 보완 후 Skill·Agent·명시적 설정 재검증 | 46 | 0 | 0 | 0 |
| Hub 공개 API·설정 UI 연결 — 9개 모듈 | 51 | 0 | 0 | 0 |

전체 LLM은 2,559개를 누락 없이 실행했고 **2574.10초**가 걸렸다. 유일한 오류는
`test_skill_tools`의 예제 Project 생성이었다. JSON 예제에 남은
`parameters.engines.loop.config.system_prompt`를 새 schema가 거부한 것으로 확인했다.
예제 JSON을 `agent.system_prompt`로 옮기고 예제 실행 코드에 명시적 전달을 연결했다.
실제 예제 요청이 그 지침을 모델 입력에 전달하는 회귀도 추가했다. 이후 해당 모듈과 Agent·설정
검사 46개가 모두 통과했다. **예제 보완 후 전체 suite를 다시 실행한 결과로 표기하지 않는다.**
전체 실행 이후 Backend Python 코드는 변경하지 않았다. 최종 LLM discovery는 새 예제 테스트를
포함해 2,560개다. Hub 전체 suite는 이번 작업에서 실행하지 않았다.

문서, S1–S7 architecture 및 계약 감사도 통과했다. 감사 대상은 649개 제품 파일,
15,522개 후보이며 누락·위반은 0건이다. 실제 사내 모델/외부 endpoint나 원격 CI는 실행하지 않았다.
커밋·업로드는 이번 요청 범위에 없다.

사본: `/home/user/.cache/ish-agent-prompt-lpof8mya`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트: `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout.
보고서: `tests/llm/reports/agent-prompt-20261026/`의 `llm.json`, `example-fix.json`,
`hub-public.json`, `focused.json`, `verified.json`. `llm-initial-source.json`은 전체 실행 당시
사본 해시를 보존하고 `snapshot.json`은 최종 파일 해시를 담는다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/agent-prompt-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --modules tests.llm.test_skill_tools tests.llm.test_agent_models tests.llm.test_explicit_configuration --output /tmp/agent-prompt-examples.json
```

## 스키마 식별자 충돌 보강

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
작업 시작 시 존재하던 Hub 변경을 그대로 보존했다. 이번 제품 코드 변경은
`llm/core/schema.py`에 한정되며, 기존 Engine binding과 실행 검증을 완화하지 않았다.

- 익명 소유 스키마에 부여하는 식별자를 결정적인 계층형 URI로 변경했다.
  서로 다른 소유자가 `inner.json` 같은 상대 `$id`를 사용해도 각각 자기 범위에 속한다.
  생성 URI는 스키마 조합 정보이며 사용자 설정·저장값·네트워크 요청 주소가 아니다.
- 공통 `checked_schema()`에서 상대 ID를 해석한 뒤 동일 resource/anchor 주소의 충돌을
  검사한다. 같은 정규 JSON 선언은 재사용 가능하지만 다른 선언은 주소와 두 위치를 포함한
  오류로 거부한다. 명시한 `$id`를 임의로 바꾸지 않는다.
- `$ref` 문자열·공유 객체·재귀 및 dynamic reference 의미를 보존한다. 실제 스키마만 순회하고
  `const`·`enum`·예제 데이터는 해석하지 않는다. 특정 Engine/Adapter 예외는 추가하지 않았다.

원래 실패를 수정 전·후 비교로 재현했다. 두 익명 스키마의 상대 child ID가 충돌하면 수정 전에는
정상 입력을 거부하고 잘못된 입력을 허용했다. 수정 후에는 정상 입력만 허용한다.
회귀 8개를 추가했으며 기존 설정 소유권·승인·명시적 재개·Tool 효과 재사용·저장 형식은 유지한다.
상세 계약은 [Agent 스키마 설명](agents.md)을 따른다.

### 검증 결과와 남은 항목

Linux Python **3.12.14**, Linux 파일시스템 사본에서 실행했다. 서로 겹치는 suite는 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 스키마 focused | 36 | 0 | 0 | 0 |
| Agent·Graph·승인·설정 focused | 159 | 0 | 0 | 0 |
| 전체 LLM | 2,555 | 0 | 0 | 0 |
| 전체 Hub | 616 | 0 | 4 | 0 |
| Hub 실패 4건 개별 재검사 — 수정 후 | 2 | 0 | 2 | 0 |
| 같은 4건 비교 — 수정 전 스키마 코드 | 3 | 0 | 1 | 0 |

전체 LLM은 **2738.72초**, 전체 Hub는 **1954.52초**다. Hub 오류 4건은
`tests/hub/support.py::eventually()`의 3초 제한 초과다. `test_component_managers`의
초기 `view.connected` 대기 3건과 `test_retry_observations`의 Tool backoff 관측 대기 1건이다.
스키마 충돌/검증 예외는 없었다. 같은 제한을 유지한 개별 재검사에서는 두 건이 통과했으며,
수정 전 `schema.py`를 넣은 별도 실제 디렉터리 사본에서도 초기 연결 timeout이 재현됐다.
시간 민감성이 확인됐지만 환경 부하·테스트 순서 등 상세 원인을 확정하지 않았다.
**Hub 전체 통과라고 보고하지 않는다.** Hub 제품·테스트와 timeout 값은 수정하지 않았다.

최초 수정 전 비교 사본은 Hub assets를 symlink로 연결해 소스 링크 금지 계약에 걸렸다.
그 결과는 비교 근거에서 제외하고, 실제 디렉터리 사본으로 재실행한 결과만 위 표에 기록했다.
처음 X11 사전 점검은 `/usr/bin` 경로에 도구가 없어 실패했으며, 기존 테스트 전용 X11 경로와
라이브러리 경로를 명시한 환경에서 전체 Hub와 별도 비교를 실행했다. 시스템 설치는 변경하지 않았다.

문서 검사와 S1–S7 architecture 검사를 통과했다. 계약 감사는 649개 제품 파일,
15,524개 후보를 대상으로 누락·위반 0건이다. 테스트 코드의 기대값을 완화하거나
사용자 기본값·자동 migration·호환 alias를 추가하지 않았다.

검증 사본: `/home/user/.cache/ish-schema-collision-u2wt1mym`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트는 CI 고정 commit `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout이다.
보고서·수정 전 비교·해시는 `tests/llm/reports/schema-collision-20261026/`에 보존한다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/schema-collision-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /tmp/schema-collision-hub.json
```

1,221개 검증 파일의 LF 정규화 해시가 작업 소스와 일치했다. 이번 인계 문서만 검사 후 추가하고
문서 검사를 다시 통과했다. Hub 코드·테스트·문서·예제는 작업 시작 시점과 동일하다.
외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다. 이번 요청에는 commit/push가 없어
작업 트리에 변경을 남긴다.

---

아래는 이전 단계 기록이다. 현재 검증 결과는 위 표를 따른다.

## Agent 스키마 참조 합성 보강

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
앞선 Engine별 Agent 폼 구현을 유지하면서 다음 공통 합성 경계를 보강했다.

- `core/schema.py::nest_schema()`는 소유 스키마의 resource 범위를 보존한다.
  기존 `$id`와 참조를 그대로 사용하고, 참조가 있는 익명 스키마에는 결정적인 Schema resource
  식별자를 부여한다. 빈 `$id`(`""`, `"#"`)도 익명으로 취급한다. 참조 문자열을 수정하지 않아
  공유된 스키마 객체의 이중 변환이 사라진다. 외부 reference와 schema default는 계속 거부한다.
- `engines/registry.py::describe_agent()`는 등록 Engine 이름 조건을 루트 `allOf`에 추가한다.
  원본 `properties.engine`의 위치·참조·기존 const·boolean schema를 변경하지 않는다.
- 추가 회귀에서 공통 외형 합성의 공유 `allOf` 목록 변형도 재현했다.
  `EngineAgentBinding.describe()`의 목록 추가 한 줄을 복사 후 조합으로 변경했다.
  `EngineAgentBinding.bind()`의 검증과 `for_agent()`의 의미 검증은 그대로 유지한다.

Schema resource 식별자는 UI/검증용 구조 정보이며 사용자 설정·저장 Agent 값·외부 조회 주소가
아니다. 특정 Engine/Adapter 예외, 이전 검증 결과 재사용, 사용자 기본값, 호환 alias 또는
migration은 추가하지 않았다. 승인·재개·Tool 영수증과 저장 형식도 변경하지 않았다.
자세한 조회·조합 계약은 [Agent 문서](agents.md)를 따른다.

### 최종 검증

Linux Python **3.12.14**, Linux 파일시스템 사본에서 새로 실행했다. suite는 서로 겹치므로
합산하지 않는다. 아래 전체 LLM 결과는 최종 코드와 테스트를 모두 포함한다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| 최종 Agent schema focused | 28 | 0 | 0 | 0 |
| 전체 LLM | 2,547 | 0 | 0 | 0 |
| 전체 Hub 실행 시도 | 61 | 1 | 105 | 0 |

전체 LLM에는 승인·명시적 재개·Tool 효과 재사용·중첩 Agent/Graph·required scope·Provider·
RAG·저장 복구 검사가 포함된다. 실행 시간은 **2143.22초**다. S1–S7 architecture 검사,
647개 제품 파일/15,449개 후보의 계약 감사, 문서 검사(177개 문서/165개 Python 예제)도 통과했다.

Hub는 기존 `hub/backend/project/title_values.py:14`의 `LoopParameters.COMPLETION` 참조 때문에
실패했다. 103개는 test module import 실패로 내부 테스트가 실행되지 않았고, 추가 오류 2개와
로더 실패 1개도 같은 import 오류다. Hub 제품·테스트 코드와 Backend 호환 API를 수정하지 않았다.
따라서 Hub 전체 연결이나 UI 렌더링까지 검증되었다고 판단하지 않는다.

추가 공통 목록 테스트의 최초 fixture에는 자체 `engine_options`를 처리하는 `for_agent()`가
없어 한 번 실패했다. fixture의 소유자 검증을 구현한 뒤 28개 focused와 전체 LLM을 통과했다.
이를 통과시키기 위해 제품 binding 검증을 완화하지 않았다.

최종 사본: `/home/user/.cache/ish-schema-references-nu05_xd7`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
참조 호스트는 CI 고정 commit `d833790959a76ac457fdeea3f21a6bea1eecfe12`의 깨끗한 checkout을
사용했다. 이전 검사와 달리 호스트 소스·Git revision 혼합이나 assertion 수정 없이 실행했다.
로그·JSON 결과·hash는 `tests/llm/reports/agent-schema-references-20261026/`에 보존한다.
suite 종료 후 LF 정규화 기준 **1,217개 파일**이 작업 소스와 일치했다. 이 인계 기록만 이후에
추가하고 문서를 다시 검사했다. 외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다.
이번 요청에 commit/push는 포함되지 않아 변경은 작업 트리에 남긴다.

---

아래는 이전 단계의 기록이다. 최종 결과는 위 표를 기준으로 한다.

## Engine별 Agent 폼 조회

기준 HEAD: `7cd4c6c6d13b5152680b682078310841a0dd1fe6`.
이번 변경은 Agent 편집 UI를 위한 조회 계약이다. Hub 제품 코드는 변경하지 않았다.

```python
catalog = await project.agents.adescribe()
form = await project.agents.adescribe(engine="loop")
snapshot = await project.agents.asnapshot("reviewer")
```

선택 Engine의 순수 동기 `describe_agent()`가 자기 Agent JSON Schema를 제공한다.
Loop는 선택 Completion adapter 스키마와 실행 옵션을, Graph는 Workflow Agent 필드와
필수 workflow 선택을 공개한다. 같은 스키마를 binding에서도 검사하고 `for_agent()`의
의미 검증을 유지한다. 사용자 정의 Engine의 schema hook은 선택 사항이며, 없으면 기존
실행은 유지하지만 선택 폼 조회는 명확하게 거부한다. 공통 최상위 계약은 확대하지 않는다.

조회는 값·기본값·Agent·Run을 만들지 않는다. 기존 저장 형식, Agent revision/CAS,
승인·재개와 완료 Tool 재사용 계약은 변경하지 않았다. 상세 계약은 [Agent 문서](agents.md)를 따른다.

### 이번 검증

Linux Python **3.12.14**, Linux 파일시스템 사본에서 새로 실행했다.
아래 suite는 중복되므로 합산하지 않는다.

| 검사 | Passed | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| Agent schema·모델·위임·Graph focused | 77 | 0 | 0 | 0 |
| 전체 LLM | 2,533 | 1 | 0 | 0 |
| CI 고정 호스트로 로더·CLI 재검사 | 7 | 0 | 0 | 0 |
| 전체 Hub 실행 시도 | 61 | 1 | 105 | 0 |

전체 LLM의 실패 1건은 참조용 로컬 ish 소스에 `_is_library_available`가 없어서 발생했다.
제품·테스트 소스는 그대로 유지하고 CI가 고정한 host commit
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 별도 임시 경로에 추출해 외부 `HOST`
fixture로 지정했다. 로더·CLI 7개를 재실행해 모두 통과했다. 원래 전체 실행이
0 failures였다고 표현하지 않으며, 남은 미해결 LLM 테스트 실패는 없다.

Hub errors 중 103개는 모듈 import 실패이며 해당 모듈 내부 테스트는
실행되지 않았다. 이전 모델 소유권 변경으로 제거된 `LoopParameters.COMPLETION`을
`hub/backend/project/title_values.py`가 참조하는 기존 연결 문제다. 이번 작업에서
Hub를 수정하거나 Backend 호환 alias를 복원하지 않았다. Hub가 통과했다고 판단하지 않는다.

S1–S7 architecture, contract coverage, 문서 검사를 통과했다. 전체 LLM 실행 시간은
2140.31초다. 최초 사본에 Git inventory와 reference host 의존성 정보가 누락된
검사는 중단했으며, 이를 보완하고 관련 12개 검사를 통과한 뒤 전체를 다시 실행했다.
전체 회귀에서 새 스키마 검증이 라이브러리 예외를 노출하는 문제도 발견했다. 기존 Agent
입력 오류의 `ValueError` 계약을 유지하도록 수정하고 Graph 요청 검사까지 focused에 포함한
후 아래 전체 결과를 다시 얻었다.

최종 실행 사본: `/home/user/.cache/ish-agent-schema-3n3ejtzh`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
로그·JSON 결과·LF 정규화 hash는 `tests/llm/reports/agent-schema-20261026/`에 보존한다.
전체 suite 종료 시 1,247개 파일이 작업 소스와 일치했다.
이 인계 문서는 그 결과를 기록한 후 문서 검사를 별도로 실행했다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /tmp/agent-schema-llm.json
.venv-linux312/bin/python -m tests.llm.run_regression --suite hub --output /tmp/agent-schema-hub.json
```

실제 외부 모델/사내 endpoint 및 원격 CI는 실행하지 않았다. 이번 사용자 요청에
commit/push가 포함되지 않아 작업 트리에 변경을 남긴다.

---

아래는 이전 Agent 모델 설정 분리 당시의 인계다. 아래 검증 수를 이번 결과로 재사용하지 않는다.

## Agent 모델 설정과 Loop 실행 설정 분리

기준 HEAD: `c7d9bfafe3aab261684fe26ac3bbf010b38135d8`.
모델·인증·SDK 인자는 저장 Agent의 `completion`, 반복/Tool/문맥 예산/외부 재시도는 Loop의
실행 설정으로 분리했다. [소유권·수동 변경 계약](agent-models.md)을 따른다.

제품 Python 변경은 `engines/loop/configuration.py`, `engines/loop/engine.py`,
`services/command_line.py`다. Agent CRUD와 기존 definition/revision snapshot을 재사용하며,
새 저장 도메인·설정 계층·호환 alias·자동 migration을 추가하지 않았다.
Run/Step/ToolExecutor/승인 저장/Provider 구현과 저장 형식 버전은 변경하지 않았다.

모델 미설정 시 다른 Agent나 Project/Session의 모델을 빌리지 않는다. 예제·CLI와 회귀 fixture는
각 Agent에 모델 인자를 명시한다. 모델 변경으로 Agent revision이 달라지면 이전 승인·재개를
거부하며 완료 Tool 효과를 다시 실행하지 않는다. API 키를 직접 저장하면 Agent와 admission/Run
definition snapshot에도 포함되는 기존 저장 계약을 명시했다.

### 이번 검증 결과

Linux Python **3.12.14**, Linux 파일시스템의 소스 스냅샷에서 실행했다.
아래 검사는 서로 겹치므로 합산하지 않는다.

| 검사 | Passed | Failed/Error | Skipped | 근거 |
|---|---:|---:|---:|---|
| 설정·모델·승인·재개 focused | 209 | 0 | 0 | `corrected.json`, 15개 모듈 |
| CLI와 실제 ish loader | 7 | 0 | 0 | `plugin.json` |
| 최종 전체 LLM | 2,519 | 0 | 0 | `final.json`, 1,793.66초 |

새 회귀는 Agent 모델 격리, SDK 명시값과 생략/null 보존, Engine의 옛 모델 경로 거부,
CLI 소유권, 모델 변경 후 기존 효과/승인 재사용 금지를 검사한다.
초기 스냅샷에서 발견한 옛 모델 상속 fixture를 수정한 뒤 전체를 다시 실행했다.
초기 `full.txt`는 중단한 진단 실행이며 최종 결과가 아니다.

최종 실행 스냅샷: `/home/user/.cache/ish-agent-models-w_qzpivs`.
Python: `/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`.
원본 로그와 소스 hash는 `tests/llm/reports/agent-models-20261026/`에 보존하며 Git/배포에서 제외한다.
실행한 Python 파일 993개를 작업 소스의 LF 정규화본과 바이트 비교해 불일치 0을 확인했다.
최종 문서 수정 후 `check_docs`, S1–S7 `check_architecture`,
`check_contract_coverage --require-complete`도 모두 통과했다. 이 정적 검사는 외부 모델 호출을
검증한 결과가 아니며 실행 회귀와 구분한다.

```sh
# 위 Linux 소스 스냅샷에서 실행한 전체 회귀 명령
.venv-linux312/bin/python -m tests.llm.run_regression --suite llm \
  --output /mnt/d/WorkSpace/ish/tests/llm/reports/agent-models-20261026/final.json
```

### Hub 영향 — 수정하지 않음

사용자의 범위 제한에 따라 `hub/`, `tests/hub/`는 변경하지 않았다.
공개 API 연결 검사는 발견 단계에서 실패했다. 103개 모듈이 같은
`AttributeError: LoopParameters.COMPLETION`으로 import되지 않아 실행 검증까지 진행하지 못했다.
이는 103개 테스트 실행 실패나 Hub 통과를 의미하지 않는다.

- `hub/backend/project/title_values.py`: 제거한 Loop 모델 상수를 import 시 참조하여 시작 차단.
- `hub/backend/project/bootstrap.py`: 모델을 Loop 설정에 쓰는 옛 `update_completion` 경로.
- `hub/backend/execution/engine_selection.py`: Loop effective view와 제거한 모델 조회 API 사용.
- `hub/backend/project/naming.py`: 제목 생성에 사용할 모델을 옛 Engine 설정에서 조회.

Hub는 생성/preset·Agent 모델 선택·모델 표시·제목 생성 경로를 새 계약에 연결해야 한다.
현재 Hub와 함께 그대로 실행 가능한 상태라고 보고하지 않는다. Backend에 Hub 전용 fallback은 없다.
실제 외부 모델/사내 endpoint 호출과 원격 CI는 이번 검증 범위에 포함하지 않는다.

---

## 이전 Tool scope 인계 기록

아래는 이전 변경 당시의 기록이다. 아래 LLM/Hub 통과 수를 위 Agent 모델 설정 분리의
결과로 재사용하지 않는다.

기준 main: `b4bd478f7f8ccc7326a25173e9bbae6398b69bd4` 및 이번 필수 Tool scope 보강.
main과 origin/main이 같은 기준 커밋임을 확인한 뒤 작업했습니다.
리팩터링 전 누적 인계 내용은 [고정 커밋 원문](https://github.com/E1der0wL/ish-llm/blob/b4bd478f7f8ccc7326a25173e9bbae6398b69bd4/docs/llm/handoff.md)에 보존합니다.

## 현재 구현과 이번 변경

- 독립 Project Agent, 수신자별 요청/Run, 병렬 Dispatcher, Session 전송, 최종 Completion 검토와
  Owner별 계약은 현재 main의 구현입니다. [아키텍처](architecture.md)와 [공개 API](backend-api.md)를 따릅니다.
- RunManager가 만든 ToolExecutionScope를 Tool/Loop/Graph/Agent가 공유합니다. 실행 문맥에 scope가
  없거나 타입이 잘못되면 `tool_invocation_invalid`로 거부합니다. 정책 없는 새 객체로 이어가지 않습니다.
- child scope는 부모 scope에서만 축소합니다. 전달받은 명시적 빈 정책 scope도 같은 객체를 유지합니다.
  context 없는 독립 ToolExecutor 호출의 초기화는 유지합니다.
- 승인 저장 후 명시적 resume, 변경된 classification/contract/constraints 검증, 완료 Tool 재사용,
  uncertain effect 보호와 기존 저장 형식은 유지합니다. Hub 제품 코드는 변경하지 않았습니다.

## 확인한 검증 결과

아래 결과는 이번 코드 변경을 포함한 동일 Linux 스냅샷의 실제 실행 결과입니다.
문서 링크/예제 문법 검사는 실행 회귀와 구분합니다. Hub 전체를 다시 실행한 결과로 표현하지 않습니다.

| 검사 | Passed | Failed/Error | Skipped | 범위 |
|---|---:|---:|---:|---|
| 전체 LLM | 2,510 | 0 | 0 | 엄격 발견/실행 검사, 약 2,035초 |
| Hub 공개 API 연결 | 29 | 0 | 0 | 아래 7개 모듈, 약 213초 |

Hub 모듈: `test_approval_continuation`, `test_backend_diagnostics`, `test_coding_skill`,
`test_execution`, `test_multi_agent`, `test_agent_graph`, `test_tool_retry`.
새 `tests.llm.test_tool_scope`는 scope 누락/잘못된 타입, child 권한 축소, 동일 객체 보존,
실제 Run 실패와 durable 승인 재개를 검증합니다.

Python **3.12.14**, LiteLLM **1.106.0**, MCP **2.3.0**, Pydantic **2.14.1**의 CI 고정 의존성을 사용했습니다.
reference ish commit은 `d833790959a76ac457fdeea3f21a6bea1eecfe12`입니다.
이전 환경 설정 시도의 중단 로그와 최종 성공 실행은 구분해 보존했습니다.
로컬 원본 로그·hash: `tests/llm/reports/tool-scope-20261025/` (Git/배포 제외).
문서 정리 후의 링크·정적 계약 재검증은 [이번 문서 검토](documentation-review.md)에 따릅니다.

## 재현 환경과 명령

소스와 Python 환경을 WSL Linux 파일시스템에 둡니다. `/mnt/d`에서 시간 민감 시험을 직접 돌리지 않습니다.
고정 의존성은 `tests/llm/dependencies-linux.lock`, reference host revision 및 필수 OS 패키지는
[CI 정의](../../.github/workflows/tests.yml)가 소유합니다. `ish.platform/`은 별도 참조 체크아웃이며 배포하지 않습니다.

```sh
# 준비된 Linux Python 3.12.14와 reference host로 새 스냅샷 실행
.venv-linux312/bin/python tests/llm/run_linux.py --full
# Hub 전체 검사가 필요할 때
.venv-linux312/bin/python tests/llm/run_linux.py --full --hub

# 이미 Linux 파일시스템에 준비된 소스에서 문서/계약 검사
.venv-linux312/bin/python -m tests.llm.check_docs
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --require S7
.venv-linux312/bin/python -m tests.llm.check_contract_coverage --reviews docs/llm/contract-coverage-reviews.json --require-complete --output /tmp/ish-contract-coverage.json
```

이번 검증 스냅샷은 `/home/user/.cache/ish-tool-scope-tvbgpwz2`, Python은
`/home/user/.cache/ish-scope-deps-69rd8deo/.venv-linux312/bin/python`입니다.
이 경로는 이 컴퓨터의 검증 기록이며 제품이 의존하는 경로가 아닙니다.

## 남은 검증 경계

외부 인증 모델·사내 endpoint·장시간 부하·전원 차단/디스크 손상은 이번 자동 회귀의 검증 범위가 아닙니다.
이전 커밋의 CI 성공을 새 커밋의 CI 성공으로 승계하지 않습니다.
운영상 한계와 실제 외부 효과의 원자성 제한은 [운영 준비 상태](production-readiness.md)에 있습니다.
