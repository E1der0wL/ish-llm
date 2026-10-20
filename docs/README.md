# 개발 문서

문서 기준은 main `94229cb865374d99f20ec48c5e48269488041a4e`다. 2026-10-20에
`docs/` 전체와 `llm/`, `hub/` 내부 문서를 재검토했다. 이번 변경은 문서 정리이며 제품 구현이 아니다.

## 읽는 순서와 적용 범위

1. [현행 아키텍처](llm/architecture.md): 현재 제품의 도메인·실행·저장 소유권.
2. [명시적 설정](../llm/CONFIGURATION.md), [설정 소유권](llm/project-authority.md),
   [Schema 소유권](llm/schema-ownership.md), [명명 규칙](llm/naming-conventions.md).
3. [안전성 계약](llm/shared-contracts.md), [체크포인트](llm/checkpoints.md),
   [트랜잭션](llm/storage-transactions.md), [승인](llm/interactions.md), [격리](llm/process-isolation.md).
4. [현행 공개 API](llm/backend-api.md), [LLM 사용법](../llm/README.md),
   [Hub 설계](hub/README.md), [Hub 사용법](../hub/README.md).
5. [클린 리팩터링 목표와 단계](llm/clean-refactor.md): 승인된 설계 방향이며 **아직 구현되지 않았다**.
6. [전체 문서 목록](document-map.md), [문서·코드 불일치와 검증 상태](llm/documentation-review.md).

사용자의 최신 필수 원칙·단일 선언 지침 → 현재 명명의 의미와 명시적 설정 계약 →
A–F 기능·안전성 요구 → 과거 작업 기록 순서로 적용한다. 코드가 문서와 다르다는 이유만으로
유효한 안전성 계약을 버리지 않는다. 반대로 목표 API를 현행 사용법에 넣거나 미구현 기능을
완료로 표시하지 않는다. 충돌은 검토 기록에 근거와 처리 단계를 남긴다.

## 현행 계약과 구현 차이

정식 출시 전이므로 새 구현에 구 API·구 저장 형식·자동 migration·호환 Facade를 만들지 않는다.
필수값 누락은 명시적으로 거부하고 Missing/Null/Value를 구분한다. 버전·상태·이벤트·오류·정책·경로·
한도는 해당 구현체가 한 선언으로 소유한다. 제품에 남은 위반은 문서 수정으로 해결된 것이 아니다.
[알려진 차이](llm/documentation-review.md)를 구현 단계에서 제거하고 검증한다.
소유자와 저장 출처가 명확한 [Hub 생성 초기값](hub/presets-and-resume.md#hub-초기값의-소유권과-저장-계약)은 허용한다.
사용자 명시값 우선·조회/편집·재열기 시 재주입 금지·Backend 검증을 지키며 매번 수동 선택을 요구하지 않는다.

현재 공개 API의 일반 Facade, sync/async 호출, 실제 Provider Adapter는 구 API 호환 계층과 다르다.
Tool 승인·권한·한도·Checkpoint·Resume·효과 영수증·동시성 안전성은 유지한다.

## 과거 기록

- [LLM 인계 기록](llm/handoff.md), 날짜별 review/audit/validation 문서: 당시 요구·변경·검증.
- [이전 아키텍처 본문](llm/architecture-history.md), [이전 API 안내](llm/backend-api-history.md),
  [이전 운영 준비 기록](llm/production-readiness-history.md), [Hub 개발 기록](hub/history.md).
- 과거 기록의 `Latest`/“현재”/통과 수는 작성 당시 의미다. 현재 검증 결과로 합산하지 않는다.
- 예제는 `examples/<plugin>/`, 검사 기록은 `tests/<plugin>/reports/`에 둔다.
  코드 옆 README는 각 구현체의 공개 계약을 설명한다.
- `hub/asset/prompts/`와 `hub/asset/skills/`는 제품 실행에 쓰이는 자산이다. 문서 감사 대상이지만
  단순 설명문처럼 변경하지 않는다. 이번 문서 정리에서 내용은 보존했다.
