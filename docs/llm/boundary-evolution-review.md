# Graph / Tool policy / Skill boundary 검토 결과

기준 commit: `b761917dc9abd46e6b042e7cf410cf737d31c591`.
최종 commit은 GitHub 업로드 완료 응답에 기록한다. 변경은 아래 계약 단위로 분리한다.

1. **Base / final commit:** 기준은 `b761917dc9abd46e6b042e7cf410cf737d31c591`이다.
   이 문서를 포함한 최종 commit은 업로드 완료 응답에 기록한다.
2. **GraphEngine:** durable Workflow orchestration engine으로 유지한다. WorkflowComponent가
   정의 저장/구조 검증을 소유하고 Project → Session → Run → Step은 바뀌지 않았다.
3. **Graph Agent 허용:** purpose, engine, engine_options와 비실행 metadata.
   engine_options의 workflow 및 config/policy는 GraphEngine SettingsLayout으로 검증한다.
4. **Graph Agent 거부:** completion, system_prompt, tools, resources, policy,
   input_schema, output_schema, output_format. 빈 dict/list/null도 존재 자체로 거부한다.
   Workflow Agent-node의 emit_text/output_format도 사전 검증 오류다.
5. **사전 검증:** definition → 등록 Engine → for_agent → 필요한 리소스 순서.
   Graph 경로는 Prompt/Skill/MCP/RAG 조회, Agent Tool child scope를 만들지 않는다.
6. **중첩:** 같은 환경은 workflow 노드, 다른 GraphEngine/handler 환경은 Graph Agent.
   부모 tools/tool_scope/capabilities를 그대로 전달한다. Workflow 노드의 입력/출력 매핑과
   schema는 유지한다. Agent Step에는 실제 workflow/engine/revision/configuration만 기록한다.
7. **Tool 인자:** 공통 ToolPolicy.argument_constraints, ToolExecutionScope,
   ToolRegistry, ToolExecutor에서 집행한다. Component별 별도 정책 엔진은 없다.
8. **fixed/bounded/selectable:** 명시 fixed만 생략값을 주입한다. bounded는 명시 숫자 범위,
   selectable은 명시 집합이다. bounded/selectable은 명시 입력을 요구하여 handler 기본값으로
   제약을 우회하지 못한다. 원본 schema와 교집합을 모델에 보여주고 실행 전에 재검증한다.
9. **상속:** 자식 scope는 부모 제약을 상속/축소한다. 확대와 증명할 수 없는 mode 전환을
   거부한다. effective 제약은 승인 action과 Loop/Graph checkpoint binding에 포함된다.
10. **제거한 상한:** Graph/Memory/Goal/process의 숨은 5초 정리 기한,
   Interaction renewal 128단계, observability label 128자/code 96자/128종 other 합산.
   max_nested_depth의 이미 없어진 상한에 대한 0..32 오류 문구와 default16 문서를 바로잡았다.
11. **유지한 강제값:** identifier/hash/storage version, JSON/evidence/vector 유효성,
    경로·symlink 보호, CAS/승인 binding/불확실한 효과 보호, workflow cycle,
    worker IPC frame/capture, Tesseract PSM 범위와 좌표 arity.
    recent256/LRU/index stride/queue/read block은 누적 count·원본·출력을 유지하는 중립 sizing이다.
    stream/n, embedding no-cache, DEFAULT_MAX_RETRIES=0, bundled cost map 격리도 유지한다.
    [분류 근거](architecture-boundaries.md)의 표를 참조한다.
    정리 동작: Graph cleanup_timeout은 명시한 양수 기간만 대기한다. 미설정은
    revoke/cancel/emit gate 뒤 PendingWork로 즉시 넘겨 Session 보호를 유지한다.
    processor close_timeout은 명시한 None 또는 유한 양수다. Memory/Goal의 close는 no-op이다.
    프로세스 그룹에는 SIGKILL을 보내고 OS/pipe EOF 회수를 기다리며 임의 타이머를 만들지 않는다.
12. **Memory:** 추출 의미 prompt는 여전히 필수다. 검색 전략/host search_fn이 있으면 관련
    기록 우선, 없으면 ID 순서로 입력 예산 안에 배치한다. 추출만으로 recall_limit을 요구하지 않는다.
13. **Skill:** dependencies/adependencies는 Agent refs와 child lineage refs를 원본 정의에서
    분리 조회한다. 직접 삭제/CREATE·FORK rollback 모두 참조가 있으면 거부한다.
    cascade/unbind/부모 변경 자동 전파/중복 index는 없다.
14. **Refinement:** proposal/validation/evaluation/approval/apply/rollback, UPDATE/CREATE/FORK/
    bind_skills 의미는 그대로다. 평가자와 합격 기준은 Host/Application 소유다.
15. **Approval:** 기존 ToolExecutor/InteractionRequest/Response와 receipt를 사용한다.
    fixed 인자는 승인 전에 확정되고 변경된 제약은 재개를 거부한다. 새 승인 subsystem은 없다.
16. **저장:** domain/workflow/graph schema는 기존 version 1. 새 영속 도메인·중복 결과 저장·
    별도 Tool 권한 index를 만들지 않았다. Skill lineage와 Agent 정의가 참조의 source of truth다.
