# 방향 전환의 지원 범위와 평가

이 문서는 [추가 지시 계약](steering.md)의 구현 범위를 설명한다. 실행 권한과 저장 계약은
기존 Owner를 유지한다. 새 Run 상태, Tool 승인 체계, SDK 전용 공통 분기, 숨은 실행 제한은 없다.

## 지원 동작

| 동작 | Backend 동작 |
| --- | --- |
| 일반 추가 지시 | 현재 호출을 유지하고 미시작 Tool/다음 모델 입력 경계에서 반영 |
| 명시적 방향 전환 | 영속 접수 뒤 선택한 invocation에 취소 요청; 저장 소비자/Run Task는 유지 |
| 모델 대기/스트리밍 | 이전 호출의 늦은 결과를 실행 근거로 쓰지 않고 다음 iteration으로 이동 |
| 읽기 전용 Tool | 실제 반환 결과가 있으면 보존; 취소되면 trusted read-only 근거로 무효과 처리 |
| 외부/unknown 효과 Tool | 실제 반환 결과가 있으면 보존; 취소 뒤 결과가 없으면 uncertain으로 종료 |
| 승인/재개 | 새 동작에는 기존 검증·승인; uncertain은 명시적 재개/재시도 판단 필요 |
| Graph | 실제 선택한 consuming Agent 채널만 제어; 형제/컨테이너/독립 프로세스 자동 종료 없음 |
| 지시 철회/대체 | 아직 선택되지 않은 일반·예약 지시만 처리; 과거 원문/영수증 유지 |

위 취소 처리는 방향 전환에 대한 계약이다. Run 전체 중단 요청은 기존 interrupt 계약을 따른다.
저장 ACK·승인 검사·정책 검사를 방향 전환으로 취소하거나 생략하지 않는다.

일반 지시 철회와 선택은 같은 저장 직렬화 경계에서 경쟁한다. 선택 checkpoint는 모델 입력
복구 근거이므로 적용 알림 이전이라도 철회하지 않는다. 방향 전환은 dispatch부터 취소 동작이
시작됐으므로 철회할 수 없다. 명시적으로 새 정정 지시를 보낸다.

## Codex 비교 기준

공식 [Codex App Server](https://learn.chatgpt.com/docs/app-server)의 공개 계약은 현재 Turn ID를
검사하는 `turn/steer`와 Turn을 종료하는 `turn/interrupt`를 구분한다. ish도 대상 Run/Agent를
명시하고 지시를 같은 실행에서 반영하며, Run 전체 중단은 기존 `interrupt()`로 분리한다.
추가된 `redirect()`는 현재 invocation 취소와 다음 판단을 같은 Run에 연결하는 ish의 계약이다.
이 API가 Codex 내부 구현과 같다는 의미는 아니다.

자동 검증으로 평가할 대상은 지시 접수, 반영 위치, 취소 후 다음 호출, 늦은 결과 차단,
Graph 대상 격리, 승인·효과·재개 무결성이다. 모델의 지시 이해 품질, 실제 사내 endpoint의
취소 처리, Codex와의 체감 속도 동등성은 별도 실환경 검증이 필요하다.

## 보장하지 않는 것

- 동기 SDK가 멈춰 있으면 local stream 전달을 취소해도 worker가 반환할 때까지 남을 수 있다.
  Provider admission slot은 실제 worker 종료 전 반환하지 않는다. 명시한 동시 호출 한도가
  꽉 찼다면 다음 모델 요청도 기다린다. 서버의 추론 종료/과금 중단을 추정하지 않는다.
- 취소를 무시하거나 event loop를 막는 custom handler의 즉시 종료는 보장하지 않는다.
  필요하면 해당 Runner/SDK가 협력 취소 또는 process isolation을 제공해야 한다.
- 중단된 파일/네트워크 효과를 rollback하거나 uncertain 효과를 자동 반복하지 않는다.
- background process는 별도 명시적 프로세스 취소 API로 관리한다.
- Hub의 버튼·기본 동작·위험 표시·대기 제한은 Application 소유다. Backend 기능만으로
  Hub의 화면 조작이나 Codex 수준의 전체 UX가 완성됐다고 판정하지 않는다.

## 검증 범위

`tests.llm.test_direction_changes`는 실제 Run/Step/Checkpoint 경로, 늦은 SDK 응답,
취소된 shell process 종료, 영속 접수 실패, 선택-철회 경쟁, 승인 후 명시적 재개,
Tool 무효과/불확실 효과/완료 경쟁, Graph 병렬 대상 격리와 반복 제한을 검사한다.

2026-11-03 최종 검증은 main `e3d38fea04a01a312f1bc8636432311ab7d5d2d4`에 이번 llm 변경과
관련 문서·테스트만 적용한 독립 소스로 실행했다. 작업 디렉터리의 다른 미커밋 Hub·Dok
변경은 업로드 후보와 검증 소스에 포함하지 않았다. Linux Python 3.12.14, Linux 파일시스템의
해시 확인 snapshot, private `/tmp`·`/dev` 및 Xvfb 환경을 사용했다.

| 검증 | 통과/발견 | 실행 시간 |
| --- | ---: | ---: |
| 집중 검증 | 58/58 | 23.137초 |
| LLM 전체 | 2,839/2,839 | 1825.264초 |
| Hub 전체 | 1,050/1,050 | 1478.289초 |

각 실행의 실패·오류·skip·누락은 모두 0이다. 전체 suite를 각각 다시 실행한 결과이며,
이전 실행이나 개별 재검사 결과를 합산한 수치가 아니다. 시간은 테스트 실행 시간으로,
사용자 요청 지연이나 Codex 대비 성능 측정값이 아니다. 원본 로그와 소스 manifest는
`tests/llm/reports/steering-release-20261103/`에 보관한다.

이전 전체 검사에서 발견한 invocation Task 경계의 ContextVar 전달 누락은 공통 호출 경계에서
binding 값을 보존하도록 수정했다. Host 변수 이름별 예외를 만들지 않았으며 이번 전체 Hub
회귀에도 Tool 오류 복구·승인·실행 중 입력 경로가 포함된다.

공용 테스트 snapshot은 배포 소스인 `dok/`·`tests/dok/`와 JSONC 자료를 포함하도록 정리했다.
Git에 포함하지 않는 `tests/<plugin>/reports/`·`manual/` 링크는 문서 검사에서 별도로 집계하며,
clean checkout에서 그 파일이 존재한다고 주장하지 않는다. 일반 소스/문서 누락과 저장소 외부
링크는 계속 실패한다. 업로드 기준 main의 오래된 감사 근거 16개는 이전 검토 소스와 현재
diff를 읽고 기존 판정과 동일한 occurrence에만 근거를 갱신했다.

별도 전체 계약 소유권 감사는 미검토 파일 129개가 남아 있어 `--require-complete` 조건을
충족하지 않는다. stale/violation/issue는 0이지만 이를 전체 감사 완료나 원격 CI 성공으로
해석하지 않는다. 미검토 항목을 일괄 승인하거나 CI 조건을 완화하지 않았다. 실제 외부 모델의
취소 응답과 Hub 방향 전환 UX/체감 지연은 별도 검증 대상이다.
