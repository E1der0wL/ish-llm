# Hub 설계 문서

Hub는 현재 동작하는 ish 인터페이스
플러그인이다. 이 문서는 현재 책임과 정규 문서의 진입점이다. 날짜별 개발 내역과 이전 UX 설명은
[개발 기록](history.md)에 보존했다. [클린 리팩터링 목표](../llm/clean-refactor.md)는 미구현 상태다.

## 책임과 계약

[다른 플러그인의 Agent 연동](plugin-api.md)은 공유 Hub worker를 통한 등록·내부 세션·
엔진 공통 출력 전달과 명시적 승인/재개 경계를 설명한다. 설정은 기존 `/agent` 관리 창을 사용한다.

[공통 표시 형식](ui-formatting.md)은 헤더·상태·목록·안내·설정 항목의 표시 소유권과
현재 UI 확인 범위를 설명한다.

[장시간 명령 중 추가 지시](command-steering.md)는 기존 프로세스 도구를 이용한
대화용 명령 관측, 지시 접수 후 대기 해제와 원래 요청 말풍선에 표시하는 경계를 설명한다.
[추가 지시 UI와 실행 상세](steering-feedback.md)는 준비/접수/반영 표시,
제한된 Tool 결과 미리보기와 스트리밍 화면 위치의 일관성을 설명한다.
[추가 지시 판단과 공통 실행 미리보기](instruction-cards.md)는 별도 모델 판단,
native redirect의 권한 경계, 질문·답변 복원과 사용자 언어 지침을 설명한다.

[승인 범위·기억 규칙](approvals.md)은 승인 폼과 관리 팝업, 동일/명령 범위 조건 비교,
규칙 철회와 개별 승인 취소의 경계를 설명한다. UI는 확정했으며 검증 기록은 해당 문서에 남긴다.

[기본 설정·프리셋·Agent API](application-defaults.md)는 생성 프로필, 이름별 JSON
내보내기, 세션 내 Agent 메시지와 완료 조건 검토의 현재 계약을 설명한다.
[Agent 설정·그룹 연결 검토](agent-settings-review.md)는 모델 설정 소유권, 스키마 폼,
대화 상대 그룹과 단수 명령의 현재 경계를 설명한다.

