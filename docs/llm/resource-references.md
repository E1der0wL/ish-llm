# 보존된 정의의 공통 참조 관찰

Component와 Agent/Engine/Workflow handler는 자신의 보존된 정의를 해석한다. 공통 참조
계층은 해당 Owner가 공개한 대상·출처·선택적 경로만 읽어 삭제/선택 보호 대상을 모은다.
별도 역참조 DB나 운영 기록을 저장하지 않고, 조회가 정의·파일·모델·Tool을 실행하지 않는다.

`ResourceTarget`의 component/identifier가 정규 대상 선언이다. identifier=null인 기존 RAG
참조는 Component 단위 대상이며 개별 기록 식별자로 변환하지 않는다. Engine의
agent_references는 기존처럼 정확히 두 필드의 비어 있지 않은 문자열 대상이어야 하며
`from_engine`이 그 더 좁은 경계를 검사한다. Engine 목록의 tuple 요구도 유지한다.
Agent의 참조 namespace는 AgentDefinition.REFERENCE_COMPONENT가 소유한다. 다른
대상 Component 이름은 실제 Component.name 선언을 참조한다.

`ReferenceFields`는 대상 선언을 상속하고 source와 선택적 path를 선언한다.
`StoredResourceReference`가 필수 identity·타입과 JSON 문법을 검증하고 typed 속성,
일치 판정, dependent Projection을 제공한다. 누락된 필수 identity를 빈 값으로 채우지 않는다.
선택적 path의 생략/null은 조회 의미상 모두 경로 전체에 해당하지만 원본 출력에서는 그
차이를 보존한다. revision·metadata 등 확장 근거는 원래 Owner의 검증 책임이며 공통 계층은
추측하거나 정규화하지 않는다. 입력/출력 중첩 자료는 사본으로 보호한다.

ComponentRegistry와 ProjectReferences는 동일한 matches 판정을 사용한다. 기존 조건은
component 일치, 조회 identifier 생략 시 전체 또는 지정 ID 일치, 조회 path 생략 시 전체 또는
참조 path 생략/null/지정 경로 일치다. Component 전체 참조의 null 식별자를 임의의 기록 ID와
같다고 처리하지 않는다. 보존된 비활성 Component도 계속 읽으며 필요한 Owner가 없거나
필수 근거가 손상되면 삭제를 허용하지 않는다. 실제 쓰기 시점의 workspace 잠금·CAS·
트랜잭션 검사를 유지한다.

`AgentUse`는 Workflow가 handler를 통해 보고하는 identifier/source 관찰을 소유한다.
Workflow 내부 사유 선택·노드 해석은 기존 handler가 유지한다. AgentManager는 그 공개
관찰을 읽으며 factory/handler 실행을 통해 참조를 추측하지 않는다.

Skill의 impact/dependencies는 AgentDependency, SkillChild, SkillDependencies로 작성하고
공개 경계에서 같은 JSON으로 직렬화한다. 삭제는 typed has_dependents 판정을 사용한다.
이는 현재 정의의 관찰이며 실행 binding·승인·수정 권한이나 영속 참조 인덱스가 아니다.
Skill lineage와 Agent resources의 기존 의미는 [Revision 계약](revisions.md)을 따른다.

공통 참조 프로토콜과 소비 연결을 전환한 범위다. 각 Component의 사유 정의 내부 필드와
history_references의 별도 Run/Message/Session 참조 계약은 전체 후속 대상에 남는다.
검증 결과는 [누적 기록](whole-ownership-verification.md)에 독립 실행별로 기록한다.
