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

## B/C 연결 진행 — 아직 상위 완료 아님

독립 Agent를 Project 생성·load·clone·backup, Manager/Handle의 CAS·수명 검사에 연결했다.
Component 등록/선택에서 Agent를 제거하고 Workflow·Refinement·Prompt·Skill·MCP·RAG 소비자와
참조 보호를 연결했다. 소실된 Agent 목록은 빈 목록으로 복구하지 않는다. Run/Step/Checkpoint와
대기 요청의 Agent 참조도 삭제를 보호한다. 선택 Engine의 동기·무효과 `for_agent` 계약을 검증한다.

Run API는 `agent_id`와 별도 `request_context`를 받는다. 정의·revision·Engine binding을 원본
User 요청과 Run에 보존하고 원본과 현재 실행 가능성을 확인한다. `request_context` 생략은 그대로
남으며 cwd/env·turn provenance를 Agent 정의에 합치지 않는다. Root도 기존 Agent 리소스 선택·정책
narrowing을 사용하고 `max_tool_calls`를 실제 child scope에 전달한다. 재개는 원래 실행 binding이
없거나 정의·리소스·환경이 달라지면 차단하며 최신 정의로 증거를 메우지 않는다.

Hub 환경 wrapper는 Agent factory와 Prompt/Skill 합성 뒤에도 유지한다. 실제 Engine binding은
요청의 환경에 맞춘 Tool 계약으로 계산한다. Hub 전체 선택 UI·설정·제목/자원 생성, CLI·예제와
나머지 시험 호출부의 전환은 진행 중이다. B/C Level 2와 전체 Level 3, 정적 감사 pin 갱신,
전체 문서 검사는 아직 완료하지 않았다. 현재 변경은 커밋·Push하지 않았다.

### 집중 검증과 실패 증거

명령은 `.venv-linux312/bin/python /workspace/llm-clean-resume/h2-q/run-focused.py
{llm|hub} /workspace/llm-clean-resume/b/{실행명} {아래 모듈}`이며 실제 내부 명령 배열·exit·요약은
각 디렉터리의 `{llm|hub}-execution.json`, 전체 원본 로그는 `{llm|hub}.log`에 보존했다.

| 실행 | 모듈 (`tests.llm.` / `tests.hub.` 접두사) | 발견/실행/통과 | Failure/Error | exit |
| --- | --- | --- | --- | --- |
| b2-focus-3 | test_agent_storage, test_agent_domain, test_agent_resource_integrity, test_agent_workflow, test_agent_delegation, test_refinement | 89/89/89 | 0/0 | 0 |
| c-focus-3 | test_agent_execution, test_agent_workflow, test_agent_delegation, test_nested_graph, test_agent_resource_integrity | 90/90/90 | 0/0 | 0 |
| c-hub-focus-3 | test_shell_environment | 11/11/11 | 0/0 | 0 |

각 성공 실행의 Skip/expected failure/unexpected success/누락/중복/예상 밖 실행은 모두 0이다.
실행 시간은 각각 36.490459/48.956457/15.872118초다. 앞선 실패를 제외하거나 합쳐 하나의 전체
검증으로 표현하지 않는다. b2-focus-1은 discovery import 오류로 exit 2, b2-focus-2는 89개 중
82통과/7Error(exit 1), c-focus-1은 6개 중 5통과/1Error(exit 1), c-focus-2는 90개 중
76통과/2Failure/12Error(exit 1)였다. 필수 CAS와 선택 Engine fixture·호출부, 남은 Refinement
조회, 오류 메시지를 수정했다. c-hub-focus-1/2는 각각 11개 중 8통과/3Error(exit 1)였고
wrapper 사본 생성 및 Registry API 연결을 수정했다. 원래 효과 횟수·손상·승인·재개 Assertion은 유지했다.

각 실행의 source/input은 전후 동일하다. 기준은 로컬 HEAD `4845e5b`와 해당 시점 미커밋 변경이다.

