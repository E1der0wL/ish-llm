# Revision 계약

Revision은 소유 객체의 상태 또는 실행 계약을 구별하는 변경 식별자다.
Component 자료에서는 `(Project, Component, identifier)`가 대상의 범위를 결정한다.
저장 형식의 `storage_version`/`schema_version`과 구분한다. 기존 `version`,
`expected_version`, `config_version`이라는 CAS 필드 이름은 그대로 유지한다.

- 소유 구현체가 계산 범위를 정한다. 소비자는 값을 분해하거나 대소 비교하지 않는다.
- JSON 정의는 기존 `revision_token`의 정렬된 JSON SHA-256을 사용한다. 같은 내용은
  같은 revision이다. 변경 순서가 필요한 Memory/RAG의 정수 revision은 유지한다.
- 확인과 쓰기는 같은 workspace 잠금·트랜잭션 안에서 수행한다.
- Skill/Data 패키지의 정의에는 관리되는 `files` 목록(상대 경로, SHA-256, byte 수)이
  포함된다. 따라서 기존 JSON snapshot/승인/Refinement 지문에 파일 변경도 포함된다.
  파일은 공개 파일 API로 수정한다. 외부 편집으로 manifest와 불일치하면 자동 보정하지 않는다.
- `expected_revision`은 호출자가 읽었던 패키지 revision이다. 파일 변경 API에는 필수다.
  정의 편집의 기존 `expected_version` 계약은 유지한다.
- 고정 참조는 대상 identity와 기대 revision을 저장한다. 대상 변경 시 부모를 자동 수정하지
  않는다. 사용 시 불일치를 거부하며 호출자가 참조를 명시적으로 갱신한다.
  현재값 참조와 출처 기록은 아래와 같이 별도 계약을 갖는다.
- 실행은 시작 시 선택한 지문에 묶인다. 지침/자료가 바뀌면 진행 중 자료 조회와 옛 승인·재개는
  변경된 자료를 조용히 사용하지 않는다. Tool 권한과 승인 결정은 기존 실행 경계가 소유한다.
- revision은 과거 버전 보관이 아니다. Skill/Data는 현재 상태만 보관하며 자동 이력/복원을
  제공하지 않는다. 참조 제거와 삭제는 별도 작업이며 자동 cascade는 없다.

| 대상 | 기존 표현과 소유자 | 이번 적용 |
| --- | --- | --- |
| Project/Component 설정 및 일반 정의 | 서비스 snapshot의 `version`, `expected_version` CAS | 이름·저장 계약 유지 |
| Skill/Data | 정의와 파일 manifest의 JSON hash | 파일 변경도 동일 snapshot에 포함 |
| Agent/Workflow 정의와 실행 binding | 각 소유자의 정의 hash 및 실행 snapshot | 기존 검증 경로 유지 |
| Memory/RAG 문서 | 생성 시 고정되는 generation + 변경마다 증가하는 정수 revision | 두 값을 함께 비교하여 같은 ID의 삭제·재생성도 구별 |
| Memory 파생 요약 | 캐시 generation + 정수 revision | 공개와 삭제 모두 두 값 비교 |
| ToolContract, ToolRuntime, Engine/handler | 신뢰한 구현체가 선언하는 revision | 코드/실행 계약 변경 시 Host가 갱신; 파일 hash로 대체하지 않음 |
| storage/schema version | 저장 형식의 버전 | 이번 작업에서 변경하지 않음 |

같은 명칭의 의미를 통일하되 모든 revision을 동일한 타입이나 중앙 카운터로 바꾸지 않는다.
소비자는 소유자가 제공한 snapshot/revision 비교 계약을 사용한다.

## 정수 revision과 생성 세대

Memory 기록과 RAG 문서는 같은 ID로 다시 생성하면 정수 revision이 다시 1부터 시작한다.
따라서 revision 숫자만으로는 이전 기록과 새 기록을 구분할 수 없다. `generation`은 생성 시
소유 Component가 발급하는 32자리 hex 식별자이며 일반 수정·soft delete·restore 동안 유지한다.
영구 삭제 후 재생성하면 바뀐다. 비교 범위는 `(Project, Component, identifier, generation, revision)`이다.
복제·백업 복원은 저장된 쌍을 보존한다. 이 값만으로 다른 Project에 대한 권한은 생기지 않으며,
접근 대상은 기존 workspace/Project 핸들의 수명·범위 검사로 결정한다.

