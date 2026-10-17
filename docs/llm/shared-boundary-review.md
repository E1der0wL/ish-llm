# 공통 실행 경계 검토

검토 범위는 공통 core/service, Engine 합성, Component hook, 선택 Provider adapter다.
구현체 이름 검색만으로 결함을 판정하지 않고, 교체 가능한 경계의 실제 호출자·저장·재개 경로를
따라가며 공개 계약과 private 표현을 구분했다. Hub 제품 코드는 수정하지 않았다.

## 확인한 여덟 항목

| 항목 | 처리 |
| --- | --- |
| 선택 Agent의 child 실행 binding 누락 | 모든 선택 Engine의 `bind_execution`을 받아 JSON 검증 후 저장·비교한다. Loop adapter 변경도 대기 승인에 반영한다. |
| 미선택 Agent의 private 설정 사전 검증 | 구조 참조 탐색과 실제 선택 검증을 나눈다. 선택된 Agent와 실제 사용한 하위 Agent만 실행·재개 binding 검증을 받는다. |
| Loop Tool checkpoint와 완료 Step 불일치 | Step 완료 이벤트에 record를 첨부하고 기존 StorageIO 트랜잭션에서 함께 확정한다. |
| 정책이 첫 approve 선택지를 임의 선택 | 유일하고 추가 필수 입력이 없는 승인만 자동 선택한다. 모호한 선택은 pending으로 남는다. |
| 확인 재개에서 option ID 고정 | ID 대신 `effect=approve`의 유일한 선택지를 사용한다. 사용자 선택·입력이 필요하면 명확히 거부한다. |
| Component 조회에 로컬 root 전제 | 사용량·보관 참조·선택 해제 보호는 owner hook 결과를 사용한다. 저장 위치를 추정하지 않는다. |
| 공통 processor가 LiteLLM retry 필드만 보호 | 공통 프로토콜 보호와 adapter-owned parameter 검증을 분리한다. Loop가 선택 adapter 검증을 반드시 연결한다. |
| Pipeline에서 child 진단 details 유실 | 실행 failure_code와 관찰 Diagnostic을 별도로 유지한다. 부모 Step source만 재결합한다. |

## 재감사에서 추가로 보강한 부분

1. ComponentRegistry의 삭제 참조 검사에도 동일한 로컬 폴더 전제가 있었다. `references(project)`에
   위임하여 외부 저장 Component의 삭제 보호도 적용한다.
2. Graph 노드 완료도 checkpoint와 Step을 별도로 저장하고 있었다. Loop에만 예외를 두지 않고
   같은 원자적 이벤트 계약을 사용한다. 중첩 bridge와 Agent 사용량 ACK도 첨부 record를 전달한다.
3. 승인 선택지가 하나여도 필수 입력이 있으면 자동 응답 생성 중 실패할 수 있었다. 입력을
   만들어 내지 않으며, 정책은 pending을 유지하고 암묵 confirmation resume은 명시적 응답을 요구한다.
4. 공통 EventContext의 서비스 소유 metadata 보호 목록에 `execution_started`, `restart`,
   `diagnostic`이 빠져 있었다. 기존 보호 계약에 포함하여 외부 이벤트 처리기가 실행 전 실패 근거나
   재시작 연결·Run 진단을 덮어쓰지 못하게 한다. 실제 효과를 수행한 무-Step Engine에서도
   이 경로로 새 실행 권한을 만들 수 없음을 검증한다.

원자적 이벤트의 구독 검사에서는 별도 CHECKPOINT 알림에 의존하는 관찰자도 확인했다.
저장을 한 번에 확정한 뒤 기존 CHECKPOINT와 STEP_COMPLETED 알림을 계속 발행하므로,
기존 관찰자가 완료 기록을 기다리는 동작도 유지한다.

선택 handler의 동기 validation은 사전/선택 경로에서 같은 검증 함수를 사용한다. 비동기 validator를
조용히 건너뛰지 않는다. 새 Manager·Registry·제품 정책·숨은 수치 기본값·migration은 추가하지 않았다.

## 유지한 명시적 공통 계약

- canonical completion 메시지, Tool 호출/결과 쌍, 현재 텍스트 요청 보존은 선언된 공통 프로토콜이다.
  멀티모달이나 임의 SDK wire message를 공통 계층에서 자동 해석하지 않는다.
- `EngineEvent`, `Diagnostic`, `ResourceRef`, checkpoint lifecycle/envelope, Tool approval binding은
  공개 데이터 계약이다. 특정 Component의 private JSON으로 대체하지 않는다.