| 실행 | source SHA256 | input SHA256 |
| --- | --- | --- |
| b2-focus-3 | `b79dabe7245cbbbb77366efe5da0e17e355ad5c2ceb21e3ac358f6b2a07c5ea2` | `58af5aaf9dfc8ef92259a1ad481c969ef0c9f86ae6913901b215e78955f2c330` |
| c-focus-3 | `f7894b54f082dc94f0f81794f91153b780601760bcb384624d7669ca9f4cc8ba` | `71908945ff6c2c1d8ea03d1c5cd3fbed8d57e0d746d085cf9f8d3bacd1849008` |
| c-hub-focus-3 | `25c47d84b6173c83d0ec95cc30f6516dae06551cd1a33b6401b4b8b10347ad26` | `2b2c0f2c3a32f2e9379c2f529d5697b91dc74302c37401632af945fa2e5c39eb` |

Python 3.12.14/136개 의존성/host `d8337909`와 실행 환경은 각 input snapshot에 기록했다.
Hub 시험은 실제 파일·프로세스·격리 환경을 사용했다. c-focus-3에는 기존 실제 중단/재개,
승인 거부·만료, 필수 사용량 소실 및 Root의 실제 파일 효과 상한 시험이 포함됐다.

같은 c-focus-3 소스에서 `.venv-linux312/bin/python -m tests.llm.check_budget_mutation`도
실제 exit 0이다. 기존 중첩 R3와 신규 Root 재개 시험에 호출 한도 제거를 주입해 **두 시험 모두
실제 추가 파일 효과 Assertion으로 의도한 실패**를 검출했다(2 Failure, Error/Skip 0).
일반 회귀 성공과 Mutation의 의도한 실패 검출은 별개 증거다. 전후 source/input 동일성을 확인했다.

전체 회귀는 공개 호출 경로와 Hub 소비자가 일관된 B/C 상위 게이트까지 유예했다. 위 집중 결과만으로
B/C 완료나 원격 CI 통과를 선언하지 않는다. A의 원격 통과는 A SHA에만 해당한다.

## C 소비자 이관 및 실패 경로 추가 검증 (진행 중)

Hub는 Project 생성 때만 실제 기본 Agent와 제목 Agent를 저장하며 기존 Project 재열기 시
삭제된 Agent를 재생성하지 않는다. `/agent`는 저장된 Agent를 선택하고 `/agents`는 관리 화면이다.
요청 환경은 `request_context`로 분리했다. Graph Workflow 참조는 선택 Engine이 선언하고
AgentManager/ProjectReferences가 삭제 경계에서 보호한다. CLI/예제도 실제 Agent를 생성·선택한다.

Hub wrapper가 있는 중첩 Graph를 행동형으로 오분류하는 실패를 실제 승인·파일 효과 시험으로
재현했다. 공개 AgentMode를 통해 판정하고 factory/요청 context가 실행 방식을 바꾸어 제한을
제거하지 못하도록 검증했다. 승인 전 파일 없음, 승인·재시작 후 저장된 환경에서 효과 1회를 확인했다.

재개 준비·체크포인트 최초 복사 실패 시에도 새 Run 생성 transaction에서 **원본 Run의 이미
저장된 실행 증거**를 보존한다. 현재 설정으로 기존 기록을 보충하지 않는다. 원래 복사 실패 뒤
재개·누락된 초기화 기록 거부 Assertion을 유지하고, 원본 증거의 동일성 Assertion을 추가했다.
접수 증거 없는 복원 큐는 복구 쓰기/소유권 attachment 전에 거부한다. 이전의 Engine 미선택
실패 Run 생성 시험은 C의 필수 Agent identity 계약에 맞춰 원본 bytes 불변·Run/효과/알림 0·
미부착 검증으로 강화했다. 누락된 identity를 가진 Run을 생성하여 정상 기록으로 대체하지 않는다.

