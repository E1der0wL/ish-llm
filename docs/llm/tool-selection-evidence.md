# Tool 선택 기록과 공통 사용량 증거

ToolInvocationSelection은 Tool 이름·원본 인자·선택 binding을, ToolBindingRecord는 승인 전
저장할 선택 기록의 종류·완료 상태·container·요청/Checkpoint identity를 소유한다. Writer와
Reader가 같은 선언을 사용하며 Registry/실행기는 속성으로 소비한다. 원본 JSON을 먼저 검증하여
tuple·실행 객체를 정상 저장 증거로 변환하지 않는다. 중첩 조회 값과 직렬화 결과는 사본이다.

Checkpoint의 예약된 @tool-binding: 키는 해당 Owner가 해석한다. 종류 필드를 제거해 검사를
우회할 수 없으며 저장한 request_key와 키, 실제 실행의 checkpoint_name이 일치해야 한다.
Checkpoint 자체가 없을 때만 새 실행이다. 존재하는 Checkpoint의 필수 records 누락은 빈 기록이
아니다. 선택이 같더라도 필수 상태·identity가 손상된 기록을 새 기록으로 덮어써 복구하지 않는다.

ToolUsageRecord는 공통 논리 Tool 사용량의 calls/completed/revision/pending_approvals/invocations를
소유한다. 기존 정확한 필드 집합·비음수 정수·예약 문자열·호출 ID 중복 거부를 유지한다.
ToolUsageCheckpoint가 runtime_tool_usage의 부착·필수 읽기·검증된 payload 투영을 소유한다.
ToolExecutionScope는 정책과 실제 사용량 변경을 계속 소유하고 저장 검증만 이 Owner에 위임한다.
이전 공개 Scope API와 ToolUsageError는 같은 구현으로 연결되며 이중 Reader를 만들지 않는다.

저장된 선택 기록 Reader는 공통 사용량을 먼저 검증한 뒤 자신의 payload를 읽는다. projection은
원본 기록을 수정하거나 사용량을 버리는 저장 형식이 아니다. 새 기록은 기존 서비스 쓰기 경계에서
현재 사용량을 함께 저장한다. 최신 revision/calls 복원, 승인 예약·호출 identity와 호출 한도는
유지된다. 전체 복원 입력의 검증이 끝나기 전 실행 중 scope를 변경하지 않는다.

독립 wire·필수 필드/identity 손상·중첩 사본·실패한 복원의 원자성을 검증한다. 실제 파일 효과
시험에서는 손상된 선택을 재개할 때 Checkpoint 이벤트·승인 호출·파일 효과·사용량 변경이 모두
0이고 입력 기록이 보존됨을 확인한다. 기존 중첩 Agent 승인/재개·효과 재실행 방지·R3 시험과
전체 통합 게이트를 유지하며 결과는 [누적 기록](whole-ownership-verification.md)에 남긴다.
