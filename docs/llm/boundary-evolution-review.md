# Architecture boundary / Skill evolution 결과

1. **Boundary audit:** 영속 소유 관계와 Manager/Engine/Component 역할은 유지했다.
   Contract, invariant, 명시적 execution policy, Application product policy를
   [architecture-boundaries.md](architecture-boundaries.md)에 구분했다.
2. **발견한 제품 정책 누출:** Memory의 자동 keyword 선택·양수 score 필터, 기본 의미 추출 지침,
   Graph의 weight 우선 정렬, 임의 검색/동시성/파일 개수 상한, 모델의 직접 prompt 변경 경로.
3. **유지한 불변식:** atomic/CAS, checkpoint identity, 승인 receipt, 불확실한 효과 재실행 금지,
   JSON/evidence/벡터 검증, 경로 보호, IPC/cleanup 안전, LiteLLM 호환성과 초기화 격리.
4. **계약 변경:** 검색 전략/추출 의미 지침은 명시적 입력이다. Skill 생성·분기·Agent 연결과
   평가·승인은 분리한다. trusted host API와 모델 Tool의 권한 경계는 구분한다.
5. **RAG 검색:** 기존 BM25/vector/hybrid 및 RRF 계산을 유지한다. 후보 수·RRF 상수는 호출자
   설정을 사용하고 품질 cutoff를 추가하지 않았다. 삭제한 임의 상한 대신 양의 정수를 검증한다.
6. **Graph 정렬:** `config.search.relation_ranking`의 `source`/`support_count`를 지원한다.
   미설정은 안정적인 출처/ID 순서다. source hit 순서와 BFS는 유지한다. 반환 support_count는
   서로 다른 문서/청크 근거 개수이며 확률이 아니다.
7. **Memory 검색:** `config.search_strategy="keyword"` 또는 host search_fn이 필요하다.
   keyword는 단어 부분 일치 알고리즘이다. host 결과는 0·음수도 보존하고 min_score는 명시 시만 적용한다.
8. **Memory 추출:** `config.processing.extract_prompt_id` 또는 명시적 host extract_prompt로
   의미 지침을 공급한다. 처리기 사본과 candidate metadata에 프롬프트 버전을 남긴다.
9. **Agent 조합:** 참조 Prompt → inline/상속 system_prompt → 선택 Skill 순서다. 명시한 host의
   고정 system_prompt는 기존 최고 우선순위를 유지한다. purpose는 메타데이터로 유지한다.
   실행 중 편집은 다음 Run에만 적용한다. Graph 조율 Agent는 자식 지침을 덮어쓰지 않는다.
10. **Skill UPDATE/CREATE/FORK:** UPDATE는 기존 CAS, CREATE는 ID 부재, FORK는 부모 CAS와
    새 ID를 확인한다. CREATE/FORK 대상은 Skill만 가능하다. 자동 선택 기준은 없다.
11. **Lineage:** 코드가 parent/parent_revision을 기록하며 생성 후 불변이다. 부모 변경을
    자식에 자동 반영하지 않는다. 영향 조회는 현재 Agent 정의에서 계산한다.
12. **Agent Skill Binding:** 별도 bind_skills 제안이 resources.skills만 교체한다.
    Agent와 연결할 Skill들의 버전을 확인하고 Tool/Engine/Policy/MCP/RAG 설정은 유지한다.
13. **Evaluation:** record_evaluation은 evaluator, baseline/candidate 버전, 결과,
    회귀·개선·Run 근거를 저장한다. 평가 수행과 합격 기준은 앱 소유다. 평가로 대상을 변경하지 않는다.
14. **Approval:** apply/rollback Tool은 기존 approval_required/ToolPolicy/Interaction을 사용한다.
    모델은 category/risk/승인을 인자로 위조할 수 없다. require_evaluation은 명시한 경우에만 적용한다.
15. **Hard limits:** 제품 상한 제거 및 유지한 IPC/참조 traversal/프로토콜/cleanup 근거는
    architecture-boundaries의 표에 있다. host가 명시한 ProviderCalls/Tool 한도는 완화하지 않았다.
16. **저장/호환:** domain/workflow/graph 버전은 모두 기존 1을 유지한다. Kuzu/graph JSON의
    weight 저장 필드는 그대로이며 조회 결과만 support_count로 명확히 했다. 옛 API 별칭이나
    migration은 없다. 미설정 Memory의 예전 전략을 복원하지 않는다.
17. **추가 파일:** architecture-boundaries.md, 이 보고서, components/skills/data.py,
    tests/llm/test_architecture_boundaries.py.
18. **수정 파일:** 아래 목록 참조. Hub와 핵심 영속 모델은 수정하지 않았다.
19. **신규 검증:** 검색 전략/0·음수/cutoff, 추출 Prompt 선택·출처, Prompt 실행 사본,
    동일 corpus 정렬, 제품 상한 부재, 생성 충돌/원자 rollback, 부모 stale/CAS,
    평가 stale/권한 거부, Tool 연산 선택, FORK→평가→승인→바인딩→다음 Run 통합.
20. **전체 결과:** Linux Python 3.12.14에서 llm **1,279 passed / 0 failed / 0 skipped**,
    Hub **110 passed / 0 failed / 0 skipped**. 수정 후 집중 26개도 통과했고 전체와 중복이다.
    [handoff.md](handoff.md) 상단에 정확한 스냅샷·명령·수정한 중간 실패와 검증 범위를 기록했다.
    production Python 159개에서 정적 검토 후보 520개를 분류했다. 원본 로그/후보 보고서는
    tests/llm/reports에만 보관한다. 외부 유료 모델/사내 endpoint/수 시간 실제 운용은 수행하지 않았다.
21. **남은 경계:** 외부 evaluator의 진실성, 임의 host Tool/커스텀 Skill 소비자의 권한 검증은
    host 책임이다. 저장된 평가 결과가 자동으로 의미적 품질을 보증하지 않는다. Graph 정렬은
    frontier별 정렬이며 전역 confidence ranking이 아니다.
22. **앱 권장 구성:** 초기에는 update만 노출하고, 필요 시 create/fork/bind_skills를 명시한다.
    평가자와 승인 UX, 검색/메모리 전략, 동시성·비용 한도는 앱에서 실제 환경에 맞게 선택한다.
    이 권장은 backend의 기본값/프리셋으로 저장하지 않는다.

## 수정 파일

- `llm/components/memory/{component,data,processing,tools}.py`, `README.md`
- `llm/components/rag/{component,data,graph_search,ingestion,prompts,search,splitting,tools}.py`, `README.md`
- `llm/components/agents/component.py`, `README.md`, `llm/engines/graph/agent.py`
- `llm/components/skills/component.py`, `README.md`
- `llm/components/refinement/{component,data,tools}.py`, `README.md`
- `llm/components/tools/builtin/catalog.py`, `llm/services/results.py`
- `llm/providers/requests.py`, `README.md`
- `llm/services/runtime/interactions.py` (traversal safety 주석)
- `llm/{README,CONFIGURATION}.md`, `llm/components/README.md`
- `docs/llm/{architecture,builtin-tools,graph-engine,memory-processing,memory,provider-validation,rag-components,configuration-audit,handoff}.md`
- `examples/llm/graph_rag.py`, `graph_rag.md`
- `tests/llm/{audit_configuration,configuration_fixtures,test_builtin_tools,test_memory_component,test_memory_processing,test_project_component_settings,test_rag_extraction_repair,test_refinement}.py`