- Memory save/update/delete/restore/purge/consolidate는 읽은 `expected_generation`과
  `expected_revision`을 모두 요구한다. 모델용 memory_update/memory_delete도 동일하다.
- Memory `replaces`는 `{id, generation, revision}`이다. 추출 모델에 이 세 값을 제공하고,
  후보 공개와 최종 통합에서 검증한다. 이전 후보가 같은 ID의 새 기억을 대체할 수 없다.
- Memory clear_summary는 읽은 요약의 두 값을 요구한다. 요약 공개도 기존 쌍을 검증한다.
- RAG update/delete의 CAS를 사용할 때 두 값을 함께 전달한다. 하나만 전달하면 오류다.
  둘 다 생략한 기존 무조건 변경 API는 유지하므로 UI 편집에서는 반드시 읽은 쌍을 전달한다.
  준비 중 코퍼스 변경 검사는 무조건 변경에서도 유지된다.
- RAG 문서의 generation은 `active.json`이 가리키는 코퍼스 공개 generation과 다르다.
  다른 문서의 변경은 기존 문서의 generation을 바꾸지 않는다. 검색 documents/sources에도
  문서의 쌍을 반환하며 Job 재시도는 이미 준비한 문서의 generation을 보존한다.

검증과 변경은 기존 잠금·원자적 저장 경계에서 수행한다. generation은 사용자 metadata가 아니며
직접 지정·수정할 수 없다. 기존 승인 receipt와 체크포인트의 Tool 인자에는 기대 쌍이 포함되어,
재개 시에도 실행 대상 Component가 과거 쌍을 거부한다. 승인 자체가 데이터 충돌을 우회하지 않는다.

generation이 없는 기존 Memory/RAG 저장 기록은 거부한다. 자동 발급, 옛 숫자만의 CAS,
자동 migration은 제공하지 않는다. RAG 백업 생성·복원에서도 같은 문서 버전 검증을 적용한다.
필요한 원문은 변경 전 API로 내보내고 새 CRUD로 재등록한다.
저장/schema version 숫자는 변경하지 않는다.

Skill 정의의 원본은 `skill.json`이다. `SKILL.md`를 두 번째 수정 가능한 원본으로 만들지
않는다. 이전 `skills/records/*.json`은 자동 변환하지 않는다. 필요한 자료를 기존 형식으로
내보낸 뒤 새 공개 CRUD로 명시적으로 재등록해야 한다.

## 참조와 수명 관리

Skill.resources의 Data/Tool 참조는 대상 component/identifier/revision을 저장한다.
Data 참조에는 path도 필요하다. 생성·수정과 Run capability 해석, 실제 자료 조회 시 검증한다.
대상 업데이트는 부모를 변경하지 않으며 stale 참조는 명시적으로 고쳐야 한다.
URI는 단순 설명이므로 원격 revision 검증이나 삭제 보호를 제공하지 않는다.

Agent.resources.skills는 ID 기반 현재값 참조다. Run 시작 시 현재 Skill 정의와 파일 manifest를
읽어 실행 snapshot에 고정한다. Agent 정의에 Skill revision을 자동 삽입하지 않는다.
Skill.lineage의 parent_revision은 FORK 당시 부모의 출처 기록이다. 부모가 변경돼도 자식이
실행 불가능해지는 고정 참조가 아니며, 부모 ID의 삭제 보호는 별도로 유지한다.

삭제 보호는 각 Component의 references()에서 계산한다. 별도 역참조 DB를 만들지 않는다.
등록된 Component가 선택 해제된 상태라도 보존된 정의의 참조는 삭제를 차단한다.
Skill의 impact/dependencies 조회도 선택 해제된 Agent의 참조를 포함하여 삭제 보호와 일치한다.
Host는 저장소를 소유한 Component 구현 등록을 유지해야 하며 등록되지 않은 임의 플러그인의
자료 의미를 backend가 추측하지 않는다. 참조된 Data/Tool 삭제, Skill parent/Agent 참조 삭제,
Component의 영구 제거에도 같은 경계를 적용한다.

복제와 백업은 정의·파일을 함께 보존한다. 백업은 stale 참조를 새 버전으로 갱신하지 않는다.
Refinement FORK는 부모의 파일을 복사하고 부모 revision을 lineage에 남긴다.
파일 변경도 적용/rollback CAS에 포함된다. 기존 승인 receipt·Run/Step·재개 binding을 사용하며
새 승인 저장소나 재개 방식은 도입하지 않는다. metadata는 실행 권한의 근거가 아니다.