다음은 각각 독립 실행이다. 전체 회귀 합계로 합치지 않는다. 모두 source/input 전후 동일하며
Skip/expected failure/unexpected success/누락/중복은 0이다. 실제 명령의 공통 앞부분은
`/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression`이며, 각 행의 인자를
그대로 붙여 실행했다. 원본 JSON·로그·명령·exit는 `/workspace/llm-clean-resume/b/<실행>/`에 보존했다.

| 실행/suite | 발견=시작 | Pass | Failure | Error | Exit | 초 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| c-examples-1/llm | 50 | 50 | 0 | 0 | 0 | 60.925391 |
| bc-references-1/llm | 38 | 38 | 0 | 0 | 0 | 5.725266 |
| c-hub-api-1/hub | 14 | 14 | 0 | 0 | 0 | 22.762360 |
| c-hub-consumers-1/hub | 22 | 19 | 2 | 1 | 1 | 94.969279 |
| c-hub-consumers-2/hub | 10 | 10 | 0 | 0 | 0 | 74.414625 |
| c-hub-graph-1/hub | 1 | 0 | 1 | 0 | 1 | 2.672067 |
| c-hub-graph-2/hub | 1 | 1 | 0 | 0 | 0 | 6.129478 |
| c-hub-graph-2/llm | 78 | 78 | 0 | 0 | 0 | 46.573749 |
| c-runtime-1/llm | 39 | 37 | 0 | 2 | 1 | 5.474319 |
| c-runtime-2/llm | 91 | 75 | 1 | 15 | 1 | 24.422974 |
| c-runtime-3/llm | 60 | 60 | 0 | 0 | 0 | 23.747252 |

```sh
--suite llm --output /workspace/llm-clean-resume/b/c-examples-1/llm.json --modules tests.llm.test_ishrc_loop tests.llm.test_recovery_probe tests.llm.test_configuration_workflow_example tests.llm.test_graph_rag_example tests.llm.test_graph_engine
--suite llm --output /workspace/llm-clean-resume/b/bc-references-1/llm.json --modules tests.llm.test_agent_domain tests.llm.test_agent_resource_integrity tests.llm.test_agent_storage
--suite hub --output /workspace/llm-clean-resume/b/c-hub-api-1/hub.json --modules tests.hub.test_agent_selection tests.hub.test_shell_environment
--suite hub --output /workspace/llm-clean-resume/b/c-hub-consumers-1/hub.json --modules tests.hub.test_naming tests.hub.test_slash_commands tests.hub.test_popup_completion tests.hub.test_snapshot_projection tests.hub.test_bootstrap tests.hub.test_summary_api tests.hub.test_resource_tools
--suite hub --output /workspace/llm-clean-resume/b/c-hub-consumers-2/hub.json --modules tests.hub.test_slash_commands tests.hub.test_resource_tools
--suite hub --output /workspace/llm-clean-resume/b/c-hub-graph-1/hub.json --modules tests.hub.test_agent_graph
--suite hub --output /workspace/llm-clean-resume/b/c-hub-graph-2/hub.json --modules tests.hub.test_agent_graph
--suite llm --output /workspace/llm-clean-resume/b/c-hub-graph-2/llm.json --modules tests.llm.test_agent_execution tests.llm.test_agent_delegation tests.llm.test_nested_graph tests.llm.test_agent_workflow
--suite llm --output /workspace/llm-clean-resume/b/c-runtime-1/llm.json --modules tests.llm.test_runtime tests.llm.test_run_api
--suite llm --output /workspace/llm-clean-resume/b/c-runtime-2/llm.json --modules tests.llm.test_runtime tests.llm.test_run_api tests.llm.test_loop tests.llm.test_graph_checkpoints
--suite llm --output /workspace/llm-clean-resume/b/c-runtime-3/llm.json --modules tests.llm.test_loop tests.llm.test_graph_checkpoints tests.llm.test_agent_execution
```

