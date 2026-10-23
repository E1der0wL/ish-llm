# 편집 조회 snapshot의 소유권

`EditableSnapshot`은 Component 설정·정의의 기존 공개 `{data, version}` 관찰을 소유한다.
`EditableSnapshotFields`의 두 필드가 정규 선언이며 JsonValue의 닫힌 Reader/Writer가 이를
사용한다. 필수 필드 누락·잘못된 타입·unknown 필드를 거부하고 payload는 유효한 JSON 객체로
유지한다. payload의 Component별 의미를 공통 계층에서 해석하지 않는다. version은 Owner가
생성한 불투명 변경 식별자로 보관하며 새 기본값·정수/해시 변환·revision 재계산을 하지 않는다.

생성·공개 Reader·data 속성·Writer는 중첩 사본 경계를 지킨다. frozen 선언만으로 중첩 dict가
불변이라고 가정하지 않는다. 공개 반환 JSON은 기존처럼 편집할 수 있지만 그 수정이 저장
원본이나 이미 읽은 관찰 객체를 바꾸지 않는다. 저장/삭제의 최신 수명·소유권·CAS·트랜잭션
검사는 기존 ComponentData/Component가 같은 workspace 잠금 안에서 수행한다.

독립 Agent의 공개 관찰은 `AgentSnapshot`/`AgentSnapshotFields`가 소유하는
`{agent_id, definition, revision}`이다. AgentDefinition의 식별자·정의 검증과 revision 계산을
재사용하여 서로 불일치하는 정의/지문을 거부한다. AgentRepository는 이미 전체 inventory와
AgentRecord를 검증한 뒤 이 Projection을 작성한다. 공개 snapshot 호출을 우회하는 새 load
경로를 만들지 않는다. Agent 관찰은 저장 envelope나 실행 binding을 대신하지 않는다.

Hub AgentManagement는 AgentSnapshot을 읽고 EditableSnapshot으로 명시적으로 투영한다.
Hub의 생성 결과 `CreatedResource`는 같은 편집 필드 선언을 상속하고 id만 추가하여 기존
`{id, data, version}`을 출력한다. 저장 객체를 추가로 만들거나 조회 결과에 실행 권한을 주지 않는다.

직접 소비는 Hub Tool/Record/Skill 관리·Vision 실패 정리, Skill 고정 참조·Agent 영향 조회,
Refinement Skill binding, Memory 추출 지침·원본 파일 조회/저장 경로까지 연결한다. JSON을
그대로 전달하는 Component/Tool 메서드는 경계 전달이며 새 부분 Reader를 만들지 않는다.
RAG corpus/Checkpoint/프로세스/관측 snapshot은 별도 계약으로 이 두 타입과 혼합하지 않는다.

이번 범위는 snapshot의 봉투와 소비 연결이다. payload 내부의 Prompt/파일/Memory 구조,
Skill 삭제 의존 응답과 공통 참조 프로토콜, Manager 목록/편집 응답의 고정 필드는 전체 전환의
후속 대상이다. 이번 타입 추가를 그 경로까지의 완료 근거로 사용하지 않는다.
검증은 [누적 기록](whole-ownership-verification.md)에 소스·입력·명령·결과별로 남긴다.
