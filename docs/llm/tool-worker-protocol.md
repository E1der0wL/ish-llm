# Tool interpreter와 프로세스 시작 프로토콜 Owner

`llm/core/tool_worker.py`가 Tool interpreter의 요청·응답·descriptor·소스 binding을 소유한다.
부모와 자식은 같은 타입을 사용하고, JSON 변환은 이 Owner에서 수행한다. 원본 Tool 정의는
ToolDefinition과 ToolContract가 계속 소유한다. descriptor의 중첩 정의는 각 Reader가 검증하며
이전 필드·지문 입력·필수 실행 선언을 유지한다. JSON을 별도 영속 정의로 저장하지 않는다.

- inspect/execute 요청은 기존 필수 필드를 명시한다. 실행에는 descriptor 지문·인자·호출 출처가
  추가된다. opaque 호출 출처의 의미는 ToolCall이 검증하며 실행 scope/원장 Owner는 부모다.
- 성공 응답은 정확한 ok/value 집합, 실패 응답은 기존 error 판별자를 사용한다. 불명확한 효과를
  no-effect로 복원하지 않는다. 기존 실패 envelope의 관측 확장 허용과 성공 envelope의 확장
  거부를 유지한다. ToolExecutionError가 효과·retryable·recoverable 규칙을 한 곳에서 검증한다.
- 신뢰한 code는 한 번 관측해 전송한다. 미분류 실행 실패는 계속 불확실한 효과이며 임의 retry를
  만들지 않는다. 승인 전 실행 금지·descriptor 변경 감지·취소·descendant 회수는 유지한다.
- ToolErrorCode가 worker 실패 코드를 선언하며 관측 통계는 같은 선언을 사용한다. 표시 reason
  vocabulary는 진단 관측 분류이며 실행 오류 코드와 동일한 권한으로 해석하지 않는다.

`llm/services/runtime/_worker.py`의 ProcessLaunch는 argv/cwd/env/payload/memory_bytes를
선언하며 일반 명령과 Tool worker가 공유한다. stdin EOF와 무제한 메모리의 명시적 None은
보존한다. Reader와 직렬화는 중첩 입력을 분리한다. ProcessGate는 기존 -I/-S 실행 인자와
부모 사망 검사를 소유한다. 환경·격리·자원 정책은 부모 runner가 계속 검증하며 시작 게이트가
추가 정책을 추론하지 않는다.

부트스트랩 순서도 계약이다. Tool child는 protocol FD를 분리하고 저장소 import root를
추가한 뒤, 표준 라이브러리만 사용하는 프로토콜 Owner를 읽는다. plugin_lib를 넣은 후에만
Component/SDK를 import한다. 일반 프로세스 시작 게이트는 -I/-S에서 llm/외부 패키지를
import하지 않고 실행한다. 부모 사망 신호와 stdin 수신 이전에 사용자 명령을 실행하지 않는다.

IPC 및 시작 요청의 Owner 연결 이후 패키지/manifest·활성화·Tool runtime·등록·함수 Schema의
후속 전수 정리는 [최종 점검](contract-closure.md)과 [전수 실행 기록](whole-ownership-verification.md)에
있다. 각 단계의 당시 실패와 최종 검증을 구분한다.