| 실행 | source SHA256 | input SHA256 |
| --- | --- | --- |
| c-examples-1 | `f9bf7f3f0199b200d9e77923112d6facaaccd210fc7b95588da9558929aaa23d` | `3a2da7a89fba1d04b5bc5bef49fcd17807f82cfec894e2decc6680db189c5105` |
| bc-references-1 | `c197a159372e23a44f8227fa9ae4a63d15067c6c3310ef1dcdded79264b8be46` | `ccefff97e62ba72176ef02bb72740e28a02f77c38356b2df0c0c9438f60f397d` |
| c-hub-api-1 | `63af95597d1098784438025a059b99e2c367c00668c1809a1b611a1dec43f0ae` | `de4ddebb775c68b212910e2b70f2f3f0c14fc6fbe0959ca7f32dce418ec92a0a` |
| c-hub-consumers-1 | `e2deefece1d6791649a5580aba7beb004d85a8183790f3807c55fbe7092c21ba` | `979bfd5e918b27447ec71cfca23f137a51825910eedb00ffc892893e75c49363` |
| c-hub-consumers-2 | `74a61610b87256fb23e477f5b8b782dea105efc36f0574556419a18c7c83f410` | `4bb4090453827b9a03fbfca423645dc8c8e8f7adab2c3dd1b37f52fc1fe9430a` |
| c-hub-graph-1 | `9d3a5735f33acc2b723bca33c529f5879a0653b377193a83f141e452c50111e7` | `521a8f10a83a8d33ac710d6fd6ff3b1bcf6aedee5d7dd0db59f5a554c48b8cb2` |
| c-hub-graph-2 | `d3f3828de628380366e2b59091e38f422c9539ce6a60d464c569defc5be55dea` | `50f8d5ef67348af976e6f4076466ca9ceb62546a9e76e328010ffcec38bffbb4` |
| c-runtime-1 | `2bccf7c875b3194e1558471a5674e5141236cfe484cb61cede8183c63a675f4d` | `5b2cb5fb26c27bb76ba0de90482d6791b2153f1021176e190f40e792ecf7ee0e` |
| c-runtime-2 | `b046be4c8e6eba652792b1f8d785f4a1692d52ce291901492b806e26b8901e0d` | `06690b70261d480f54031c86989858ce98079727ee9f03e77cfe2d50a94af1ee` |
| c-runtime-3 | `311902831ca42dd652a80ed55c4ed4651e9d559a4098d5173b33975a95d11b19` | `1078b497c05df4baf6a66323d3b3a6a723944c8209f973952302244db6db02db` |

실패 후 변경: consumers-1은 선택 Agent 생성, 선택/관리 명령 및 optional purpose 시험 fixture를
수정했다. graph-1은 위 wrapper 실행 방식 결함이다. runtime-1의 두 Error는 구 AgentComponent
fixture와 접수 증거 없는 crash fixture를 현행 Writer로 이관했다. runtime-2의 Loop 13 Error는
fixture의 dict 설정 접근 오류, Graph 두 Error는 위 재개 증거 보존 회귀, 나머지 Failure는
메모리 대화 부재 오류 메시지였다. runtime-3에서 해당 경로 및 Root 한도/증거 누락 보호가 통과했다.

실행 환경은 Python 3.12.14, 의존성 136개, host d8337909이며 각 input snapshot이 상세 동일성을
증명한다. 영향 범위인 Repository/선택 factory/Root·nested/Hub·예제 소비자와 실제 승인·중단·재개를
집중 검증했다. 다른 Run API 소비자, 정적 감사·문서 검사 및 B/C 전체 Level 3는 아직 남아 있다.
전체 회귀는 공개 소비자 이관을 모은 B/C 게이트에서 실행한다. B/C 완료·Push·원격 CI 통과를
선언하지 않으며, 이전 A 결과를 현재 소스의 전체 통과 증거로 재사용하지 않는다.

### C 소비자 이관: 원본 증거와 실행 경계 검증

