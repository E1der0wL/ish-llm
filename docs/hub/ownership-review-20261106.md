# Hub 미검토 경로와 감사 근거 정리 — 2026-11-06

범위는 기존 미검토 Hub 제품 파일 108개, 감사 근거 해시가 달라진 파일 7개,
기존 부분 검토 파일에 남아 있던 미분류 발생 165개, 그리고 이를 고치면서
추가·변경한 직접 소비자다. 이번 파일별 검토 기록은 144개다. 기준은
[단일 소유](../llm/contract-ownership.md), [명명](../llm/naming-conventions.md),
[검증](../llm/verification-policy.md) 계약이다. `llm/` 구현은 변경하지 않는다.
이 문서는 [이전 경계 점검](pending-contract-review.md)의 후속 결과이며 이전 기록을 대체하지 않는다.

## 발견 사항과 수정

| 경계 | 발견 사항 | 수정과 유지한 책임 |
|---|---|---|
| Memory | 누락된 실행 설정을 Hub의 생성 프리셋으로 보충하던 경로 | native `MemoryProcessingConfig`로만 해석한다. 명시되지 않은 압축을 켜거나 읽기 프리셋으로 결과 예산을 만들지 않는다. 새 Project 프리셋과 기존 명시 설정은 유지한다. |
| 플러그인 | 작업명·요청 참조·출력 구조의 중복, 참조의 Project identity 검증 부족 | `PluginOperation`, `PluginReference`, `PluginBatch`를 공통 경계로 사용한다. 참조의 Project와 실제 소유 Session을 대조한다. 등록·영속 요청·승인·질문·공통 stream은 기존 공개 API로 처리한다. |
| 승인·질문 | UI와 Runtime이 같은 payload를 따로 조립·해석 | 조작 요청과 조회 결과를 타입으로 전달한다. native 승인 조건·위험도·CAS·재개 계획은 재구현하지 않는다. 답변 저장과 전달을 분리하고 원래 Run identity를 보존한다. |
| MCP | 발견 결과·Agent 선택·revision envelope 중복 | native Tool snapshot을 담는 조회 타입과 명시 선택 타입을 연결한다. 서버·목록·Agent revision 확인과 native 저장을 유지한다. |
| 추가 지시·터미널 | 분류 결과와 관측 페이지의 고정 구조가 실행부에 분산 | 분류·대상·출력 페이지 타입을 연결한다. 모델 분류와 출력 관측은 실행 허가가 아니며 실제 redirect/프로세스 읽기는 native API가 집행한다. |
| 대용량 Tool 결과 | 파일·프로세스 결과 필드를 직접 조립하는 모델용 projection | native `FileTextPage`, `ProcessOutput` 필드와 타입을 사용한다. 저장 원문과 영수증을 바꾸지 않으며, 알려진 원문이 없는 결과에 커서를 만들지 않는다. 재조회 힌트는 현재 등록 Tool schema와 제약으로 검증한다. |
| 백그라운드 후처리 | 모델 입력·수정 계획을 여러 소비자가 dict로 해석 | `BackgroundInput`, `BackgroundPlan`으로 경계를 정리했다. 모든 대상을 먼저 검증하고 Memory revision·Goal CAS를 유지하며 Refinement는 제안만 생성한다. 날짜는 native 시각 계약에 맡긴다. |
| 음성 | 장치·상호작용 설정과 런타임 자원 dict의 혼용 | `SpeechBehavior`, `AudioDevice`, `SpeechResources`를 분리한다. provider 옵션은 adapter에 전달하며, 누락 설정을 무제한·자동 실행으로 바꾸지 않는다. 장치·task·queue는 저장하지 않는다. |
| UI·이미지·Workflow | 고정 키·문구·크기·프로토콜 선언 위치와 popup hook 중복 | 각 위젯·프로토콜 Owner의 선언과 공통 Dialog 수명 API를 사용한다. 그래프 draft/undo는 로컬 편집 상태이며 저장 검증·CAS·실행은 native Owner에 남긴다. |

전체 회귀 중 선언 정리에서 생긴 Tool 미리보기 말줄임표 충돌도 발견했다.
Rich의 `ellipsis` 모드와 표시 문자 `…`를 별개 선언으로 고쳤다. 기존 미리보기 테스트는
변경하지 않고 좁은 화면과 넓은 화면에서 다시 검증했다.
암묵적 압축 기본값에 의존하던 테스트는 Memory 정책을 명시하도록 수정했고,
요청 키 생략과 같은 의미의 native `UNSET` 전달은 구조화된 대상 보존 테스트에서 확인했다.
실행 횟수·저장 원문·잘못된 대상 거부에 관한 기존 주장은 유지했다.

## 원래 해시가 달랐던 7개 파일

| 파일 | 재검토한 변경 |
|---|---|
| `tests/hub/test_application_defaults.py` | 생성 프리셋과 backend 소유 시각 fixture. 기존 설정을 재주입하지 않음 |
| `hub/backend/project/postprocessing.py` | 날짜를 직접 쓰지 않고 native 시각 계약을 사용. 기존 공개 CRUD·CAS와 후처리 범위 유지 |
| `tests/hub/test_background_records.py` | 후처리 날짜 검증이 backend 기록을 확인하도록 변경 |
| `tests/hub/test_agent_settings_groups.py` | native Agent 시각과 revision을 사용하는 fixture |
| `docs/hub/plugin-api.md` | 공통 출력·등록·질문·시각의 현행 공개 계약 설명 |
| `tests/hub/test_plugin_api.py` | 현재 공개 계약 fixture와 소유 Project·Session 경계 |
| `hub/backend/project/mcp_management.py` | 서버·카탈로그·Agent 선택 및 revision 검사 |

