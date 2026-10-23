# 내장 capability의 선언과 Agent 정의 투영

ToolRegistry.CAPABILITY가 tools export의 이름을, 모델 처리 모듈의 COMPLETION_PROCESSORS가
처리기 export 이름을 소유한다. AgentDefinition.CAPABILITY는 기존 Agent 도메인 identity를
참조한다. Component 선언·Resolver·Engine·Run·Hub는 같은 Owner를 참조한다. Project의 tools
Component 이름, Agent 정의의 tools 필드, Hub 관리 종류 등 다른 의미의 동일 철자는 혼동하지 않는다.

capability map은 열린 이름 공간이다. Application의 새 이름을 닫힌 Enum으로 제한하지 않는다.
선택한 요청만 구성하고 tools는 Registry를 합치며 일반 export는 제공자별 tuple을 유지한다.
completion_processors의 빈 tuple은 유효하고 다른 필수 기능이 없으면 기존 오류로 실패한다.
새 기본 기능이나 자동 선택·권한·처리 순서를 추가하지 않는다.

AgentDefinitions는 Agent capability의 records envelope를 직렬화하고 필수 records를 읽는다.
원본 JSON을 먼저 검증해 tuple이나 실행 객체를 정상 정의로 변환하지 않는다. 선택은 정확히 한
정의를 요구하며 원본에서 분리한 사본을 반환한다. 실제 선택된 정의의 의미는 기존
AgentDefinition.validate가 검증한다. 선택이 없거나 중복되거나 records가 누락/손상되면 새 정의나
빈 데이터로 대체하지 않는다. 다른 도메인의 정의 export는 그 Owner 전환 작업에 남긴다.

ToolComponent의 저장 layout 선택·명시적 dependency prepare·진단은 클래스 선언부에 둔다.
저장 목록·원자적 쓰기·활성 package 삭제 거부와 실제 prepare 시점은 기존 Owner API를 유지한다.
[검증 기록](whole-ownership-verification.md)에 순수 선언 참조 치환의 AST 동일성, 직접/Hub 소비자와
승인·예산·효과·재개 안전성 및 전체 게이트의 소스를 구분한다.