`c-runtime-3` 이후의 15개 독립 실행은 [기계 판독 가능한 결과](agent-consumer-results.json)에
실제 명령·exit·발견/실행/통과·실패·오류·Skip·누락·중복, 소스/입력 SHA256과 원본 로그 위치를 기록했다.
각 실행의 전후 snapshot은 동일하다. 서로 다른 소스의 결과를 하나의 전체 통과로 합산하지 않는다.
Python 3.12.14와 고정 ish host를 사용했고, 환경·의존성·테스트 발견 입력은 각 input snapshot으로 구분한다.
GitHub CI 및 B/C 전체 Level 3 완료 증거는 아직 없다.

소유자 변경은 `SkillData.impact`가 ComponentRegistry의 폐기된 AgentComponent 대신
독립 AgentHandle의 저장 snapshot revision을 조회하도록 한 것이다. 저장 참조가 있는 Skill의
삭제 거부·원본 보존·재열기 검증을 유지했다. Agent는 Component 선택에서 독립된 도메인이므로
관련 시험은 Component 목록에 Agent가 없는 상태의 의존성 보호와 재열기를 검증한다.

기존 런타임 소비자는 실제 저장 Agent를 명시적으로 생성하고 `agent_id`로 호출한다.
Raw Run/queued User fixture에는 현재 필수 AgentBinding을 기록하며 Reader가 누락을 보완하지 않는다.
Memory·Refinement fixture는 Application이 선택한 Tool 목록을 명시적으로 저장한다.
Session 전용 `memory_tool_result`는 Memory가 선택된 시험에서만 추가한다.
공개 Writer의 선택 Engine 검증을 통과하지 않는 기존 자료를 재현하는 시험은 공개 쓰기 거부도
검증한 뒤 Repository fixture로 구성한다. 실행 때 선택되지 않은 정의는 실행하지 않는다.

원래의 안전성 Assertion을 유지했다. Root Agent가 Project Tool 상한을 넘는 경우는 Provider 전
거부를 추가 검증하고, 별도 Host 승인 거절 경로에서 원래의 Tool Step·거절 영수증·Provider 1회·효과 0회
Assertion을 모두 실행했다. Agent 초기 상태 주입으로 깨진 Engine 사유 상태 격리는 제품에서 수정했다.
합법적인 재개 준비 실패 뒤 재시도에는 원본 실행 증거를 새 Run 생성 트랜잭션에서 승계하며,
기존 Run의 필수 증거 소실은 복구하지 않는다.

최근 `c-runtime-boundaries-1`은 Memory 처리·효과 원장 Schema·런타임 신뢰성·실행 경계·MCP의
5개 모듈 114/114/114, Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행
모두 0, 실제 exit 0(96.191891초)이다. `c-memory-consumers-1`의 RAG·현행 안전성 계약·도메인 강화
모듈도 통과했고, 그 실행에서 실패한 Memory 원본 결과 조회는 위 114개 실행에 포함해 재검증했다.
`c-extension-consumers-2`의 Skill 조회 외 54개 통과와 이후 Skill 전체 통과는 별도 소스 기록으로 남긴다.

Hub `c-hub-selection-2`는 전송·이력·Tool 재시도·실제 shell 환경·Agent 선택 43개가 통과했다.
UI가 문맥을 캡처하지 않았을 때 빈 request_context를 만들던 문제를 고쳤고 생략 보존을 검증했다.
`c-hub-features-1`은 24개 중 23개 통과, 1 Error다. Engine 선택 이관 script가 해당 Engine이 없는
별도 UI fixture에도 Agent 생성을 넣은 오류이며 수정 후 해당 모듈을 다시 실행한다.

이 범위는 현재 연결한 API 소비자와 실제 파일 효과·승인·원장·Checkpoint·재개·삭제 계약을 확인하는
집중 검증이다. 남은 공개 API 소비자 이관, 정적 감사 검토 갱신, 문서 검사 및 R3 Mutation을 포함한
B/C 통합 Level 3 게이트 이후에만 상위 완료·Push·정확한 SHA의 원격 CI를 확인한다.


