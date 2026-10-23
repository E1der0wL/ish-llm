# Tool 실행 선언의 단일 Owner

`llm/core/tool_contracts.py`의 `ToolContract`, `ToolClassification`, `ToolEffect`,
`ToolIsolation`이 신뢰한 Application/개발자 실행 선언을 소유한다. 등록·실행·Component·
Provider·Hub는 같은 타입을 사용한다. 기존 `llm.components.tools` 공개 import는 이 타입을
직접 내보내며 별도 Reader나 호환 실행 경로를 만들지 않는다.

`ToolContract`의 dataclass 필드가 정규 선언이다. Writer와 닫힌 Reader는 해당 필드에서
직렬화 구조와 필수 집합을 파생한다. effect/isolation은 Enum 값으로, retry_fields는 저장 시
배열로 표현한다. 여섯 실행 선택은 계속 명시해야 하며, False와 빈 retry_fields를 보존한다.
등록부는 여전히 실행 직전 계약과 인자 Schema의 연결을 검증한다. 승인 판단은 Owner가
읽은 `approval_required` 속성을 사용하며 모델 설명·Project 저장값으로 권한을 만들지 않는다.

기존 저장 JSON, descriptor fingerprint, 승인·재시도·격리·효과의 의미는 그대로다.
ToolRegistry의 나머지 binding, worker IPC 및 주변 모듈의 전수 정리는 별도 미완료 범위로
유지한다. 이 선언의 일원화나 좁은 정적 검사 통과만으로 전체 완료를 선언하지 않는다.