- `file`/`memory` 대화 저장 선택과 File Component base의 경로는 해당 구현의 명시 계약이다.
  외부 Component hook에 그 경로 존재 조건을 강제하는 것과 구분한다.
- 기본 LiteLLM adapter 조립과 교체 가능한 Host token counter 등록은 composition 경계다.
  공통 실행기가 SDK-specific 옵션을 재해석하는 경계는 제거했다.
- Agent의 Graph 실행 연결과 Graph의 중첩 환경 검사는 선언된 Graph orchestration 연동이다.
  이를 모든 Engine의 저장·진단·재시도 형식으로 강제하지 않는다. 공통 Engine 이벤트와
  선택 binding 검증을 통과하는 다른 Engine은 같은 Run 안에서 사용할 수 있다.
- 코드 identity는 Host가 revision/execution_binding으로 선언한다. 함수 구현의 변경을 자동 탐지하거나
  arbitrary trusted Python 코드의 내부 변조를 막는 기능은 제공하지 않는다.
- 실제 선택 검증 이전의 독립 노드는 이미 실행될 수 있다. 실패한 Agent 자신의 효과는 시작하지
  않으며, 선행 효과를 자동 rollback/replay하지 않는다.

## 감사 방법과 범위

SDK import/이름 분기, Component root/directory 전제, checkpoint marker/option ID, Provider retry 필드,
예외 wrapper와 진단 source, 설정 resolution과 public hook 호출자를 검색하고 소비 경로를 확인했다.
처음 발견한 항목은 같은 의미의 경로로 확장하여 검사했다. 기존의 explicit-only 설정, 승인 응답
영속화, 명시적 resume, 불확실한 효과의 재실행 거부, 원자적 저장 책임을 유지한다.

검토는 정적 추적과 대체 구현/장애 주입 회귀를 근거로 한다. 모든 미래 Engine/Component나 실제 외부
서버의 동작까지 결함이 없다고 증명하는 것은 아니다. 이번 범위에서 확인된 actionable 결함을 수정하고
같은 패턴을 다시 검색하는 방식으로 마무리한다.

최종 정적 검색에서 공통 core/service 및 Engine 합성 경로 73개 Python 파일을 대조했다.
남은 SDK 참조는 기본 adapter/token counter 조립이고, `max_retries`는 Project가 소유하는
Tool 재시도 정책, Graph의 `retry_policy=None`은 외부 scheduler의 자동 효과 재실행을 끄는
연동 계약이다. uncertain-effect 재시도에서 사용한 `approve` ID는 해당 서비스가 직접 생성한
정형 요청의 ID이며 외부 구현체의 임의 선택지를 해석하는 데 사용하지 않는다.
각 검색 결과와 소스 일치 검사는 `tests/llm/reports/`에 별도로 기록한다.

## 최종 검증

Linux Python 3.12.14, Linux 파일시스템의 해시 검증된 소스 스냅샷에서 실행했다.

| 범위 | 결과 | 시간 |
| --- | --- | --- |
| 실행 경계·processor·Agent·중첩 Graph·승인·확장·checkpoint·event 집중 회귀 | 177 passed, 0 failed, 0 skipped | 77.162초 |
| 전체 LLM (`tests/llm/run_linux.py --full`) | 1,641 passed, 0 failed, 0 skipped | 660.414초 |
| Hub 공개 API 연결 7개 모듈 | 58 passed, 0 failed, 0 skipped | 205.747초 |

Hub 연결 검사는 진단, Provider 복구, 질문 Tool, RAG Job, 요청 이력, 요약 API, Tool 재시도를
대상으로 했다. Hub 전체 UI suite나 외부 모델 서버 검증을 수행했다는 의미는 아니다.
Hub 파일 393개의 작업 시작 시 해시와 비교하여 이번 작업으로 인한 변경이 없음을 확인했다.
테스트에 사용한 Python 파일 358개와 작업 폴더의 최종 코드도 일치했다.

로그·정적 검색 목록·소스 검증:
`tests/llm/reports/ish-provider-h251yvfo/`.

확인한 여덟 항목과 재감사에서 발견한 네 항목을 보강했다. 같은 패턴과 실제 소비 경로를 다시
검토한 범위에서 추가로 확정할 수 있는 계약 위반은 찾지 못했다. 승인/재개 영수증,
Tool 효과의 중복 실행 방지, Project 설정 소유권을 유지하며 저장 형식 버전·자동 migration은
추가하지 않았다. 선택 binding이 달라진 기존 승인/체크포인트는 재사용하지 않고 새 요청을 요구한다.
