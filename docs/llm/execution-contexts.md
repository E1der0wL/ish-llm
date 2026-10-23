# Tool 호출 문맥과 사용량 문맥의 수명 Owner

`ToolCallContext`가 Task별 Tool 호출 문맥의 이름·미결합 값·진입·조회·복원을 소유한다.
ToolExecutor와 실제 Tool child가 같은 scope API를 사용하고 private ContextVar에 직접
접근하지 않는다. 기존 current_tool_call() 공개 API는 동일한 분리된 사본을 제공한다.
호출 정보의 사본 변경으로 실행 중 원본 인자나 계약이 바뀌지 않는다.

UsageScope는 자신의 ContextVar와 현재 문맥 조회/존재 판정을 소유한다. current_usage()는
기존처럼 같은 Scope/counter 객체를 반환한다. 조회를 이유로 counter나 정책을 복사·초기화하지
않는다. 두 문맥은 서로 다른 수명을 유지하며 하나의 전역 실행 여부로 합치지 않는다.

중첩 scope 종료·실패·취소에서는 이전 문맥을 복원한다. 동시 Task끼리 문맥을 공유하지 않는다.
기존 runner/handler 호출, timeout, 승인·효과·예산·원장 순서는 바꾸지 않는다. Scope Owner는
문맥의 존재를 제공하며 별도의 실행 권한이나 재시도 정책을 만들지 않는다.

ToolData.prepare는 두 Owner의 존재 판정을 사용해 기존의 실행 중 준비 금지를 유지한다.
실제 소스 import가 만드는 파일 효과로 이를 검증한다. Tool 문맥만, 사용량 문맥만, 두 문맥이
모두 있는 경우는 준비와 파일 효과가 금지되며, 문맥 종료 후 명시적 준비는 기존처럼 가능하다.
단순 mock 호출 횟수로 이 효과 검증을 대체하지 않는다.
