# 개발 문서

현재 `main`의 사용법과 계약은 [LLM 문서 안내](llm/README.md)와 [Hub 문서 안내](hub/README.md)에서 찾습니다.
최신 작업 인계와 검증 범위는 [handoff](llm/handoff.md)를 확인하세요.
`refactor/llm-clean`의 변경은 main에 병합되었습니다. 과거 작업 브랜치 지시와 당시 통과 수는
현재 개발·배포 상태를 대신하지 않습니다.

## 읽는 순서

1. [LLM 사용법](../llm/README.md), [공개 API](llm/backend-api.md), [Hub 사용법](../hub/README.md).
2. [아키텍처](llm/architecture.md), [설정](../llm/CONFIGURATION.md),
   [Project 권한](llm/project-authority.md), [명명 규칙](llm/naming-conventions.md).
3. [계약 소유권](llm/contract-ownership.md), [Schema 소유권](llm/schema-ownership.md),
   [공통 실행 계약](llm/shared-contracts.md), [체크포인트](llm/checkpoints.md),
   [트랜잭션](llm/storage-transactions.md), [승인](llm/interactions.md).
4. [검증 정책](llm/verification-policy.md), [테스트 실행](../tests/README.md),
   [운영 준비 상태와 한계](llm/production-readiness.md).

## 현재 계약과 과거 기록

현재 계약 문서는 소스의 공개 API와 함께 관리합니다. 단계별 작업 계획·검증 기록은 당시 범위와
정확한 소스 SHA를 설명하는 증거입니다. 예전 실패가 지금도 남아 있다거나 예전 통과가 현재 변경을
검증했다고 추론하지 않습니다. 계약과 코드가 충돌하면 코드의 동작을 숨기지 않고 별도로 검토합니다.

최초 계획과 검증 증거는 [클린 리팩터링](llm/clean-refactor.md), [전수 소유권 점검](llm/contract-closure.md)에
연결되어 있습니다. 현재 CI가 사용하는 검토 manifest와 증거 문서는 유지합니다.
중복된 옛 사용 안내·누적 인계 기록은 [보관 목록](llm/documentation-history.md)에서 고정 커밋으로 읽을 수 있습니다.
[전체 문서 목록](document-map.md)은 현재 파일을 분류하며 과거 전수 검토 결과를 새 검토 결과처럼 재사용하지 않습니다.

새 테스트 로그·스냅샷·임시 검증 스크립트는 `tests/<plugin>/reports/`, 개인 스크립트는
`tests/<plugin>/manual/`, 실행 예제는 `examples/<plugin>/`에 둡니다. 이들은 배포 플러그인에 포함하지 않습니다.
코드 옆 README는 해당 구현체의 공개 계약을 설명합니다. 제품 prompt/skill 자산은 실행 코드의 일부로 취급합니다.