### C 소비자 이관: 최종 호출부와 문서·정적 검토

이후 결과도 `agent-consumer-results.json`에 실행별로 추가했다. `c-hub-features-2`의 7개,
`c-resource-tools-1`의 73개, `c-sandbox-config-1`의 52개와 `c-final-root-consumers-3`의 50개는
각각 실제 exit 0이다. 마지막 실행은 Completion 처리·검색 Tool·격리 Tool의 50/50/50이며
Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행이 모두 0이다.
원본 로그와 전후 source/input 동일성, 정확한 명령은 JSON에 기록했다. 성공 실행들을 하나의
전체 회귀로 합산하지 않는다. `c-final-root-consumers-1`은 fixture 들여쓰기 오류로 discovery
exit 2였으며 미완료로 보존했다. 수정 뒤 동일 소비자 범위를 다시 실행했다.

Crash worker의 실제 효과·fsync·프로세스 종료, 공유 SessionManager 잠금, 재열기 시 Engine 등록,
Component 선택 변경 시 명시적 Agent CAS, Graph nested factory와 instruction reservation fixture를
현재 API로 이관했다. 기존의 승인·실제 효과 횟수·손상 거절·재개·원본 보존 Assertion을 유지했다.
Agent 경로는 `agents/records`와 `agents/records.manifest.json`이며 `.agents`가 아니다.
과거 기록과 38개 데이터군 감사 결과를 덮어쓰지 않고 현재 사용자 문서·예제만 갱신했다.

Workflow action 참조는 handler owner가 해석한다. 저장 Component가 `type == agent`로 사유
필드를 읽던 후보를 제거하고, 등록 Graph의 순수 handler 선언과 AgentNode.agent_references로
삭제 전 저장 참조를 검사한다. 다른 등록 이름·반복 본문·비활성 Workflow·무관한 Agent 삭제,
미등록 owner의 fail-fast와 원본 bytes 보존을 시험한다. 실행·factory·모델 호출 없이 검사한다.
초기 추가 시험의 필수 `on_limit` 누락과 CRUD fixture의 미등록 action owner 오류도 실패로
기록했다. 완화 대신 실제 handler owner를 fixture에 등록하여 원래 CRUD Assertion을 유지한다.

정적 재검토는 기존 152개 동일 occurrence의 소유자·소스·계약·소비자 근거를 재사용하되,
변경된 근거 파일을 검토하여 hash를 갱신했다. Hub 생성 프리셋은 여전히 생성 때만 적용되고
새 Agent 초기화도 같은 경계에 있다. Agent 기본 ID의 반복 선언은 하나의 Hub 선언으로 통일했다.
Skill/Refinement는 공개 AgentHandle·revision/CAS를 사용하며 기존 비교/opt-in 후보의 의미는
변하지 않는다. Base/Graph의 부분 출력 후 retry 금지·유한 timeout·정확한 uncertain node 집합,
Run 사용량 검사·Graph ParameterLayout 소유권도 유지한다. 테스트 변경은 실제 저장 Agent와
필수 binding fixture 이관이며 현재 전체 회귀의 성공 여부와 별개로 정적 근거를 검토했다.

새 세 후보는 Hub 관리 catalog의 `manager == agent` 소비와 두 언어팩의 Agent 표시 문자열이다.
각 occurrence에 소유자·이유·실제 소비자 테스트 ID·계약/소스 hash를 개별 기록했다. S1/S6나
미분류 후보에 포괄 예외를 추가하지 않았다. 현재 문서 검사는 135문서·183 Python fence·980링크·
35 anchor에서 오류 0이다. 이는 문법/링크 검사이며 모델 실행이나 전체 회귀 통과의 증거는 아니다.
B/C 완료·Push는 이 통합 소스의 Level 3 전체 LLM/Hub·R3·정적·문서·실환경 검증 뒤에 진행한다.


