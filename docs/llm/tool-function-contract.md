# Python 함수의 Tool 선언과 인자 Schema

ToolMetadata가 Python 함수의 __tool_contract__/__tool_options__ attribute 이름과 선언·조회
경계를 소유한다. 공개 tool decorator는 여전히 원래 함수 자체를 반환한다. 중복 선언은
거부하고 ToolContract 객체와 options의 읽기 전용 MappingProxy를 유지한다. strict 생략과
명시적 false/true를 구별하며 wire 필드 이름은 ToolDefinition의 정규 선언에서 가져온다.
함수 소비자는 직접 attribute 문자열이나 options 딕셔너리를 해석하지 않는다.

FunctionAnnotations가 허용한 Python 타입 문법·인자 수·검증 진단을 선언한다. Annotated,
Union/PEP 604, Literal, JSON scalar/list/string-key dict의 기존 범위를 유지한다. 인자 이름과
순서·필수 여부·호출 기본값의 원본은 함수 signature다. Pydantic이 만든 Schema의 default를
제거한 후 실제 Python 기본값만 JSON 문법과 원래 인자 Schema로 검증하여 공개한다.
형 변환이나 새로운 기본값을 적용하지 않는다. 명시적 nullable None도 그대로 보존한다.

SchemaKeyword와 object_schema를 사용해 공통 JSON Schema 어휘를 재사용한다. Literal
검증의 임시 enum 객체와 기본값 검증 객체는 문법 확인용이며 별도 정의로 저장하지 않는다.
서명 검증 순서, ToolDefinition 옵션 소비 시점, ToolRegistry의 최종 검증은 유지한다.

인자 제약은 계속 ArgumentConstraint의 mode/choices/value_schema를 읽는다. 원래 Schema와
allOf 교집합을 만들고, fixed 이외의 제약은 생략으로 Python 기본값을 우회하지 못하게 한다.
fixed의 유일한 선택값만 명시적으로 채우며 입력·원래 Schema는 사본으로 보호한다.
JsonObject.validate가 이미 보장하는 객체 타입의 도달 불가능한 중복 검사를 제거했다.

Tool 권한·승인·효과·예산·재시도/재개 계약을 변경하지 않는다. 기존 독립 wire·잘못된 타입/
기본값·원본 Schema 제약·실제 worker·효과/승인 검증을 그대로 사용하며 결과는
[누적 기록](whole-ownership-verification.md)에 소스별로 남긴다.
