# 영속 기록의 생성·수정 시각

영속 도메인과 Component가 소유하는 공개 CRUD 대상의 관리 시각은 `core.record_times.RecordTimes`가 의미와
검증을 소유한다. Application이 선택하는 정책값이나 사용자 정의 본문이 아니다.
Skill/Data 패키지, Prompt/Workflow/MCP/Goal/Refinement 정의, Memory 기록, Vision 이미지,
Project Python Tool과 RAG 문서, Project·Session·Agent·Run·Step에 적용한다.
내부 캐시·인덱스 파일마다 시각을 만들지 않는다. Run/Step의 `started_at`·`ended_at`,
Job의 진행 시각과 외부 서버가 소유하는 자료 시각은 별도 의미로 유지한다.

## 의미와 저장

- `created_at`: 논리 대상의 최초 생성 시각. 생성 후 유지한다.
- `updated_at`: 대상 레코드의 실제 내용·정의·참조·상태 필드 변경이 성공적으로 저장된 시각.
- UTC 시간대가 명시된 ISO 8601 문자열이며, 처음에는 두 값이 같다. 시스템 시계는 역행할 수
  있으므로 시각 자체를 순서 토큰·revision·CAS·승인 binding 또는 무결성 증거로 쓰지 않는다.
  기존 전체 저장 기록의 hash와 삭제·복구 계획은 필수 시각을 포함한 원본 전체를 계속 검증한다.
  이는 원본 변경 감지이며 날짜의 동일함/대소로 CAS를 판단하거나 본문 검증을 생략하는 것이 아니다.
- 조회·검증·사용량 영수증·캐시 갱신·같은 내용 재저장은 대상의 수정 시각을 바꾸지 않는다.
- 원본과 시각을 같은 기존 트랜잭션으로 확정한다. 저장 실패의 트랜잭션 rollback은 함께 복원하며 별도
  중앙 원장, 시간 기록 전용 fsync, 숨은 TTL/용량/모델/재시도 설정은 추가하지 않는다.
- 저장 원본은 해당 Component의 기존 필수 관리 기록이다. 이미 본문/이력에서 시각을 소유하는
  구현은 기존 값을 사용한다. 공통 저장 계층은 child의 필드명을 추측하지 않고 선언된 reader에
  위임한다. 동일 의미의 시각을 두 원본에 중복 저장하지 않는다.
- 공개 `get_times/aget_times`는 읽기 전용 관리 정보다. 관리 시각은 사용자 definition의
  임의 필드나 Application metadata에 삽입하지 않는다.

## 도메인 기록

Project·Session·Run·Step은 기존 필수 저장 레코드에 `created_at`과 `updated_at`을 둔다.
새 객체는 생성 시 두 값이 같고, 저장된 객체는 두 값 모두 필수다. 생성 인자의 초기화와
저장 형식 검증을 구분하며, missing/null을 과거 자료 호환 경로로 사용하지 않는다.
수정 시각은 해당 Repository가 이미 읽고 검증한 이전 기록과 비교해 결정한다. 내용과 시각은
기존 한 번의 기록 쓰기에 함께 포함하며, 별도 파일/원장/동기화를 추가하지 않는다.
이미 조회한 도메인 객체가 이후 Manager 저장으로 자동 최신화된다고 가정하지 않는다.
최신 시각은 해당 소유자의 조회 API로 다시 읽는다.

`updated_at`은 해당 레코드 자체의 변경 시각이다. 자식 Step/Agent/Component 자료나 별도
Conversation/output journal 변경은 부모 시각으로 전파하지 않는다. Session의 active Run 목록처럼
부모 자체의 영속 필드가 바뀔 때는 그 부모의 시각도 갱신된다. 따라서 프로젝트 전체의 마지막
활동, 마지막 토큰, 실행 순서 또는 보존 정책의 기준으로 대체하지 않는다.

Run/Step 상태·오류·metadata 수정은 관리 시각을 갱신하지만 기존 시작·종료 증거를 다시 만들지
않는다. 재개는 새 Run을 생성하고 원본의 생성/실행 시각을 유지한다. 저장 실패와 transaction
rollback은 시각도 기존 기록 및 메모리 객체와 함께 복원한다.