### B/C 첫 전체 게이트와 수정 후 집중 검증

`bc-full-1`은 실제 전체 LLM 2170/2170/2164, 실패한 시험 ID 6개(Failure 10/Error 1),
Hub 581/581/568, 실패한 시험 ID 13개(Failure 10/Error 8), 각 exit 1이다.
두 suite의 Skip/expected failure/unexpected success/누락/중복/예상 밖 실행은 모두 0이다.
각각 873.945174/919.785793초이며 source `ef36922064cfc9883018f4f915fdeeaad2433a66cf52bad0e73323d20933399b`,
input `9309767f38bf33b702542b4d90296a0b449b7214895c04bc353b075afa018f25`가 게이트 전후 동일하다.
정확한 명령·원본 로그·각 실패는 JSON의 `bc-full-1:llm`과 `bc-full-1:hub`에 별도 기록했다.
상위 게이트 실패이며 이 결과를 통과로 집계하거나 실패 시험을 제외하지 않는다.

LLM의 문자열 subprocess fixture 네 곳에 남은 engine= 호출을 실제 저장 Agent 호출로 이관했다.
재열기 프로세스에는 Agent를 재생성하지 않는다. 새로운 memory Project에는 Application이
명시적으로 Agent를 저장한다. 필드 이름 검증은 기존 필드를 유지하면서 필수 Run.agent_id를 확인한다.
강제 종료 코드 77·각 도달 marker·원본 또는 완전한 새 상태·효과 횟수 Assertion을 모두 유지했다.

Hub의 실제 UI 결함은 Agent 관리 창에서 존재하지 않는 Component 설정 화면을 열던 버튼이었다.
관리 catalog의 owner를 따라 Agent 정의 생성/편집/삭제를 제공하고 해당 Component 설정 버튼은
표시하지 않는다. 일반 Component의 설정 이동·포커스 검증은 그대로 실행한다. 남은 RPC/Graph
소비자도 저장 Agent를 선택한다. 비활성화한 Tool을 여전히 선택한 Agent는 Provider 전 거부를
추가 검증하고, Application의 명시적 Agent CAS 뒤 원래의 Provider 1회·승인·실제 파일 효과 검증을
유지했다. 설정 UI 시험은 catalog 재조회 중 choose가 무시되는 race를 확인해 busy 종료를 기다린다.
기존 timeout을 늘리지 않았고 편집 값·포커스·화면 복귀 Assertion을 유지했다.

수정 후 `bc-crash-consumers-1`은 6개 LLM 모듈 51/51/51, exit 0(16.988580초)이다.
`c-hub-gate-fixes-1`은 11개 Hub 모듈 56/56/56, exit 0(128.529035초)이다.
모두 Failure/Error/Skip/expected failure/unexpected success/누락/중복/예상 밖 실행 0이며,
source `2ea074cb5eef4f85b84ad341980785713323790fbe4fae795ef2867419a0a7e1`,
input `8da2d7c30985e31836663d19bf2735e7c5f38969c3e5deb50df2ae302627e00e`가 각 실행 전후 동일하다.
새 전체 Level 3 게이트는 아직 남아 있다. 원격 B/C 완료도 선언하지 않는다.


### B/C 통합 Level 3 완료

`bc-full-2`의 전체 LLM은 2170/2170/2170(880.082715초), Hub는
581/581/581(925.451399초), 각 실제 exit 0이다. 두 suite 모두 Failure/Error/Skip/
expected failure/unexpected success/누락/중복/예상 밖 실행 0이다. 실행 명령은
`.venv-linux312/bin/python -m tests.llm.run_regression --suite llm|hub --output <suite.json>`이며,
각 실제 argv·발견/실행 ID·원본 결과 경로는 `agent-consumer-results.json`의 두 별도 실행에 보존했다.
첫 실패 게이트와 수정 후 집중 실행을 이 전체 실행으로 합치거나 덮어쓰지 않았다.

