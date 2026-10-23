# Tool 등록 정보와 실행 binding 관측

ToolRegistrationBinding은 신뢰한 ToolContract, 선택적 ToolClassification, Application의
execution_binding을 공개 등록 정보로 투영한다. 선택하지 않은 classification/실행 binding은
계속 생략한다. 확장 binding은 기존 JSON encoder가 허용한 Python 모양을 유지하고 사본으로
분리한다. JSON으로 정규화하거나 새 Schema·권한을 부여하지 않는다.

ToolExecutionBinding은 Engine에 제공하는 tools/tool_contracts/tool_policy wire를 소유한다.
내부 policy 속성의 직렬화 이름은 해당 필드 metadata에 선언한다. 과거 독립 ToolPolicy 객체나
실행 경로를 복원하지 않는다. 현재 scope의 관측값일 뿐 정책 합성·승인·호출 한도를 계산하지
않는다. Registry의 공개 definitions()/contracts() 재정의를 그대로 거치며 opaque 정책 값의
Python 모양도 deepcopy로 보존한다.

ToolHandlerBindings는 기존 선택 binder/validator/실행 binding hook의 이름과 조회를 소유한다.
정적 선언 확인은 property나 동적 __getattr__을 실행하지 않는다. 등록 시 retry_fields와 durable
선택의 충돌을 검사하고, 실행 시 정적으로 선언된 binder만 조회하는 기존 차이를 유지한다.
Registry의 저장 선택 검증은 기존 동적 binder/validator 조회 계약을 유지한다.

ToolDefinition.require_object_parameters가 Tool 인자 Schema의 객체 조건을 소유한다. Registry와
모델 정의는 같은 검사를 호출하며 정규 Schema/로컬 참조/선언 일치·인자 제약 검증은 각 원래
경계에서 유지한다. 준비 단계의 중복 객체·finite 검사는 기존 json_copy와 constrained_arguments에
위임한다. 잘못된 입력은 동일 공개 오류로 실패하며 핸들러를 호출하지 않는다.

독립 wire/생략·사본·opaque 확장/정책·공개 Registry 재정의·정적 hook 탐색·등록/준비 거부를
시험한다. 실제 MCP·worker·Agent·승인/예산/효과/재개 소비자와 전체 통합 게이트 결과는
[누적 검증 기록](whole-ownership-verification.md)에 소스별로 구분한다.