Hub는 모델·prompt·실행 제한·검색 전략 등 제품 선택을 새 Project의 명시 설정에 기록한다.
Backend는 그 계약을 검증·집행한다. Host ceiling으로 저장된 Project 권한을 덮어쓰지 않으며
UI effective values를 새 설정 기본값으로 저장하지 않는다.
해당 Hub 기능/프리셋이 소유한 생성 초기값은 매번 사용자 선택 없이 제공할 수 있다.
사용자 값 우선·정규 저장/조회/편집·기존 프로젝트 재주입 금지·Backend 검증과 독립성은
[Hub 초기값 계약](presets-and-resume.md#hub-초기값의-소유권과-저장-계약)을 따른다.

| 소유자 | 책임 | 정규 문서 |
|---|---|---|
| bootstrap/config/Project 서비스 | 구현 주입, 앱 생성 preset, 설정·선택·CAS | [구조](structure.md), [설정/재개](presets-and-resume.md) |
| backend worker | 이벤트 루프, 공개 llm API와 실행 수명 | [초기 연결·단계 표시](backend-progress.md), [조회 중복 제거](backend-observation.md), [렌더링 파이프라인](rendering-performance.md), [유지보수 경계](maintenance.md) |
| 요청 환경 | Enter 시점 cwd/env 사본, Tool·path revision binding | [셸 환경](shell-environment.md) |
| 요청별 도구 선택 | 인사 입력 축소, 조회 도구 선택, 기존 승인·재개 유지 | [요청 분기](request-routing.md) |
| 대용량 Tool 데이터 | 생성 preset·공개 저장 연결·범위 조회 지침 | [원문 저장과 외부 읽기](large-data-tools.md) |
| backend 오류 계약 소비 | Diagnostic 보존·backend admission·앱 선택 | [오류](error-handling.md), [Provider 복구](provider-recovery.md), [작업 복구](operation-recovery.md) |
| Tool UI/선택기 | 승인·질문 표시, 허용된 인자 수정 제안 | [질문과 Tool](questions-tools.md), [Tool retry](tool-retry.md) |
| 자원 편집 | 선택한 자원의 소유 Project에서 Schema·CRUD·CAS·참조 보호 | [관리 창](component-managers.md), [authoring](resource-authoring.md), [RAG](rag.md) |
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

Hub는 독립 Project Agent를 정의하고 `Session.agent_ids`에 대화 상대를 저장한다.
요청마다 명시한 수신 Agent를 통해 Engine을 실행하며, 전체 수신 시 Agent별 Run이 응답한다.
제목 생성·일반 대화·Graph 위임·자원 편집·환경 캡처도 같은 공개 Agent 계약을 사용한다.
다중 Run의 이름·진행 상태·출력은 backend 원본의 투영이며 호환 wrapper를 추가하지 않는다.

P1-b에서 옛 preferences/theme/profile 변환을 제거했다. 저장된 각 section은 현재 필드 전체를
가져야 하며 Reader는 누락 필드·옛 키를 거부하고 원본을 보존한다. 생성·편집 시 owner의 초기값을
적용한 완전한 section을 저장한다. 환경 없는 구 Run fallback은 C의 실행 binding 전환 대상이다.

## 기능 확장 연결

[기능 확장 작업 지침](feature-expansion-llm-handoff.md)의 1~4단계 구현을 연결했다.
현재 사용법·API·제약은 [Workflow·관측·이미지·음성](interactive-features.md)을 따른다.
Workflow 작성 화면은 [그래프 중심 편집기](workflow-editor.md)에 조작법·소유권·검증 경계를 정리한다.
실제 음성 모델/장치/서버 및 Kitty 수동 검증은 모의 검사와 구분한다.

1. 기존 API로 가능한 Hub 이미지 표시·터미널 관측·스킬·Graph 지시 예약을 연결한다.
2. 실행 없는 Workflow 초안 검증과 위치 진단을 편집기에 연결한다.
3. 미실행 Tool 기록과 재개 계약을 확정한 뒤 Tool 사이에서 추가 지시를 반영한다.
4. 확정 발화의 접수·중복 방지 계약을 마련하고 STT → 기존 텍스트 Run → TTS를 연결한다.
5. 필요한 경우에만 네이티브 음성 Provider와 해당 Provider의 응답 중 지시 지원을 별도 구현한다.

정책값·프리셋은 Application이 선택해 공개 설정 경로에 명시하고 llm은 검증·집행한다.
버퍼·유휴 시간·검증량 등 사용자 조정 제한에 숨은 Backend 기본값을 넣지 않는다.
편집 검증 성공은 실행 허가가 아니며, 실행 경계의 권한·승인·예산 검사는 유지한다. 동일 입력과
의존성이 입증된 순수 검증만 [기존 재사용 계약](../llm/storage-transactions.md)의 범위에서 재사용한다.

음성 때문에 `policy.final_review`를 자동 활성화하지 않는다. 활성 시에는 검토된 후보만 재생하며
그 대기 시간을 계측하고, 비활성 시에는 기존 공개 출력 계약을 따른다. 명시적인 `request_key`와
`get_receipt()`가 ID·대상·내용·접수 결과를 기존 저장 소유자에 연결한다.
file/memory의 재시작 보장 차이와 명시적 재개는 유지한다.
각 단계의 집중 검증과 통합 완료 시 전체 회귀를 수행하며 보류한 기능은 완료로 보고하지 않는다.

## 사용법·검증 기록

- [계약 감사 기록 갱신과 회귀 검증](contract-audit-refresh.md): 최근 UI·도구 구성의 개별 검토, 현재 소스 근거와 전체 Hub 회귀 결과. 과거 전수 감사와 현재 미검토 범위를 구분한다.

- [핵심 설정 화면과 전체 JSON 편집](essential-settings.md): 공통 항목 노출 기준, 원본 설정 보존과 클릭 편집.

- [요청 준비 비용 분해와 중복 조회 제거](preparation-ownership-review.md): 저장 잠금 점유·대기와 Hub 준비 비용의 분리 계측, 조회 공유 및 전후 검증.

- [조회·렌더링 3단계 최적화](observation-render-optimization.md): 선택 세션 우선 조회, 표시 변경 재사용, 협력적 렌더 작업과 전후 계측·회귀 검증.

- [현재 llm 연결과 요청 지연 개선 작업 지침](request-latency-handoff.md): 2026-10-31 Hub 작업 범위, 이미 반영된 수정, 측정·검증 및 실제 ish 확인 절차. 향후 작업 지침이며 완료 보고가 아닙니다.
- [요청 상태 전달·스트리밍 비용 검토](request-latency-review.md): 해당 지침에 따른 현재 변경, 격리 측정·검증 기록과 실제 ish 확인 절차.
- [변경 신호 기반 갱신 검토](signal-refresh-review.md): 최대 갱신율, Run별 도구 카탈로그 재사용과 요청 준비 시간의 비교 검증.
- [요청 준비 경로 추가 최적화](request-preparation-review.md): Run 목록·초안 조회와 요청별 도구 준비 비용, 동일 환경 측정 및 전체 Hub 회귀 근거.

- [검사 자료와 UI 대기 검증](verification-stability.md): Linux 복사본·검토 해시·미리보기 정리·검사 진행 기록.

- [Tool 누적 문맥 관리](working-context.md): Memory 요약 실패의 참조 대체와 CompletionPolicy 재검사.

- [공통 UI 규격](ui-consistency.md): 버튼 정렬·팝업 크기·Agent 편집·디버깅·승인 정책.

- [Agent와 대화 상대](participants.md): 이름·초대·수신 대상·동시 응답·실시간 Workflow 표시.
- [초대·관측 계약 검증](observation-contract-review.md): canonical 초대, 반복 조회 제거, 단계 전달 Owner와 검증 증거.

- [Hub README](../../hub/README.md): 설치·실행·현재 설정.
- [요청 이력](request-history.md), [Completion 표시](completion-display.md),
  [사용량](usage-and-system-messages.md), [코딩 가이드](coding-workflow.md).
- [문서 목록](../document-map.md), [기준선과 미검증](../llm/documentation-review.md).
- `backend-contract-handoff.md`, 구조 감사, 각 문서의 날짜별 검증 절은 과거 기록이다.
  당시 X11 Skip·모의 Provider·특정 snapshot 결과를 현재 전체 통과로 승계하지 않는다.