17. **호환:** Graph Agent의 금지 필드를 자동 제거하지 않는다. 해당 정의는 명시적으로 고쳐야 한다.
    바뀐 binding으로 이전 checkpoint를 재개하지 못하며 새 요청을 제출해야 한다.
    Graph Agent 예산은 제거되었다. Run 단위 한도는 새 Run의 한도이고, behavioral Loop Agent
    사용량/완료 Tool receipt 재사용 및 uncertain retry 보호는 기존대로다. migration/alias 없음.
18. **추가 파일:** 아래 목록.
19. **수정 파일:** 아래 목록.
20. **신규 검증:** Graph Agent 모든 금지 필드/빈 값/리소스 사전 거부, 별도 handlers/부모 scope,
    node schema, fixed/bounded/selectable, $ref 및 원본 schema 보존, child narrowing,
    실제 RAG/Memory/builtin Tool schema 실행, Loop 모델 schema, Graph ToolNode,
    durable 승인과 reopen 후 정책 변경 거부, 무검색 Memory 추출 순서, A→B→C 삭제/
    rollback와 Agent 참조 보호, 긴 renewal chain/cycle, 긴 관측 코드/label 보존.
21. **집중 검사:** CI와 동일한 목록으로 214 passed / 0 failed / 0 skipped (79.490초).
22. **전체 LLM 검사:** 1,295 passed / 0 failed / 0 skipped (482.886초).
23. **Hub 검사:** 110 passed / 0 failed / 0 skipped (93.882초). Hub 구현은 변경하지 않았다.
24. **CI:** .github/workflows/tests.yml은 main push/PR/manual에서 Linux Python 3.12.14로
    core runtime과 architecture boundary를 검사한다. plugin은 설치하지 않고 의존성만 설치한다.
    외부 유료 API/key 없음. Hub CI는 공개 저장소에 없는 reference ish.platform에 의존하므로
    이번 workflow에 넣지 않고 로컬 WSL 전체 회귀로 검증한다. 원격 실행 상태는 업로드 응답에 명시한다.
25. **외부 검증:** 유료 모델, 사내 endpoint, 장시간 soak는 수행하지 않았다.
26. **한계:** provider가 지원하는 JSON Schema 부분집합은 SDK/provider 계약이다.
    custom host handler를 ToolExecutor 밖에서 직접 호출하면 정책 경계도 우회한다.
    취소를 무시하는 Python/OS 작업은 강제로 안전하게 종료한다고 보장하지 않는다.
    미완료 작업은 Session 보호를 계속 유지한다. 제약 없는 작업의 실제 자원 소진은 OS/provider 오류다.
27. **남은 경계:** 새 승인/설정 소유권 모호성은 추가하지 않았다. Workflow 설계,
    어느 Graph 환경을 등록할지, evaluator의 품질, 검색/기억 전략과 자원값은 Application이 결정한다.
    Graph Agent는 같은 handler를 가진 다른 등록 Engine도 선택 가능하다. 의미상 같은 환경을
    중복 등록했는지를 backend가 추측하거나 설정을 자동 바꾸지는 않는다.

## 추가 파일

- `llm/components/tools/constraints.py`
- `tests/llm/test_tool_constraints.py`
- `tests/llm/test_graph_agent_contract.py`
- `docs/llm/tool-constraints.md`
- `docs/llm/agents.md` (기존 로컬 문서 갱신 후 공개 저장소에도 포함)
- `.github/workflows/tests.yml`

## 수정 파일

- `llm/engines/graph/{engine,agent,tool}.py`, `README.md`
- `llm/engines/loop/engine.py`
- `llm/components/tools/{registry,process}.py`, `README.md`
- `llm/components/memory/{data,processing}.py`, `README.md`
- `llm/components/goals/processing.py`, `llm/components/processing.py`
- `llm/components/skills/data.py`, `README.md`
- `llm/components/refinement/data.py`, `llm/components/agents/README.md`
- `llm/services/runtime/{tools,interactions,processes}.py`
- `llm/services/infrastructure/{processes,observability}.py`, `OBSERVABILITY.md`
- `llm/{README,CONFIGURATION}.md`
- `docs/llm/{architecture,architecture-boundaries,boundary-evolution-review,configuration-audit,completion-processing,domain-hardening,graph-engine,interactions,memory-processing,nested-workflows,handoff}.md`
- `tests/llm/{audit_configuration,test_architecture_boundaries,test_error_boundaries,test_interaction_decisions,test_nested_graph,test_observability}.py`

## 감사 증거와 중간 실패

160개 production Python 파일에서 551개 정적 후보를 수집했다. default helpers, setdefault,
숫자/문자열 fallback, API/dataclass 기본값, schema maximum, literal 비교/min,
cleanup/timeout, retry, score/정렬/자동 선택, 영속 변경 경계를 검토했다.
후보 수는 중복 패턴을 합산한 수가 아닌 코드 행 수이며, grep만으로 적합 판정하지 않았다.
raw audit와 테스트 로그/해시 스냅샷은 `tests/llm/reports/`에만 보관하여 배포하지 않는다.

첫 집중 실행은 잘못 지정한 test_processing 모듈 이름으로 import 오류가 났고 실제 검사 73개는 통과했다.
기존 전체 테스트의 Graph Agent 오류 전달 fixture 한 곳에 남은 tools=[]를 제거했다.
새 renewal test의 잘못된 ID fixture는 실제 approval_request가 생성한 protocol ID로 수정했다.
제품 동작을 테스트에 맞추어 약화시키지 않았으며 수정한 fixture도 최종 검증에 포함한다.
