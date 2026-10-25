# Tool 활성화 선택의 단일 Owner

`llm/components/tools/selection.py`의 ToolSelection.enabled 필드 metadata가 선택 설정의
정규 선언이다. Python Tool과 내장 Tool Component는 여기서 config Schema와 parameter_layout
필드를 파생한다. Reader는 같은 Schema로 목록/문자열/유일성/미지 필드를 검증한다.
중복된 Component 검증 구현은 제거한다. 경계별 현재 설정 검증은 유지한다.

- 설정 생략은 선택 없음으로 조회하지만 Writer는 생략을 유지한다. 명시적인 빈 배열은 그대로
  저장하고 null은 거부한다. Backend가 Tool을 암묵적으로 활성화하지 않는다.
- add는 기존 순서를 보존하면서 한 번만 추가하고 remove는 지정한 이름만 제거한다. 명시적
  활성화/비활성화 API 호출은 기존처럼 선택 배열을 저장한다. 반환 목록은 원본과 분리한다.
- 파일 식별자 검증은 기존 validate_name에 위임하고, 패키지 존재 여부·잠금·수명·저장/트랜잭션은
  ComponentData/Component가 소유한다. Builtin의 현재 등록 목록과 실행 계약 검증도 유지한다.
- Hub의 새 Project 기본 선택은 Application이 명시적으로 선택하는 값이다. 이미 설정한 배열과
  빈 배열은 보존한다. 선택 Owner는 기본 Tool 목록이나 sandbox 가용성을 계산하지 않는다.
- Hub 관리 화면도 같은 Reader와 변경 API를 사용하며 기존 expected_version 검사를 유지한다.
  화면 행의 enabled boolean은 별도의 조회 표현으로, 설정의 enabled 배열과 혼동하지 않는다.

공통 Schema 어휘에 enum/uniqueItems 선언을 추가했으며 기존 Schema 함수의 구현은 그대로다.
UI 설명과 Builtin choices를 포함한 기존 Schema 직렬화 및 오류 순서를 보존한다. 나머지 공통
Schema 소비자의 후속 전수 정리는 [최종 점검](contract-closure.md)에서 확인한다. 소단계와 전체 검증은 구분한다.