전체 게이트 전후 source `2ea074cb5eef4f85b84ad341980785713323790fbe4fae795ef2867419a0a7e1`,
input `c209a70f8c6eaa58d7597070edf5034fe38293763f49d88269be9ac55388b4b1`이 각각 동일하다.
동일 source/input의 `bc-aux-2`도 R3 Mutation, S1–S6, 문서, LLM/Hub 실환경,
오프라인 Provider SDK 검증의 실제 exit가 모두 0이다. R3은 한도 전달 제거 시 기존 nested와
새 root의 실제 효과 횟수 Assertion이 모두 실패함을 확인했다. 정적 후보 156개는 개별 소유자
근거가 있으며 미해결/오래된 근거/검토 오류는 0이다. 문서는 135개·Python fence 183개·
링크 980개·anchor 35개에서 오류 0이었다. 실제 실행 명령은 JSON의 auxiliary_gate에 기록했다.

Python 3.12.14, 고정 136개 의존성, host `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
환경 변수·bwrap/Xvfb/xclip 및 소스별 지문은 input snapshot에 포함한다. 일반 UID의 실제
격리·X11·외부 파일 효과·중단/재개 시험을 포함했고 root 실행·격리 해제·Skip 확대는 없다.
현재 backend-api Echo와 owned_handler 예제도 별도 실제 실행 exit 0이며 이를 전체 회귀에
합산하지 않는다. 인증된 실제 모델 호출·전원 손실·손상 디스크는 이번 증거 범위 밖이다.

게이트 뒤에는 이 결과 기록과 현재 참조/Revision 문서만 갱신했다. Python 세 파일의 trailing
공백/마지막 빈 줄 정리는 변경 전후 AST 동일성을 확인했고 파일별 hash/AST hash를 JSON에
보존했다. 제품 동작·시험/fixture·의존성은 바꾸지 않아 같은 로컬 전체 회귀를 반복하지 않는다.
최종 문서/정적/diff 검사와 Push한 정확한 SHA의 원격 전체 CI는 별도로 확인한다.
B/C 로컬 상위 게이트 완료이며 D1/D2·E·F는 미완료다. 원격 B/C CI는 아직 미확인이다.


최종 `bc-postgate` 검사도 `check_architecture --require S1 --require S2 --require S3
--require S4 --require S5 --require S6`과 `check_docs`가 각 exit 0이다. 정적 156개 검토·
미해결/새 후보/오래된 근거/검토 오류 0, 문서 135개·Python fence 183개·링크 993개·anchor 35개·
오류 0, `git diff --check HEAD` exit 0을 확인했다. 전체 게이트 뒤의 현재 문서 정리와
AST가 같은 공백 정리에 해당하며, 제품 기능 변경이나 새로운 전체 회귀 실행으로 보고하지 않는다.


### B/C 정확한 Push SHA의 원격 CI 완료

2026-10-22 06:29:27 UTC에 커밋 `975913e71b413618125b7b770802baaba0d88e52`의
[GitHub Actions 39731609064](https://github.com/E1der0wL/ish-llm/actions/runs/39731609064)를
직접 확인했다. workflow 완료/Success이며 LLM 2170/2170/2170(1108.514600초),
Hub 581/581/581(1205.691608초), 두 suite 모두 Failure/Error/Skip/expected failure/
unexpected success/누락/중복/예상 밖 실행 0이다. Python 3.12.14(GCC 13.3.0)의 원격
필수 두 job 결과이며 로컬 Clang 환경 실행과 별도 증거다. run 페이지가 정확한 commit SHA를
가리킴도 확인했다. 이 Push에는 B-a `4845e5b`와 B/C 통합 `975913e`가 포함되며,
이후 로컬 D 소단계의 완료나 원격 검증을 뜻하지 않는다.