Agent의 Repository는 definition 밖의 기존 AgentRecord envelope에 시각을 저장한다.
`project.agents.get_times/aget_times(identifier)`가 읽기 전용 관찰 API다. definition snapshot,
Agent revision, CAS와 승인 binding에는 관리 시각을 넣지 않는다. 동일 definition 재저장은
시각과 revision을 유지하고, 독립 Project로 Agent를 복제하면 새 대상의 시각을 생성한다.

Hub에서 Component/Agent 관리 시각을 표시할 때는 소유자의 API를 사용해야 한다. 기존 Hub의
`metadata.created_at/updated_at` 작성과 Skill Markdown 날짜 삽입은 이 저장 계약의 원본이 아니다.
Hub 표시/작성 경로 변경은 별도 Application 작업이며 자동 metadata 정리/이전은 하지 않는다.

독립적인 새 대상 생성·fork·원본 가져오기는 새 시각을 기록한다. 백업 복원은 저장된 시각을
보존한다. 기존 generation/이력 자체를 보존하는 복사 계약은 당시 시각도 보존한다. 과거
Memory 이력·승인·효과 영수증을 새 시각으로 다시 작성하지 않는다.
이미지 원본 identity를 보존하는 복사 역시 기존 원본의 시각을 보존한다. 격리 영수증이나 Job처럼
별도 수명을 가진 기록의 사건 시각을 원본 내용의 수정 시각과 혼동하지 않는다.
사용자가 명시한 Refinement rollback처럼 이전 내용을 다시 적용하는 새 변경은 `updated_at`을 갱신한다.

## 공개 API와 확장

```python
times = await project.components["data"].aget_times("manual")
created_at, updated_at = times["created_at"], times["updated_at"]
```

기본 Component는 필수 record manifest에 저장한다. 고유 저장 형식이 있는 Component는
`get_times(project, identifier)`를 구현한다. 기본 record 저장을 쓰되 이미 본문에서 시각을
소유한 구현은 `read_times(record) -> RecordTimes`를 선언하고 자신의 Writer에서 관리한다.
공통 계층은 이 선언만 사용하고 Memory/Vision 등 구현 이름이나 private 필드를 해석하지 않는다.

RAG의 `get_times`는 일반 metadata record를 대상으로 한다. 별도 문서 API가 소유한 문서의
시각은 `aget_document`와 `alist_catalog`에서 제공한다. 준비/checkpoint 단계에서는 새 시각을
확정하지 않으며 corpus 공개 때 기존 원본과 함께 확정한다. 조회·변경 없는 재등록과 내부
처리 계수 갱신은 문서의 시각을 변경하지 않는다.
등록·수정 API는 준비 데이터가 아니라 실제 공개된 문서의 시각을 반환한다. 이미 공개된 Job의
복구 비교는 `RAGDocument.matches_prepared`가 소유하며 공개 시 추가되는 관리 시각만 제외한다.
원문·metadata·벡터·세대·revision·설정/추출 증거의 차이는 계속 재사용을 거부한다.
RAG generation 자체를 보존하는 Project 복제·백업 복원은 기존 문서 시각도 보존한다.

새 저장 계약의 필수 시각이 없거나 잘못되면 fail-fast한다. 이전 파일은 보존하며 현재 시각,
filesystem mtime으로 보정하지 않는다. 이전 형식 reader, migration, compatibility alias는 없다.
외부 원본을 사용자가 새로 등록하는 것은 신규 생성이며 이전 저장 자료의 자동 채택이 아니다.

## 조회 검증 재사용

검증한 불변 자료를 같은 조회/준비 작업에서 전달하여 중복 읽기를 줄인다. 다음 독립 조회에서는
원본·목록·파일 무결성을 다시 확인한다. 권한·현재 참조 revision 검사를 생략하거나 외부 변경을
무시하는 캐시는 만들지 않는다. 파일 범위 읽기의 CAS 검사는 파일 소유자가 같은 검증 결과로
수행한다. Skill 실행 준비는 참조 검증과 공개 `resolve()` 확장 호출을 유지한다. Host가 이
경로에서 조회 도구를 감쌀 수 있으므로 해당 두 조회를 우회하는 최적화는 적용하지 않는다.

시각 기록은 이 재사용의 유효성 근거가 아니다. 최초 전체 검사 비용은 남으며, 파일 읽기 횟수와
바이트 수를 측정하여 개선 범위를 보고한다. 생산 저장/검증 코드와 실제 적용·회귀 결과는 구분한다.
