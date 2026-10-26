# LLM 개발 문서 안내

이 디렉터리는 Backend의 공개 계약, 설계 설명, 검증 근거를 관리합니다.
사용자는 [플러그인 README](../../llm/README.md)와 [Backend API](backend-api.md)부터,
개발자는 [아키텍처](architecture.md)와 [최신 작업 인계](handoff.md)부터 읽으세요.

| 목적 | 문서 |
|---|---|
| 책임과 설정 | [Project 권한](project-authority.md), [설정](../../llm/CONFIGURATION.md), [계약 소유권](contract-ownership.md), [Schema](schema-ownership.md), [명명 규칙](naming-conventions.md) |
| 실행과 확장 | [Engine](../../llm/engines/README.md), [Agents](agents.md), [Agent 모델 설정](agent-models.md), [Graph](graph-engine.md), [Component 정의](component-definitions.md), [Provider](../../llm/providers/README.md) |
| 승인·재개·오류 | [Interactions](interactions.md), [Tool 제약](tool-constraints.md), [체크포인트](checkpoints.md), [복구 API](backend-recovery.md), [Retry](retry.md) |
| 저장과 수명 | [트랜잭션](storage-transactions.md), [데이터 수명](data-lifecycle.md), [대화](conversation-storage.md), [Revision](revisions.md), [Session 전송](session-transfers.md) |
| 모델 문맥과 자료 | [Completion 처리](completion-processing.md), [최종 검토](completion-review.md), [RAG](rag-components.md), [Memory](memory.md), [공통 요약 API](../../llm/components/summarization.py) |
| 검증과 운영 | [검증 정책](verification-policy.md), [최신 검증 범위](handoff.md), [운영 한계](production-readiness.md), [현재 문서 검토](documentation-review.md) |
| 과거 변경 근거 | [전수 계약 점검](contract-closure.md), [단계별 계획](clean-refactor.md), [문서 보관 목록](documentation-history.md) |

Tool/Agent 실행에 전달한 `EngineContext`는 Run이 만든 `ToolExecutionScope`를 유지해야 합니다.
누락을 새 scope로 보충하지 않으며 `tool_invocation_invalid`로 즉시 거부합니다.
문맥 없는 독립 `ToolExecutor.execute(context=None)`의 초기화와 Run 문맥의 상속은 서로 다른 경계입니다.
자세한 규칙은 [필수 실행 객체의 전달](project-authority.md#필수-실행-객체의-전달)에 있습니다.

전체 파일 목록은 [문서 색인](../document-map.md)을 참고하세요. 단계별 검증 기록의 수치·브랜치 지시는
해당 날짜에 한정됩니다. 숨은 기본값, 자동 migration, 과거 API 호환 경로를 복원하는 근거가 아닙니다.
