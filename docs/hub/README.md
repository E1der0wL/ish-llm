# Hub 설계 문서

기준: main `94229cb865374d99f20ec48c5e48269488041a4e`. Hub는 현재 동작하는 ish 인터페이스
플러그인이다. 이 문서는 현재 책임과 정규 문서의 진입점이다. 날짜별 개발 내역과 이전 UX 설명은
[개발 기록](history.md)에 보존했다. [클린 리팩터링 목표](../llm/clean-refactor.md)는 미구현 상태다.

## 책임과 계약

Hub는 모델·prompt·실행 제한·검색 전략 등 제품 선택을 새 Project의 명시 설정에 기록한다.
Backend는 그 계약을 검증·집행한다. Host ceiling으로 저장된 Project 권한을 덮어쓰지 않으며
UI effective values를 새 설정 기본값으로 저장하지 않는다.
해당 Hub 기능/프리셋이 소유한 생성 초기값은 매번 사용자 선택 없이 제공할 수 있다.
사용자 값 우선·정규 저장/조회/편집·기존 프로젝트 재주입 금지·Backend 검증과 독립성은
[Hub 초기값 계약](presets-and-resume.md#hub-초기값의-소유권과-저장-계약)을 따른다.

| 소유자 | 책임 | 정규 문서 |
|---|---|---|
| bootstrap/config/Project 서비스 | 구현 주입, 앱 생성 preset, 설정·선택·CAS | [구조](structure.md), [설정/재개](presets-and-resume.md) |
| backend worker | 이벤트 루프, 공개 llm API와 실행 수명 | [렌더링 파이프라인](rendering-performance.md), [유지보수 경계](maintenance.md) |
| 요청 환경 | Enter 시점 cwd/env 사본, Tool·path revision binding | [셸 환경](shell-environment.md) |
| backend 오류 계약 소비 | Diagnostic 보존·backend admission·앱 선택 | [오류](error-handling.md), [Provider 복구](provider-recovery.md), [작업 복구](operation-recovery.md) |
| Tool UI/선택기 | 승인·질문 표시, 허용된 인자 수정 제안 | [질문과 Tool](questions-tools.md), [Tool retry](tool-retry.md) |
| 자원 편집 | 현재 Project의 Schema·CRUD·CAS·참조 보호 | [관리 창](component-managers.md), [authoring](resource-authoring.md), [RAG](rag.md) |
| UI | immutable snapshot, 키·마우스·출력·설정 초안 | [홈](home-navigation.md), [패널](panel-ux.md), [출력 객체](output-objects.md), [마우스](mouse.md) |

승인 응답과 실행은 별개다. Hub는 공개 resume_plan/resume/restart와 revision을 사용하며,
불확실한 효과나 checkpoint 부재를 이유로 새 submit을 자동 호출하지 않는다. 별도 Run/Step 저장소,
Provider retry loop, 오류 classifier를 만들지 않는다. UI 질문·모델 분석·렌더링은 실행 권한이 아니다.

현재 bootstrap은 사용자 classifier가 없을 때 `classify_model_risk`를 연결한다. 이는
`hub-model-risk-v1`로 모델의 위험 설명을 표시하는 앱 선택이다. trusted classifier preset의
`hub-risk-v1`과 구분한다. 미분류/unknown은 점수가 없고 모델 설명 자체는 승인 결정이 아니다.
[알려진 구현 차이](../llm/documentation-review.md)의 명시 선언 정리 대상이며,
“기본 classifier가 없다”는 과거 설명을 현행 계약으로 사용하지 않는다.

## 정규 API 전환

현재 Hub는 명시 Engine 요청과 AgentComponent를 사용한다. B/C 단계에서 독립 Project Agent와
단일 Run→Agent→Engine API로 직접 이관한다. 제목 생성·일반 대화·Graph 위임·자원 편집·환경 캡처를
같이 검증하며 호환 wrapper를 추가하지 않는다. D의 다중 Run에서도 UI 상태는 backend 원본의 투영이다.

옛 preferences/theme/profile 변환과 환경 없는 구 Run fallback은 main에 남아 있다.
이 문서 정리로 제거된 것이 아니며 [제거 계획](../llm/clean-refactor.md)에 따라 현재 Reader 하나로 바꾼다.
현재 저장값을 문서 작업 중 변환하거나 삭제하지 않는다.

## 사용법·검증 기록

- [Hub README](../../hub/README.md): 설치·실행·현재 설정.
- [요청 이력](request-history.md), [Completion 표시](completion-display.md),
  [사용량](usage-and-system-messages.md), [코딩 가이드](coding-workflow.md).
- [문서 목록](../document-map.md), [기준선과 미검증](../llm/documentation-review.md).
- `backend-contract-handoff.md`, 구조 감사, 각 문서의 날짜별 검증 절은 과거 기록이다.
  당시 X11 Skip·모의 Provider·특정 snapshot 결과를 현재 전체 통과로 승계하지 않는다.