같은 파일이 여러 감사 발생의 근거로 사용되어 7개 파일이 9개 기록을 무효화했다.
이번 코드 정리로 추가로 바뀐 근거도 함께 다시 검토한다. 해시를 맞추기 위해 테스트의
주장이나 검사기를 약화하지 않는다.

## 검토 방법과 해석 범위

- 각 파일의 선언·Reader·소비자·Writer와 동적 접근을 확인했다. 생성 기본값을 실행 시
  fallback으로 사용하는 경로와 고정 요청 구조의 소비 경계를 우선 수정했다.
- locale·theme·prompt·skill은 별도 선언 자료로 읽었다. 지침은 승인 권한이 아니며
  사용자 설정을 자동 덮어쓰거나 실행 결과를 성공으로 바꾸지 않는다.
- 단순 문자열 이동만으로 완료 처리하지 않았다. 고정된 요청/응답은 타입으로 전달하고,
  native 의미·필드·상태는 해당 Owner를 참조한다. 임의 Tool 결과의 제한된 표시와
  opaque provider 옵션은 열린 데이터로 유지한다.
- 감사 후보는 현재 파일 해시·정확한 발생 ID와 검증 근거에 묶는다. 제거된 발생 3개는
  삭제하고, 변경된 MCP revision 비교와 음성 readout 조건은 각각 새 발생으로 검토한다.
  파일 전체 예외, 검사 규칙 변경, debt baseline 갱신은 하지 않는다.
- 기존 검토의 변경되지 않은 발생은 같은 심볼·역할·리터럴을 대조한 후에만 유지한다.
  이번 범위 밖의 이전 감사 판단을 새로 전수 검증했다고 주장하지 않는다.

`docs/llm/contract-coverage-reviews.json`은 파일별 근거,
`docs/llm/contract-coverage-index.json`은 현재 대상과 상태,
`tests/llm/fixtures/architecture_reviews.json`은 개별 정적 발생의 근거다.
공유 감사 파일이 `llm` 문서·테스트 경로에 있다는 이유로 backend 구현을 변경한 것은 아니다.

## 검증 기록

실행 증거는 `tests/hub/reports/ownership-20261106/`에 둔다. 과거 실행의 실패도 보존한다.
Linux 파일시스템의 소스 사본과 Python 3.12.14를 사용하며, 유료 외부 모델 호출 없이
공개 backend 연동을 포함한 Hub 회귀를 실행한다. 집중 검증과 전체 회귀, 감사 인덱스·정적
검사·문서 검사의 결과는 각각 구분하여 기록한다.

- `data06`: Memory·대용량 결과·플러그인·승인·음성·Workflow 집중 테스트 96개 통과.
- `pages07`: native 결과 페이지·원문 이어 읽기·미리보기 집중 테스트 21개 통과.
- `background08`: 후처리·추가 지시·Agent/그룹 집중 테스트 27개 통과.
- `routing10`: 추가 지시 대상·입력 보존 집중 테스트 10개 통과.
- `recovery11`: 명시적 압축·복구·Tool 중복 실행 방지 집중 테스트 20개 통과.
- `x11_12`: 독립 임시 공간의 Xvfb를 사용한 클립보드 연동 9개 통과. 시스템 소켓이나
  데스크톱 클립보드는 변경하지 않았다.
- `full02`: Hub 전체 회귀 **1,140/1,140 통과**, 1,669.88초. 실패·오류·Skip·미실행·중복 0개.
  공개 backend 연동과 격리 X11 9개를 같은 전체 실행에 포함했다.

최초 `full01`은 미리보기 선언 충돌, 명시적 설정/`UNSET` 전달에 맞지 않던 fixture,
WSLg 공용 소켓에서의 X11 서버 시작 실패를 기록한다. 이 결과를 지우거나 성공으로
바꾸지 않았으며, 수정 후 고정한 소스로 전체 회귀를 다시 실행한 결과가 `full02`다.

최종 소스의 `audit13`은 감사 검사기·근거·커버리지 테스트 39개, 엄격 아키텍처 검사,
완전 커버리지 검사와 문서 검사를 모두 통과했다. 정적 발생 85개의 근거가 현재 소스와
일치하며 미해결·기록 오류는 0개다. 현재 제품 파일 795개의 감사
인덱스에서 미검토·오래된 해시·미분류·위반·미검토 동적 경로는 모두 0개다.
이는 기록된 검토 범위를 완료했다는 뜻이며, 정적 후보 수가 제품의 모든 동작을
증명하거나 과거 검토 전체를 새로 수행했다는 뜻은 아니다.

검증 소스는 `/home/user/.cache/hub-ownership-7s08jpqy`다. `full02/source.json`으로
고정했고, `guard.json`은 제품·테스트·참조 호스트 등 1,343개 검증 파일이 현재 작업
트리와 일치함을 확인한다. `llm/` 412개 파일은 작업 시작 시의 바이트와 동일하다.
최종 전체 회귀를 시작한 뒤 제품·테스트 코드는 변경하지 않았다. 공유 감사 인덱스와
근거만 갱신했으며 검사 규칙·기존 계약·과거 보고서 내용은 변경하지 않았다.
