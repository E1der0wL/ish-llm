# B/C Agent 전환 검증 기록

[A의 정확한 SHA 원격 전체 게이트](provider-refactor-verification.md)는 완료했다.
[현행 B/C 계약](clean-refactor.md#bc-agent와-단일-실행-binding)과
[Level 1–3 정책](verification-policy.md)을 따른다. B/C 전체 완료는 아직 선언하지 않는다.

## B-a 독립 저장 계약

`core/agents.py`의 AgentDefinition/AgentRecord/AgentCatalog와
`services/lifecycle/agents.py`의 AgentRepository를 추가했다. 아직 Project 생성·공개 API·실행
소비자에 연결하지 않았다. 기존 AgentComponent를 감싸는 façade를 만들지 않으며,
외부 전환은 B/C와 Hub를 함께 연결하는 후속 게이트에서 완료한다.

필수 선택값은 engine뿐이다. purpose는 설명이며 생략 가능하다. 원본 정의의 생략/null/빈 값은
그대로 보존한다. 현재 record는 storage_version/project_id/id/definition/revision을 모두 요구하고,
catalog는 schema_version/project_id/records를 요구한다. 버전은 정확한 int 현재 값만 받는다.
정의 revision은 기존 canonical JSON 내용 지문을 유지하고 catalog는 실제 record bytes의 SHA256을
보관한다. 모든 조회·쓰기·삭제·복제는 목록과 실제 파일 집합, digest, 레코드 owner를 먼저 검사한다.

필수 catalog·record·디렉터리 소실은 빈 저장소로 해석하지 않는다. 초기화는 Project 트리가 아직
존재하지 않는 명시적 신규 생성에만 허용한다. 저장·삭제의 expected_revision 비교와 파일·catalog
갱신은 같은 WorkspaceOwnership/TransactionManager 범위에 둔다. clone은 원본을 유지하고 빈 목적지에
Project identity를 다시 기록한다. 실제 Project 수명·선택 Engine 검증·참조 보호의 연결은 후속 B/C 범위다.

과거 `refactor/llm-backend@bb0946257ab6bcabb9a3ef04d3d854e5333b07d5`의 Agent CRUD/CAS·선택
흐름·장애 시험을 선택적으로 참고했다. 구형 raw JSON reader, glob의 누락→빈 목록 해석,
AgentComponent 호환 façade, engine=에서 자동 Agent 생성, 이후 단계의 필드를 채택하지 않았다.
현행 저장 트랜잭션과 필수 무결성 검사를 적용해 재구현했으며 과거 통과 기록은 승계하지 않는다.

### Level 1 b1-focus-1

명령: `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm
--output /workspace/llm-clean-resume/b/b1-focus-1/llm.json --modules tests.llm.test_agent_storage`.
발견/실행/통과 16/16/16, Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행
모두 0, 실제 exit 0, 1.448875초다. 원본·수정/삭제 보존, 동시 CAS 1승자,
catalog commit 실패, clone 실패 및 실제 프로세스 종료 9경계(create/save/delete × record/catalog/commit)를
검증했다. 미확정 종료는 정확한 기존 bytes, 확정 후 종료는 완전한 새 상태로 재열기·반복 조회했다.

source 716파일 `6d08a7fb849dede6a5d4503da9af925e848d4437774a9dcda7c731e39206de19`,
input 880파일 `b7f39761e969cf13995c2a337b0f77095910765b100f7f7b3adb2909fa0c9ecd`가 실행 전후 같다.
기준 HEAD `2f2781b7d8314e19b3dce42f036040117413de76`에 신규 세 파일을 더한 상태다.
Python 3.12.14/136개 의존성/고정 host d833790959a76ac457fdeea3f21a6bea1eecfe12/환경과 실행 binary를
input snapshot에 보존했다. S1–S6 사전 감사도 실제 exit 0, 신규/미분류/오래된 검토 0이다.

전체 회귀는 아직 실행하지 않았다. 새 owner의 순수 저장 코드만 추가했고 기존 호출 관계·공개
저장 형식·정책 소비자는 변경하지 않았으므로 Level 2에서 관련 저장·트랜잭션·Agent와 공통 핵심
안전성을 검증한다. B/C 공개 전환과 상위 완료는 새로운 전체 Level 3 게이트를 요구한다.

### Level 2 b1-level2 완료

16모듈 204개 발견/실행/통과, Failure/Error/Skip/expected failure/unexpected success/누락/중복/
예상 밖 실행 모두 0, 실제 exit 0, 56.718646초다. 신규 Agent 저장 계약과 기존 Component 목록,
트랜잭션·Agent Workflow/위임/리소스·Graph Agent/정의 소비자, interaction_storage·usage_deletion·
operation_integrity·checkpoint_integrity·rag_storage·lifecycle_integrity, 아키텍처 검사 자체를 실행했다.
R3 Mutation/S1–S6/문서/offline SDK의 보조 4명령도 실제 exit 0이며 정적 신규/미해결/오래된 검토는 0이다.

실제 명령은 `.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output
/workspace/llm-clean-resume/b/b1-level2/llm.json --modules`에 다음 완전한 모듈 목록을 지정했다.
`tests.llm.` 접두사 뒤의 이름은 test_agent_storage, test_component_record_integrity, test_transactions,
test_agent_workflow, test_agent_delegation, test_agent_resource_integrity, test_graph_agent_contract,
test_definition_components, test_interaction_storage, test_usage_deletion, test_operation_integrity,
test_checkpoint_integrity, test_rag_storage, test_lifecycle_integrity, test_architecture_checks,
test_architecture_reviews다. 전체 명령 배열·실제 exit·요약은 `b1-level2/llm-execution.json`,
보조 명령은 `b1-level2/aux/execution.json`에 원본 로그와 함께 보존했다.

source 716파일 `6d08a7fb849dede6a5d4503da9af925e848d4437774a9dcda7c731e39206de19`,
input 881파일 `28d3950b993800fb69e667efbb0b0c52da08d5ad805bfed5d5605de647212133`가 실행 전후 같다.
환경은 앞선 Level 1과 같고 실제 격리 환경에서 실행했다. 이후 변경은 이 결과 문서뿐이다.
B-a 순수 저장 소단계의 로컬 검증만 완료했다. 공개 호출 경로를 아직 바꾸지 않았으므로 전체
LLM/Hub는 유예하고 독립 로컬 커밋으로 보존한다. 다음 B/C 통합 게이트에서 전체 회귀 후 Push한다.
