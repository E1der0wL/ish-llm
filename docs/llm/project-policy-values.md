# Project 정책의 선언·실행 관측

기존 정책·승인·예산·Resume 계약을 유지하며 `core.policies`의 필드 선언을 공개 Schema와
실행 관측의 단일 원본으로 사용한다. `PolicySection`은 section 이름, `PolicyDeclaration`은
필드 metadata와 정규화 진단을 소유한다. JSON Schema 문법은 `core.schema`를 참조한다.

`ToolPolicyValues`·`ToolRetryPolicy`·`ApprovalPolicy`·`ApprovalRule`은 검증된 명시 정책의
메모리 사본이다. 생략을 저장 기본값으로 만들지 않는다. `normalize_policies`는 원래 명시 키만
반환하고 공개 Schema는 기존 형식과 같다. 원본·저장 정책, Run이 입장할 때 고정한 정책,
실행 중 공유 사용량은 서로 다른 책임이다. 이 값 타입은 호출 권한이나 별도 저장 원본이 아니다.

ToolExecutionScope는 타입의 속성으로 한도·선택·인자 제약·재시도를 읽는다. 기존 부모 한도
검사·narrowing·예약·실제 효과 순서는 유지한다. 자식 생성도 같은 필드 선언을 사용하며
`max_calls`를 빠뜨릴 수 없다. 기존 실제 효과 기반 R3 mutation은 계속 필수다.

`ToolExecutionBinding`은 typed Tool 정책과 Host 실행 관측을 기존 평평한 binding JSON으로
직렬화한다. 승인·Resume Reader와 비교에 쓰이는 기존 wire를 유지하며 별도 파일에 저장하지
않는다. 선택 callback이 없으면 `retry_selector`는 계속 생략한다. 중첩 dict/list는 사본이다.

승인 규칙의 순서·유일한 ID·risk scheme·정확한 정수 위험도 검증은 ApprovalPolicy가 소유한다.
서비스는 유효한 규칙을 읽더라도 현재 응답·취소·만료·재시도 분류를 확인하고 기존 응답 저장
경계를 통과해야 한다. 이 관측이 실행 재개 또는 Host 허용 검사를 대신하지 않는다.

Hub의 Tool 재시도 표시는 같은 저장 Run 정책의 타입을 사용한다. 필수 `policies` 스냅샷 누락을
빈 설정으로 복원하지 않는다. 선택 횟수·delay·취소·효과 판단은 기존 Backend 계약을 따른다.
Context/Run/Output 소비자도 각 Owner의 관측 API를 사용한다. TokenCounters가 기본 계산기의
등록 이름·검증·명시 override를 한곳에서 소유하며 정책이나 token quota를 암묵적으로 활성화하지 않는다.

변경 검증은 독립적인 기존 JSON/오류·효과 Assertion, 소비 필드 누락 검사, 승인·예산·중단·재개와
삭제 보호를 포함한다. [Level 1–3 정책](verification-policy.md)에 따라 공통 정책 변경의 전체
통합 게이트 및 정확한 Push SHA CI를 확인한다. 주변 다른 고정 구조의 미전환은 전수 감사에 남긴다.
