## 2026-10-03 사용자·개발자용 README 정리

- `llm/`의 README 22개를 정리했다. 기존 8개는 개요·사용 흐름부터 읽도록 개편하고,
  README가 없던 14개 코드 폴더에는 역할과 직접 소유한 파일의 설명을 추가했다.
  132개 운영 Python 파일 모두 해당 폴더 README에서 찾을 수 있다.
- 메인 README에 ish 설치/명령 등록, import 가능한 worker 진입점, ProjectConfig 인증,
  LargeLanguageModel의 Request → Run 결과 대기, 기존 Session 재조회, 비동기 UI,
  설정·구독·승인 경계, 새 Engine/Component 구현 예제를 넣었다.
- Tool의 옛 JSON 저장 예시, RAG 배치 임베딩·고정 temperature 표현 등을 바로잡았다.
  AgentNode의 별도 Engine 등록/for_agent, 명시적 재개, Component 등록과 선택,
  JSON 공통 CRUD와 Tool/RAG/Memory 전문 API의 차이를 명시했다.
- 운영 Python 코드에는 변경이 없다. 이전 검증(3lys99mz)의 llm/*.py 132개 해시와 같다.
  문서 검사 스크립트/결과는 배포·자동 발견에서 제외되는
  `tests/llm/reports/readme_audit.py`와 `readme_audit.json`에 있다.
  README 22개, 내부 링크 232개, Python 블록 23개, 파일 안내 누락 검사를 통과했다.
  문서에서 직접 추출한 코드로 확장 Component CRUD/검증, EchoEngine, facade reopen,
  정의 저장, 구독 정리, Graph/Agent/Pipeline 구성 및 메인 Loop 예제 등 11개 검사를 통과했다.
  Loop 예제의 모델 함수는 fixture로 주입했으며 실제 API나 TUI 화면 검사는 하지 않았다.
- Linux Python 3.12.14: 집중 191개 통과(131.218초), 전체 1,072개 통과(416.751초),
  실패 0 / skip 0. 집중 검사는 전체에 포함되므로 합산하지 않는다.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  Linux 소스 스냅샷: `/home/user/.cache/ish-provider-ue8xjzaa`.
  결과: `tests/llm/reports/ish-provider-ue8xjzaa/{focused.txt,suite.txt,snapshot.json,source-verification.json}`.
  전체 검사 후 현재 Python 소스 270개의 해시가 스냅샷 manifest와 일치함을 확인했다.
  이번 요청은 문서 정리이므로 GitHub 업로드는 수행하지 않았다.

## 2026-10-03 Run 정책 보호와 구독 자원 회수

- 정책 보호를 먼저 적용·검증한 뒤 구독 회수를 적용했다. EventContext.update_metadata는
  policies/completions/resume/checkpoints/output이 하나라도 있으면 전체 갱신을 거부한다.
  사용자 JSON 키와 Project 설정 API는 유지한다. StorageIO에 Run을 명시적 인자로 전달해
  기존 watch/rollback이 저장·commit 실패 시 파일과 메모리까지 복원하게 했다.
- 수정 전 신규 회귀 3개가 실패했다(rbnoq65d). 정책 단계의 서비스 확장·장시간 실행·
  트랜잭션 집중 64개가 16.210초에 통과했다(jek6qq0_). max_calls=1을 메타데이터로
  지우는 시도를 거부하고 실제 호출은 1회, 다음 호출은 usage_limit으로 끝나는지 확인했다.
- EventSubscriptions는 해제 시 pending 알림을 버리고 막힌 생산자를 깨운다. 진행 중인
  콜백은 취소하지 않고 완료 후 worker/콜백/큐 참조를 회수한다. Subscription.aclose로
  해제 및 정리를 기다린다. 자기 콜백의 aclose/전체 close는 fail-fast하고 동기 해제는
  허용한다. queued 동기 콜백의 해제는 소유 루프로 예약한다.
- 종료 구독별 객체 대신 숫자 합계만 유지하고 기존 stats/Observability 소유권을 지킨다.
  해제 폐기는 overflow dropped와 구분해 집계하지 않는다. idle worker의 마지막 알림/
  on_error 참조도 해제한다. 정상 close는 활성 구독의 알림을 배출하며 동시 close와
  대기자 취소가 다른 대기자/콜백을 취소하지 않는다.
- 숨은 deadline을 추가하지 않았다. 끝나지 않는 콜백은 명시적 callback_timeout이 없으면
  정리를 지연시킬 수 있다. timeout된 동기 콜백 스레드는 실제 반환까지 살아 있을 수 있다.
  Project → Session → Run → Step, 승인/Tool/저장 형식·버전에는 변경이 없다.
- 구독 단계 집중 73개 통과(15.469초, n3gpx967) 뒤 inline 진행 중 해제와 idle 알림 참조
  회수 검사를 추가했다. 최종 집중 75개 통과(15.467초, 3lys99mz).
- 최종 Linux Python 3.12.14 전체 suite는 1,072 passed(335.277초), 0 failed / 0 skipped다.
  집중 검사는 전체에 포함되므로 합산하지 않는다. 종료되지 않은 Task/미회수 예외 경고는
  없었으며 실제 모델 API나 TUI 화면 검사는 수행하지 않았다.
  현재 Python 소스 270개와 Linux snapshot/manifest 해시가 일치한다.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full --modules tests.llm.test_service_extensions tests.llm.test_runtime_reliability tests.llm.test_observability`.
  결과: `tests/llm/reports/ish-provider-3lys99mz/{focused.txt,suite.txt,snapshot.json,source-verification.json}`.

## 2026-10-03 승인 경계와 이벤트·UI 연결 계약

- Graph pause_before confirmation을 Tool 승인으로 전달하던 경로를 수정했다.
  ToolNode는 node.decision을 넘기지 않고 EngineContext.execute_tool에 원래 invocation
  key만 전달한다. ToolExecutor는 waiting Tool interaction/action에 맞는 decision만
  자동 해석하며 confirmation/started 기록에 명시적 Tool decision을 붙이면 fail-fast한다.
  일반 비영속 호스트 decision API, 승인 영수증/재시도/operation/Step 소유권은 유지한다.
- 확인 → host deny, 확인 → 별도 ASK → approve/deny, 중첩 Workflow 및 실제 Linux
  execution worker를 검사했다. 확인/ASK 단계 execution worker 0회, Tool 승인 후
  명시적 resume 1회다. inspector는 prepare/resolve용 별도 runtime isolation이다.
  논리 Tool 요청 집계는 두 번 세지 않는다. 저장 형식·버전 변경은 없다.
- UI 검사에서 INTERACTION_CHANGED가 응답/취소/갱신 transaction 확정 전에 예약되어
  rollback 후에도 전달되고 transaction ContextVar가 관찰자로 전파되는 문제를 재현했다.
  LargeLanguageModel._interaction_changed에서 기존 after_commit을 사용한다.
  세 작업 모두 rollback 시 알림 0회이며, 정상 알림에서 저장 응답/독립 뷰를 조회할 수 있다.
- interactions.md에 Engine PAUSED와 Run PAUSED의 차이, 응답/재개 분리, submitted와
  execution_status, queued/drop_oldest 재조회 및 출력 sequence의 범위를 명시했다.
  Engine README·Graph 체크포인트·아키텍처·트랜잭션 문서도 동일 계약으로 맞췄다.
  아키텍처의 옛 RAG temperature/repair 기본값 설명과 경로 정리 중 생긴 문구 오타도 수정했다.
- 수정 전 Graph 회귀 4개 중 2개 실패를 확인했다(jjzek1g2). 승인 집중 135 passed
  (73.598초, ew1vfyxg). UI commit 회귀는 수정 전 respond/cancel/renew 및 transaction
  context 유출을 재현했다(zqjh___g). 수정 후 이벤트·UI·트랜잭션 집중 99 passed
  (25.302초, 0zw2p57e). 실제 모델 호출이나 TUI 화면 테스트는 아니다.
- 최종 Linux Python 3.12.14: 집중 54 passed (19.758초), 전체 1,057 passed
  (325.533초), 0 failed / 0 skipped. 집중 suite는 전체에 포함되므로 합산하지 않는다.
  snapshot `/home/user/.cache/ish-provider-aijpdsua`의 Python 파일 270개와 현재 소스/
  manifest 해시가 일치한다. namespace·pyproject·Python 버전·AGENTS 파일도 일치했다.
  문서 47개의 로컬 링크 오류는 없다. 최종 결과만 인계 문서에 추가했으며 실행 코드는
  검사 이후 변경하지 않았다.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full --modules tests.llm.test_interactions tests.llm.test_domain_hardening tests.llm.test_observability`.
  결과: `tests/llm/reports/ish-provider-aijpdsua/{focused.txt,suite.txt,snapshot.json,source-verification.json}`.

## 2026-10-03 플러그인 소스와 개발 자료 분리

- 배포 루트는 `llm/`, 향후 인터페이스 플러그인 공간은 `hub/`다. hub에는 안내만 있으며
  아직 PLUGIN_META/실행 진입점은 없다. `ish.platform/`은 호스트 참고 소스로 유지한다.
- 루트와 llm 내부에 나뉘어 있던 테스트를 `tests/llm/`, 예제를 `examples/llm/`로 통합했다.
  import는 `tests.llm.*`, `examples.llm.*`를 사용하며 이전 경로의 별칭은 없다.
  subprocess, fixture, 정적 감사, CLI 호출의 모듈명과 기준 경로도 갱신했다.
  `tests/hub/`, `examples/hub/`, `docs/hub/`는 향후 작업 공간이다.
- 루트의 개발 문서는 `docs/llm/`로 옮겼다. 기존 루트 README는 backend-api.md로 보존하고
  새 루트 README는 작업 공간의 파일 배치와 실행 방법을 안내한다. 설정 감사 및 provider/
  Tool 검증 기록도 docs/llm에 둔다. 공개 컴포넌트·엔진 계약 README는 코드 옆에 유지한다.
- 검사 기록 443개와 이전 검사 스크립트·snapshot·배포 보조 기록은
  `tests/llm/reports/history/`로 모았다. 옛 workflow/research 데모 workspace는
  `history/workspaces/`로 부모 경로만 이동하고 저장 내용을 바꾸지 않았다.
  이동 manifest는 reports/layout-moves.json이며 파일 1,111개를 이동 시 해시 검증했다.
  보관 자료 및 개인 수동 스크립트 934개는 최종 검사에서도 원본 해시와 일치했다.
- 개인 스크립트는 `tests/llm/manual/`에 둔다. 기존 `workspace/`와 개인 `.ishrc.py`는
  유지한다. .ishrc의 예제 import만 새 경로로 바꿨다. 자동 검사는 공개
  `examples/llm/ishrc.example.py`를 사용하며 개인 설정을 읽거나 snapshot에 복사하지 않는다.
- Linux 실행기는 `tests/llm/run_linux.py`다. `--full`은 `tests/llm -t .`로 통합 suite를
  발견한다. 결과는 `reports/<snapshot-name>/`에 보존하고 `reports/latest.json`이 가장
  최근 완료 결과를 가리킨다. reports/manual은 discovery와 snapshot에서 제외한다.
- pyproject와 배포 동기화 스크립트에서 플러그인 내부 tests/examples를 제외했다.
  llm만 복사한 새 프로세스에서 외부 개발 디렉토리 없이 import/CLI 진입이 되는 회귀 검사를
  추가했다. 생산 코드 132개 모듈은 문서 문자열을 제외한 AST가 이전과 같고,
  ish.platform Python 소스 42개도 동일하다. 소유 경계·저장 형식은 변경하지 않았다.
- Linux Python 3.12.14: 이동 경로 집중 70 passed (41.615초). 최종 집중 187 passed
  (79.984초), 통합 전체 1,047 passed (305.636초). 모두 0 failed / 0 skipped다.
  전체에 집중 suite가 포함되므로 합산하지 않는다. 현재 Python 소스 270개의 snapshot/
  manifest/SHA-256 일치 및 최상위 namespace·버전 설정 파일 일치를 검증했다.
  로컬 Markdown 링크 오류는 없으며 실제 모델 API를 호출하지 않았다.
  최종 snapshot: `/home/user/.cache/ish-provider-q56joj56`.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  결과: `tests/llm/reports/ish-provider-q56joj56/{focused.txt,suite.txt,snapshot.json,source-verification.json}`.
  배치 검토: `tests/llm/reports/layout-verification.json`.

---

## 2026-10-03 Linux Python 3.12.14 단일 개발·검증 환경

- `.python-version`의 3.12.14를 유지하고 `pyproject.toml`도 `==3.12.14`로 고정했다.
  구버전 의존성 마커와 async-timeout backport를 제거했다. 현재 LiteLLM/jsonschema/
  LangGraph 의존성 범위는 유지하며 새 라이브러리 설치나 host 리팩터링은 하지 않았다.
- `llm/compat.py`를 제거했다. 제품·예제·테스트는 표준 dataclasses.dataclass,
  enum.StrEnum, asyncio.timeout, contextlib.aclosing을 직접 사용한다.
  Graph의 구버전 LangGraph 감지 분기 대신 지연 import한 NodeCancelledError를 사용한다.
  순수 import 전환 외 제품 실행 로직이 같음을 이전 검증 snapshot의 AST와 비교했다.
  영속 도메인, 저장 형식, Run/Step 수명, 취소·정리 순서는 유지한다.
- 구버전 호환 테스트를 `tests/llm/test_python312.py`로 정리했다. 기존 타입 힌트,
  enum/JSON, frozen dataclass, timeout/취소/stream close, symlink 안전성 검사는 보존한다.
  Linux/정확한 인터프리터 버전과 두 설정 파일의 버전 일치 회귀 검사 2개를 추가했다.
  persistence 테스트는 native slots를 검사한다.
- `tests/llm/run_linux.py`와 `setup_linux.py`는 Linux에서만 실행되며 정확한 3.12.14를
  검사한다. snapshot에 `.python-version`도 포함한다. AGENTS/README/architecture/
  Graph 안내를 갱신하고 과거 버전별 검증 문단을 정리했다.
- 저장소 내부 구버전 가상환경 2개, 전용 검사 보고서 6개, 오래된 egg-info 2개,
  구버전 지원 정보를 담은 build/ 및 wheel 2개, 관련 bytecode 142개를 제거했다.
  경로와 링크 여부를 확인한 뒤 삭제했다. 시스템 Python, 현재 Linux 검증 환경,
  사용자 workspace와 개발 백업 snapshot은 유지한다.
- Linux Python 3.12.14 결과: 표준 API/취소/Graph 집중 107 passed (24.081초),
  최종 focused 187 passed (81.742초), 전체 859 passed (254.834초).
  모두 0 failed / 0 skipped이며 suite 간 일부 중복이 있다. 유료 모델 호출은 없다.
  Python 소스 271개의 현재 파일·manifest·Linux snapshot SHA-256 일치와 AST 구문 검사를
  확인했다. snapshot: `/home/user/.cache/ish-provider-clzgfba4`.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  결과: `tests/llm/reports/{focused.txt,suite.txt,snapshot.json}`.

---

## 2026-10-03 승인·재개 데이터 해석 공통화

- core.interactions.InteractionRequest에 순수 decision_for/select_decision을 추가했다.
  InteractionResponse, InteractionRepository, Loop/Graph validate_resume,
  ToolExecutor 호출 연결 검증과 중첩 checkpoint bridge가 같은 선택지/입력 해석을 쓴다.
  same_interaction_value는 JSON-safe 값의 중첩 true/1 및 1/1.0을 구분한다.
- 응답 누락/거절/빈 confirmation을 구분한다. raw decision이 여러 option과 일치하면
  모호한 응답으로 거부한다. Tool 인자 연결도 같은 엄격한 JSON 비교를 사용한다.
  source/action/binding/응답 지문/만료/취소/갱신 검증을 다른 계층으로 옮기지 않았다.
- 중첩 bridge는 부모 요청과 자식 원본의 scope 연결을 확인하고 option ID로 자식 값을
  복원한다. approved 필드 직접 추출을 없앴다. 서비스가 검증한 갱신 응답을 원본 요청의
  옛 만료 시각으로 다시 거부하거나, 여기서 새 응답 영수증을 만들지 않는다.
- 새 Manager, 저장 도메인, 설정값, migration은 없다. 도메인/Workflow 저장 버전과
  체크포인트 JSON 형식은 같다. Graph의 approved/state, Loop bool, retry_nodes 계약 유지.
  RunManager와 Graph Agent 실행부 및 ToolExecutor._execute는 이전 AST와 같다.
  Graph 실행·저장·취소·ACK 함수도 그대로이며 이번 작업은 ACK 변경을 포함하지 않는다.
- 새 집중 테스트 18개 및 만료된 중첩 승인 갱신 통합 테스트 1개를 추가했다.
  tests/llm/test_observability.py의 불완전한 interaction fixture를 실제 요청 형식으로 교정했다.
  단계별 Linux Python 3.12.14 검사: core 22 passed, 서비스/Tool 59 passed,
  승인/재개/중첩/장시간/worker 집중 120 passed (63.007초), 각 0 failed / 0 skipped.
  최종 focused 187 passed (81.310초), 전체 857 passed (252.803초), 각 0 failed / 0 skipped.
  suite 간 일부 중복이 있다. Python 소스 272개의 현재 파일·manifest·Linux snapshot
  SHA-256 일치를 확인했다. snapshot: /home/user/.cache/ish-provider-9aw4teea.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  상세 결과: tests/llm/reports/{focused.txt,suite.txt,snapshot.json}.
- 순수 해석과 권한 검증의 책임은 docs/llm/interactions.md, docs/llm/architecture.md,
  llm/engines/README.md에 설명했다. 이번 작업에서 원격 업로드는 하지 않았다.

---

## 2026-10-02 Graph 판단·실행 분리

- engines/graph/engine.py 내부에 순수 _branch_port/_loop_continues/_plan_node를
  추출했다. 분기 우선순위·반복 종료/상한·완료 재사용/사전 대기/실행 판단을 JSON만으로
  검증할 수 있다. _NodePlan은 즉시 소비하는 내부 결과이며 decision은 독립 사본이다.
- 새 Manager, 저장 도메인, 공개 API, 설정값은 추가하지 않았다. GraphEngine 공개 클래스의
  AST와 _WorkflowRuntime의 _compile/_parallel/_call/_record/_nested/run은 이전과 같다.
  변경된 _loop/_execute_node/_node에서도 await 호출·예외 분류·async context AST가 같음을
  비교했다. LangGraph 스케줄링, 공유 예산/세마포어, 승인/binding/retry_nodes 검증은 유지한다.
- started 체크포인트 확인 → Step 시작 → 처리기 효과 → completed 체크포인트 확인 →
  Step 완료 순서와 실패/취소 경계를 유지한다. 완료 노드는 재실행하지 않고 재사용하며
  waiting은 처리기 호출 전에 저장한다. Workflow/체크포인트/도메인 저장 형식은 변경하지 않는다.
- tests/llm/test_graph_decisions.py에 순수 판단 8개, 실제 저장/이벤트 경계 2개 테스트를
  추가했다. 검토 입력을 반영한 분기, 완료 재사용 시 효과 중복 없음, 원본 체크포인트 유지,
  비교 오류가 기존 Step/Run 실패 경계로 전달됨도 검사한다.
- Linux Python 3.12.14: Graph/체크포인트/중첩/Agent 집중 111 passed (51.006초),
  최종 focused 169 passed (83.367초), 기존 전체 856 passed (252.355초).
  모든 suite 0 failed / 0 skipped이며 suite 간 일부 중복이 있다.
  현재 소스·manifest·Linux snapshot의 Python 파일 271개 SHA-256 일치를 확인했다.
  snapshot: /home/user/.cache/ish-provider-f20hivcm.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  상세: llm/engines/README.md, tests/llm/reports/{focused.txt,suite.txt,snapshot.json}.
  이번 작업에서 원격 업로드는 하지 않았다.

---

## 2026-10-02 Run 상태 전이 검증

- core.models.validate_run_transition은 start(PENDING → RUNNING),
  finish(RUNNING → COMPLETED/FAILED/INTERRUPTED/PAUSED),
  recovery(PENDING/RUNNING → INTERRUPTED)를 순수 검증한다.
  종료한 Run을 다시 열지 않으며 명시적 재개는 기존처럼 새 Run을 만든다.
- RunManager 내부 불변 _RunOutcome이 종료 상태·오류·오류 코드를 묶는다.
  _finish는 전이/종료 정보/Session current_run_id를 변경 전에 검증한다.
  중복 종료와 늦은 이전 Run 종료는 저장을 변경하지 않고 거부한다.
- 기존 _worker/_begin/_consume_limited/_finish 호출 순서, try/except/finally 위치,
  트랜잭션, Step/Assistant/Run/Session 저장 순서와 저장 후 알림은 유지했다.
  복구는 검증 함수만 공유한다. 공개 API, Engine/Component 책임, 저장 버전 1은 유지한다.
- tests/llm/test_run_transitions.py에 전이 표, 종료 정보 불변성, 중복/잘못된 종료,
  새 Run 보호, 반복 복구, 상태별 저장 후 알림, Engine 첫 명령 전 취소,
  종료 저장 실패 시 rollback 및 알림/완료 통계 억제 회귀 테스트 9개를 추가했다.
- Linux Python 3.12.14: 전이/트랜잭션/Run API/신뢰성 집중 69 passed (13.741초),
  최종 focused 159 passed (84.308초), 기존 전체 856 passed (236.424초).
  각 suite 0 failed / 0 skipped이며 suite 간 일부 중복이 있다.
  현재 소스·manifest·Linux snapshot의 Python 파일 270개 SHA-256 일치를 확인했다.
  snapshot: /home/user/.cache/ish-provider-lwzako_4.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  상세: llm/services/README.md, tests/llm/reports/{focused.txt,suite.txt,snapshot.json}.
  이번 작업에서 원격 업로드는 하지 않았다.

---

## 2026-10-02 Durable Tool invocation 연결 helper 및 검증

- EngineContext.execute_tool/BaseEngine.execute_tool을 추가하고 Loop/Graph ToolNode를 연결했다.
  기존 invocation checkpoint key를 그대로 쓰며 저장 형식과 승인/receipt/retry/Step ownership은 변경하지 않았다.
- ToolExecutor.execute도 durable 승인 재개의 연결을 검증한다. 키 누락·모호함·불일치는
  ToolInvocationError(tool_invocation_invalid)로 실행 전에 거부한다. 일반 호출과 pause_before는 구분한다.
- decision 생략은 기존 Interaction 선택지에서 해석한다. 명시적 decision은 durable 승인 응답과 일치해야 한다.
  관측 오류가 아닌 Engine 계약 검증이며 observer 없이도 수행한다. source of truth는 기존 Interaction/checkpoint다.
- Linux Python 3.12.14 최종 focused 150 passed / 0 failed / 0 skipped (82.960초),
  기존 전체 856 passed / 0 failed / 0 skipped (328.703초).
  snapshot: /home/user/.cache/ish-provider-nok4fhny. 상세는 docs/llm/tool-validation.md,
  llm/engines/README.md 및 tests/llm/reports를 참고한다. 이 변경은 아직 원격에 업로드하지 않았다.

---

## 2026-10-02 Project Tool runtime isolation 및 관측 경계

- Project Python source의 prepare/resolve introspection과 실제 main 호출을 모두
  child interpreter로 이동했다. backend는 JSON descriptor와 호출 adapter만 보유한다.
  호출마다 fresh execution worker를 만들며 source/requirements/descriptor fingerprint를 검증한다.
- ToolExecutor의 승인·재시도·operation receipt·Step ownership은 유지한다.
  ASK/DENY 상태에서 execution spawn 0회, 승인 후 resume에서 1회임을 검증했다.
  Host builtin 및 RAG/Memory/MCP capability는 일괄 subprocess 전환하지 않는다.
- 기존 process group/parent-death helper를 재사용한다. inspector 취소 및 Run interrupt,
  timeout, backend shutdown 시 worker/descendant 회수를 검사했다. 이는 runtime isolation이며
  filesystem/network sandbox를 제공하지 않는다.
- ToolExecutionError의 effect/retryable과 trusted CodedError의 code를 보존한다.
  unknown 오류는 uncertain/nonretryable tool_failed, infrastructure는 작은 worker taxonomy로 구분한다.
- backend.observability.snapshot()/asnapshot()은 공통 실행 경계의 누적 관측과 기존 owner의
  현재 gauge를 합성한다. 승인 재개와 retry를 구분하고 bounded/privacy-safe recent와
  실패가 실행 결과에 영향을 주지 않는 host sink를 제공한다.
- 최종 Linux Python 3.12.14 native snapshot 검사: focused 145 passed / 0 failed / 0 skipped
  (78.572초), 기존 전체 856 passed / 0 failed / 0 skipped (228.015초). 두 suite는 일부 중복된다.
  Python 파일 269개의 현재 소스·manifest·Linux snapshot SHA-256 일치를 확인했다.
  snapshot: /home/user/.cache/ish-provider-r34w5ztp.
  명령: `wsl -e /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python /mnt/d/WorkSpace/ish/tests/llm/run_linux.py --full`.
  상세: docs/llm/tool-validation.md, services/infrastructure/OBSERVABILITY.md,
  tests/llm/reports/{focused.txt,suite.txt,snapshot.json}. 이번 작업에서 원격 업로드는 하지 않았다.

---

## 2026-10-02 출시 전 저장 형식 버전 통일

- 사용자 요청으로 Project/Session/Run/Step의 storage_version과 RAG corpus의
  graph_schema_version을 모두 1로 통일했다. 저장 구조와 실행 동작은 그대로다.
- version 2를 자동으로 변환하거나 지원하지 않는다. 기존 저장 파일은 수정하지 않는다.
  Workflow/checkpoint/Tool 원장/backup/WAL의 버전은 이미 1이므로 유지한다.
- 아래 날짜별 기록의 version 2는 당시 구현을 설명하는 이력이다. 현재 계약은 이 항목을 따른다.

---

## 2026-09-29 RAG 추출 검증·수정과 프롬프트 컴포넌트

- `components/prompts`의 PromptComponent는 messages/few-shot 정의의 공통 CRUD를 제공한다.
  기본 컴포넌트 목록에도 등록했다. RAG 지침과 검증은 계속 components/rag가 소유한다.
- RAG extraction 설정은 `ProjectConfig.component_configurations.rag.extraction`에 둔다.
  repair_attempts 기본 2, 0 비활성화, prompt_id 선택, relation_types 우선 목록을 제공한다.
  TripleExtractor는 최초/수정 호출 모두 temperature=0을 적용하고 유효 설정에도 표시한다.
  JSON/엔티티 참조/인용 오류에만 오류·직전 JSON·chunks로 제한된 수정 요청을 한다.
  연결 실패·취소는 수정 루프로 재시도하지 않고 모델 사용량/한도는 각 호출에 적용한다.
- Prompt 내용은 준비/색인 재개 fingerprint에 포함된다. 준비 중 편집은 공개 시 충돌로
  거부한다. 삭제/미선택 참조는 새 문서 준비 전에 실패하되 설정 편집·자료 조회는 가능하다.
- 관계에는 metadata, 프로그램이 부여한 document_id/extracted_at과 weight를 저장한다.
  weight는 같은 트리플의 서로 다른 문서/청크 근거 수다. 중복 응답·재시도는 가산하지 않고
  문서 수정/삭제 시 Kuzu의 남은 관계도 재계산한다. 검색은 출처와 함께 이를 반환한다.
- RAG corpus의 graph_schema_version=2가 필요하다. 이전 색인은 비파괴 거부하므로 새
  Project에 원문을 등록한다. 핵심 도메인 storage_version=2는 변경하지 않았다.
- graph_rag 예제는 편집 가능한 추출 프롬프트를 저장하고 `--extraction-prompt`를 받는다.
  안내/예제 JSON과 prompts README에 정책·범위·가중치 의미를 설명했다.

검사: **Linux/WSL Python 3.12.14**, 집중 **90 tests OK**, 최종 전체 **850 tests OK,
245.567초**, 생략 없음. tests/llm/reports/history/rag-extraction-suite-python312.txt 및 tests/llm/reports/history/rag-extraction-snapshot.json.
명령: `wsl -e /home/user/.local/bin/python3.12 /mnt/d/WorkSpace/ish/.devtools/rag_extraction_check.py --full`.
네이티브 Linux 스냅샷은 /home/user/.cache/ish-rag-extraction-m9olmsoa에 보존했다.

설치된 `/home/user/.local/bin/ish`(1.1.10)를 별도 테스트 홈에서 PTY로 실제 실행했다.
로컬 HTTP 모델 응답 서버 + 실제 LiteLLM/Chroma/Kuzu/GraphEngine으로 두 추출 오류를
연속 발생시킨 후 수정 2회, 답변 검증/재수정, 문서 CRUD/검색/재개방 모두 성공했다.
20 checks, Run completed, 22 Steps; tests/llm/reports/history/rag-extraction-ish-probe.json 참고.
테스트 홈: /home/user/.cache/ish-rag-host-iwmbczvp. 사용자의 .ishrc.py는 변경하지 않았다.
기존 ish plugin/lib에는 langgraph가 없어 별도 홈에 검증된 의존성을 준비했다.

실제 Gemini: 제공된 키로 gemini/gemini-embedding-2 임베딩은 성공했다. 기존 .ishrc.py의
gemini/gemini-3.6-flash 추출 호출은 별도 2회 실행 모두 503 고부하 오류로 실패했다.
따라서 이 모델을 통한 전체 Graph/RAG 성공은 아직 확인하지 못했다. 다른 모델로 진행할지
사용자 선택을 요청한 상태다. tests/llm/reports/history/rag-extraction-ish-live.json은 비밀값을 제외한 최신 실패 보고서다.
인증 설정은 WSL 테스트 홈에만 있고 저장소 코드에 추가하지 않았다.
사용자 요청으로 llm 폴더만 E1der0wL/ish-llm main에 업로드했다:
60cf6f3e624c9b2de9f4ed596143afac013ff8f5. 원격 HEAD 일치, 배포 파일 138개가
850개 테스트를 통과한 소스와 일치함을 확인했다. 루트 테스트·로컬 인증·실행 결과는 제외했다.

---

## 2026-09-28 Engine별 패키지 분리

- Loop/Graph/Pipeline 구현을 engines/loop/engine.py, graph/engine.py,
  pipeline/engine.py로 옮기고 각 __init__.py를 공개 진입점으로 둔다.
- Graph 전용 AgentNode, ToolNode, EngineCheckpointScope는 graph/agent.py,
  graph/tool.py, graph/checkpoints.py에 둔다. GraphEngine과 내부 Runtime은
  graph/engine.py의 한 파일 안에 유지한다.
- BaseEngine/Engine/EngineContext/EngineEvent와 capability 계약은 base.py에,
  EngineRegistry 등록·조회·설정 검증은 registry.py에 둔다. 옛 평면 helper 모듈과
  base.EngineRegistry 호환 별칭은 제공하지 않는다. 소비 코드·예제·문서를 갱신했다.
- 실행 코드의 AST를 비교해 6개 이동 모듈, 분리한 Registry, 나머지 base 구현이
  import 배치 외에는 동일함을 확인했다. 저장 버전 2와 체크포인트 형식은 유지한다.
- 새 프로세스의 여러 import 순서, 공개 클래스 identity/직렬화, LangGraph 지연 로딩,
  옛 helper 경로 제거를 검증하는 회귀 테스트를 추가했다.

검사: **Linux/WSL Python 3.12.14**, 집중 **111 tests OK**, 전체 **840 tests OK,
210.361초**, 생략 없음. 검증 사본 253개 파일의 SHA256이 원본과 일치한다.
tests/llm/reports/history/engine-layout-suite-python312.txt와 tests/llm/reports/history/engine-layout-snapshot.json 참고.
실행 명령: `wsl -e /home/user/.local/bin/python3.12 /mnt/d/WorkSpace/ish/.devtools/engine_layout_check.py --full`.
배포 폴더의 llm/engines/README.md에 새 구조와 공개 import를 정리했다.
아래 과거 기록의 engines/graph.py 등은 당시 경로다. 이번 변경은 로컬 작업 트리에 반영했다.

---

## 2026-09-28 Task → Session 도메인 명칭 정리

- 영속 소유 계층을 **Project → Session → Run → Step**으로 문서화했다. Engine은
  Run의 실행 전략, Component는 Project가 선택하는 기능과 자료의 소유자다.
- Session/SessionStatus/SessionPaths, SessionManager/SessionRepository/SessionRuntime,
  SessionHandle/SessionRuntimeView와 `services/lifecycle/sessions.py`를 사용한다.
  공개 API는 project.sessions, RunManager(session=...), EngineContext.session,
  Run.session_id이며 ProjectConfig.session_defaults와 session_config로 설정을 전달한다.
- Memory scope="session", session_id와 결과/승인/복구/재개/보관/설정 스키마의 참조도
  같은 명칭으로 통일했다. 대화 저장소의 소유권, 동일 Session 직렬 실행 및 서로 다른
  Session 동시 실행 계약은 유지한다. asyncio.Task 및 처리기 CompletionSession은 별개다.
- Project/Session/Run/Step 저장 형식은 **storage_version=2**다. 새 Session 기록은
  projects/<project>/sessions/<session>/session.json에 둔다. 이전 version 1 프로젝트를
  빈 Session 목록으로 취급하지 않고 명시적으로 거부한다. 자동 변환/호환 별칭은 없다.
  기존 workspace 파일은 변경하지 않았다. 검증에는 새 workspace 경로를 사용한다.
- 예제·테스트·아키텍처/운영 문서를 갱신하고 배포용 llm/README.md에 API 및 저장 형식
  변경을 안내했다. ish Loop 예제는 --session-id로 기존 Session을 선택한다.

검사: **Linux/WSL Python 3.12.14**, 집중 **80 tests OK**, 최종 전체 **839 tests OK,
209.534초**, 생략 없음. 새로운 공개 API/실제 파일 배치/재개방, 이전 버전 읽기·쓰기의
비파괴 거부, 구형 별칭 부재를 검증하는 회귀 4개를 추가했다.
tests/llm/reports/history/session-domain-final-suite-python312.txt와 tests/llm/reports/history/session-domain-final-snapshot.json 참고.
Linux 검증 사본의 소스 247개 파일은 SHA256으로 원본과 일치함을 확인했다.
실행 명령: `wsl -e /home/user/.local/bin/python3.12 /mnt/d/WorkSpace/ish/.devtools/session_domain_final_check.py --full`.

아래 과거 기록의 Task 명칭 및 저장 버전 설명은 당시 구현을 가리킨다. 현재 계약은 위의
Session 도메인과 storage_version=2이며 이번 변경은 로컬 작업 트리에 반영되어 있다.

---

## 2026-09-28 배포 가능한 장애 복구·다중 프로세스 검사

- `examples/llm/recovery_probe.py`와 `.md`를 추가했다. API 키 없이 Linux Python
  3.12.14에서 실행하며 매번 독립 테스트 디렉토리에 report.json과 자식 로그를 보존한다.
- 실제 SIGKILL 후 새 프로세스에서 Run/Step/Assistant 중단 상태와 부분 출력,
  QUEUED 요청의 완료, 합성 작업 효과의 비재실행을 확인한다.
- Run 시작·출력 fsync·Run 종료 저장에 각각 ENOSPC를 한 번 주입해 rollback과
  새 프로세스의 후속 요청 처리를 확인한다. 실제 디스크나 inode를 채우지는 않는다.
- 로컬 SSE 서버가 첫 토큰 전/부분 토큰 후 TCP를 끊는다. 실제 LiteLLM SDK를 통해
  실패·부분 출력 보존·다음 요청 완료·재개방을 검증하며 외부 모델 호출은 없다.
- 같은 workspace의 경쟁 프로세스 생성·조회 거부와 기록 불변, 소유자 종료 후 잠금
  획득을 검사한다. 여러 프로세스의 동시 쓰기를 허용하는 구조로 변경하지 않았다.
- 검사 자체의 예외/중단 시 생성한 자식 프로세스를 종료·회수한다. 회귀 4개를 추가했으며
  메인 도메인 구현과 저장 형식은 변경하지 않았다.

검사: **Linux/WSL Python 3.12.14**, 집중 **4 tests OK**, 전체 **835 tests OK,
298.575초**, 생략 없음. tests/llm/reports/history/recovery-probe-suite-python312.txt와
tests/llm/reports/history/recovery-probe-snapshot.json 참고. 검증 사본 245개 파일의 SHA256이 원본과 일치한다.
지속적인 디스크 고갈/전원 장애, 사내 모델의 DNS·TLS·프록시, 임의 외부 Tool의
정확히 한 번 실행, 장시간 부하를 모두 검증했다는 의미는 아니다. 범위는 예제 안내에 명시했다.

---

## 2026-09-28 메인 도메인 단순성·성능 보강

- RunManager의 소비/알림과 이벤트/델타 저장을 구분했다. IO 트랜잭션과 승인 ACK,
  저장 성공 후 알림 순서는 유지한다. 새 Manager/도메인이나 저장 형식은 추가하지 않았다.
- RunOutputState와 RunRepository가 OutputProjection을 공유한다. 델타 본문을 버퍼에
  누적하고 출력 JSONL은 순차 투영한다. 사용자 저장소/저널 읽기 재정의는 유지한다.
- Conversation도 본문 버퍼를 사용한다. 조회/확정/보관 시 Message.content를 문자열로
  제공하며 메모리 rollback은 버퍼와 길이를, 파일 rollback은 기존 캐시 무효화를 사용한다.
  JSONL/fsync/대기열 내구성은 바꾸지 않았다.
- Graph의 노드 계산과 승인·재개·Step 수명을 같은 graph.py 안에서 구분했다.
  준비 단계의 호출별 정의 스냅샷을 검증과 재개 바인딩에 재사용하되 Run 사이에 캐시하지 않는다.
- 역순 Query는 list/dict view를 복사하지 않고 필요한 페이지까지만 순회한다.
  파일 목록의 stat/무결성 확인은 유지한다. 출력 희소 인덱스 위치 선택은 이진 탐색한다.
- 메인 도메인 합성 성능/통합 검사를 examples/llm/domain_performance.py와 .md에 추가했다.
  모델/API 키 없이 실행하며 llm 폴더 배포에 포함된다.

검사: **Linux/WSL Python 3.12.14**, 집중 **122 tests OK**, 전체 **831 tests OK,
301.473초**, 생략 없음. tests/llm/reports/history/main-domain-suite-python312.txt와 tests/llm/reports/history/main-domain-snapshot.json 참고.
완료·실패·취소·승인·중첩 재개·다중 파일 rollback·프로세스 종료 복구를 기존 통합 검사로
검증했고, 누적/역순 조회/순차 읽기 확장 계약/메모리 버퍼 rollback 등 10개 회귀를 추가했다.

성능 비교는 전체 테스트 종료 후 동일 Linux 환경의 원본/수정 사본에서 단독 실행했다.
각 시간은 3회 중앙값, Python 할당 최고치는 별도 추적 실행이다. 기본 부하: 256자 델타
10,000개(2.56M자), 메모리 목록 100,000개, 동시 Task 5개, Graph 작업 노드 8개.

| 미세 측정 | 변경 전 | 변경 후 | 비율 |
| --- | ---: | ---: | ---: |
| Run 출력 누적 | 3.831초 | 0.413초 | 약 9.3배 |
| 출력 저널 투영 | 3.065초 | 0.384초 | 약 8.0배 |
| 메모리 대화 누적 | 1.665초 | 0.100초 | 약 16.7배 |
| 파일 대화 복원 | 1.377초 | 0.051초 | 약 27.1배 |
| 메모리 최신 10건 선택 | 4.065ms | 0.049ms | 약 83배 |

출력 저널 투영의 Python 최고 할당량은 약 10.67MB→5.81MB였다. 반면 스트리밍/대화
버퍼는 최종 문자열 생성 시 약 5.12~5.17MB→5.76~5.81MB로 약 13% 증가했다.
누적 본문의 반복 복사를 없애는 대신 버퍼 용량을 유지하는 교환이며 모든 메모리가 줄었다는
의미는 아니다. 실제 파일 저장 Graph 5 Task는 모두 완료했고 결과/Step/재개방을 확인했다
(통합 전체 3.68초→3.46초; 이 단회 수치를 전체 시스템 처리량 향상률로 사용하지 않는다).
원본 보고서: tests/llm/reports/history/main-domain-performance-before.json, tests/llm/reports/history/main-domain-performance-after.json.

평가: 메인 도메인 코드 단순성과 측정한 스트리밍·복원 경로의 최적화는 높음 수준으로
보강했다. 파일 카탈로그 전체 확인·workspace 잠금·트랜잭션 fsync 비용은 남는다.
사내 디스크/실제 모델에서 3시간·2GB 부하와 컴포넌트 성능은 별도 검증 범위다.

---

## 2026-09-28 설정 Workflow 예제의 사용자 입력·배포 자료 정리

- 제공받은 질문 목록 파일을 삭제하고 예제 코드·설정·문서·테스트에서 특정 프로그램,
  파일 이름, 옵션과 업무 시나리오를 제거했다. 테스트는 합성 설정 자료만 사용한다.
- files는 사용자가 명시해야 한다. plan은 터미널 직접 입력, --request, --request-file,
  --request-file - 표준 입력을 지원한다. 비대화형 입력 누락과 빈 요청은 실행 전에 거부한다.
- 질문을 배포 소스에 저장할 필요는 없다. 실제 실행 시 기존 모델 전달·Run/대화 기록
  저장은 유지하며, 이를 사용 안내에 명시했다. 검증·승인·적용 경계는 변경하지 않았다.
- 예제 회귀 15개 및 전체 **821 tests, OK, 219.514초**. Linux/WSL Python 3.12.14이며
  이번에는 실제 ish 호스트 소스도 최초 수집 전 포함해 생략 없이 검사했다.
  tests/llm/reports/history/configuration-workflow-suite-python312.txt와 tests/llm/reports/history/configuration-workflow-snapshot.json 참고.
  배포 소스·문서·테스트 및 이번 검사 산출물에서 제거 대상 표현이 남지 않았음을 확인했다.

---

## 2026-09-28 사내 설정 변경·검증·승인 Workflow 예제

- examples/llm/configuration_workflow.py, configuration_workflow.config.example.json,
  configuration_workflow.md를 추가했다. llm 폴더 배포에 포함된다.
- 사내 Markdown을 RAG에 색인하고 Graph의 Agent가 검색하여 전체 파일 변경안을 제안한다.
  후보 사본에 호스트가 지정한 검사기를 실행하고 실패하면 제한 횟수만큼 수정한다.
  공통 Tool 승인에서 PAUSED한 뒤 approve/deny CLI로 저장된 결정에 답하고 새 Run으로 재개한다.
- Agent는 검색 Tool만 사용한다. 적용 Tool은 검증 후 Graph에서만 호출하며, 승인 해시와
  원본/참고 파일의 전체 스냅샷을 확인한다. 원본 다중 파일 쓰기는 공통 트랜잭션을 재사용한다.
  Tool 원장과 원본 변경은 분리된 저장 단위이며 불확실한 외부 효과를 자동 재실행하지 않는다.
- --report-only는 변경안을 거부하고 비교 표만 반환한다. 요청은 사용자가 입력하며
  프로그램별 문법을 라이브러리에 하드코딩하지 않았다. 기존 도메인 구현은 그대로다.
- 실제 사내 검사/dry-run은 validators.argv로 지정해야 한다. 미지정 시 변경을 막는다.
  후보 코드를 평가하는 검증기의 OS 격리는 호스트 래퍼가 담당한다. cwd는 샌드박스가 아니다.
- 새 회귀 13건: 실제 로컬 RAG+Graph, 검증 실패 후 재수정, 승인 전 원본 보존, 재개방 승인,
  거절/변경된 원본/승인 해시 불일치/거짓 인용/후보 변경 거부, 비교 전용, 적용 중 rollback.

검사: Linux/WSL Python 3.12.14, 전체 **819 tests, OK (skipped=1), 218.081초**.
테스트 수집 당시 사본에 없던 ish.platform 로더 검사는 실제 호스트 소스를 복사한 뒤
별도 **1 test, OK, 1.051초**로 검증했다. 신규 13건도 별도 통과했다.
로그: tests/llm/reports/history/configuration-workflow-suite-python312.txt, tests/llm/reports/history/configuration-workflow-host-python312.txt,
tests/llm/reports/history/configuration-workflow-focused-python312.txt. 사본: tests/llm/reports/history/configuration-workflow-snapshot.json (240개 파일 해시 확인).
실제 사내 모델·대상 프로그램 호출과 GitHub push는 수행하지 않았다.

---

## 2026-09-28 메인 도메인 공통 저장 트랜잭션

- services/infrastructure/transactions.py의 TransactionManager를 WorkspaceOwnership에
  연결했다. StorageIO/Facade/동기 Manager의 기존 소유권 경계가 저장 단위이며 중첩 호출은
  같은 단위에 참여한다. 잠금 후 읽기 전에 미완료 저장을 복구한다.
- Run 시작/종료 → 출력/Step/체크포인트 → 승인/재개/Tool 원장 → Project/Task 수명,
  복제/보관/복구와 백업 복원을 연결했다. 전체 범위와 확장 계약은
  [저장 트랜잭션](storage-transactions.md)을 참고한다.
- 확정 마커 전 실패는 undo, 확정 후는 결과 유지/GC다. JSONL은 이전 길이만 기록하고
  metadata 교체는 hard link로 이전 inode를 보관한다. 스트리밍 전체 파일 재작성은 없다.
  파일 교체 임시본/백업 복원 staging도 저널이 회수한다. 새 트리는 확정 전에 동기화한다.
  디렉토리 이동은 목적지를 먼저 동기화한다. 재복구 진행 마커와 저널 체크섬을 검증한다.
- 삭제는 트리를 격리하여 확정하고 물리 삭제는 재시도한다. 확정 전 실패한 생성/복제/
  기본 프로젝트/기억 병합/보관은 부분 결과를 남기지 않는다. 기존 부분 실패를 기대하던
  테스트를 원본 보존/명시적 재시도 계약으로 수정했다. 기존 삭제·보관 복구 API는 유지한다.
- 파일 대화 캐시는 rollback 시 무효화하고 메모리 대화는 메시지 단위로 되돌린다.
  Task 실행 소유권은 확정 여부가 불명확한 저장 오류에도 해제한다. 운영 로그/캐시 제거는
  확정 뒤 실행한다. 자동 승인 응답 실패는 별도 단위로 되돌리고 PAUSED 상태는 유지한다.
- Tool 원장과 Step의 operation_receipt를 함께 저장한다. 외부 효과는 트랜잭션에 포함하지
  않는다. 실행 후 저장 실패는 불확실한 원장으로 남으며 자동 재실행하지 않는다.
  Engine 이벤트/Step 소유권과 Project → Task → Run → Engine → Step 경계를 유지했다.
- 확장 저장소를 기본 구현으로 교체하지 않는다. 공통 파일 API/참여 훅을 사용하지 않는
  외부 DB나 직접 파일 쓰기까지 원자화하지 않는다. 메모리의 재시작 보장도 추가하지 않는다.
  프로젝트 밖 백업은 기존 staging의 원자 공개를 사용한다. 분산 트랜잭션은 아니다.

최종 검사: **Linux/WSL Python 3.12.14, 806 tests, OK, 205.757초**.
새 회귀 24건. 강제 프로세스 종료, 모든 fsync 경계의 old/new 일관성, 복구 도중 재종료,
승인+재개 등록 실패, Tool 효과 후 영수증 저장 실패, 출력/Step 알림 경계, 메모리 rollback,
복원 공개/Component 초기화 실패, 같은 경로 생성·삭제·재생성을 검증했다.
실제 모델 호출/사내 환경 재검증/물리 전원 차단 시험은 하지 않았다.

실행 명령: `/tmp/ish-output-dpfwrktp/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: `tests/llm/reports/history/transactions-release-suite-python312.txt`.
사본: `tests/llm/reports/history/transactions-release-snapshot.json` (실행 코드·테스트 등 234개 파일 SHA-256 일치).
네이티브 Linux 사본/환경은 기존 handoff의 uv 오프라인 복사 절차로 구성했다.
일반 Linux checkout에서는 `.venv-linux312/bin/python -m unittest discover -s tests/llm -t . -v`로 실행한다.

---

## 2026-09-28 사내 Graph/RAG 통합 실행 예제

- examples/llm/graph_rag.py와 graph_rag.config.example.json, graph_rag.md를 추가했다.
  llm 폴더만 배포하는 ish 환경에서 main을 Tool worker에 등록할 수 있다.
- ProjectConfig JSON으로 대화/임베딩/관계 추출 모델과 키를 지정한다. 새 테스트 Project에서
  문서 CRUD, BM25/Chroma/Kuzu 검색, Agent 검색 Tool 호출, Graph 입력/출력·분기·재수정,
  Run/Step 기록과 백엔드 재개방 조회를 검증한다. 기존 도메인 실행/저장 구현은 변경하지 않았다.
- 임시 CRUD 문서는 제거하고 본 문서는 보존한다. 단계별 시간·실패·검색 근거는 workspace/reports에
  JSON으로 저장한다. 답변 검증은 구조·원문 인용 일치이며 의미적 정확성 평가는 아니다.
- 자동 검사는 고정 모델 응답과 실제 로컬 DB를 사용했다. CLI는 실제 모델만 호출하며 사내 호출은 미검증.
  전체 Linux Python 3.12.14 검사: 782 tests, OK, 175.678초. 새 회귀 5개.
  로그 tests/llm/reports/history/graph-rag-example-suite-python312.txt, 사본 tests/llm/reports/history/graph-rag-example-snapshot.json의 232개 파일 일치.

---

## 2026-09-28 안정성 우선 구조·설정·성능 보강

- EngineRegistry는 공개 입력 스키마를 Project/Task 각각에 적용한 뒤 기존 최종 설정
  해석을 호출한다. 호스트 max_iterations=4가 Project의 잘못된 0을 가리는 경로를 차단했다.
  스키마는 UI와 같고, 상속 가능한 옵션은 선택 필드로 선언한다. 호스트 우선순위는 유지한다.
- runtime/output.py의 RunOutputState로 출력 소유권·순서·확정·대화 델타 누적을 이동했다.
  RunManager는 영속 저장·Step 기록·체크포인트·알림 순서를 그대로 소유한다. 확정 결과의
  큰 data/text/metadata는 검증 상태에서 해제하고 원본 이벤트/저장 내용은 유지한다.
- Run/Step FileCatalog의 커서 없는 유한 페이지는 상위 offset+limit 항목만 정렬한다.
  전체 파일 변경/무결성 확인과 커서 오류, 상태/역순/큰 정수 조건은 유지한다.
- RAG는 최종 RRF 결과의 상위 항목만 정렬한다. BM25/벡터/혼합 검색의 동점 순서까지
  기존 전체 정렬과 비교했다. corpus JSON·DB 복사·공유 잠금은 그대로 남는다.
- examples/llm/operational_probe.py는 시간/Task 수/본문 크기/간격을 조절하는 합성 운영 측정이다.
  반복 요청 원본/응답 일치, 정상 종료 후 새 백엔드 조회, RSS/FD/스레드/이벤트 루프 지연을
  보고한다. 실제 모델·2GB RAG·3시간 운영 검증을 대체하지 않는다.

최종 전체 검사: **Linux Python 3.12.14, 774 tests, OK, 171.885초**.
명령: `/tmp/ish-output-9erjpll4/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/stability-refinement-final-suite-python312.txt.
소스: tests/llm/reports/history/stability-refinement-final-snapshot.json. 검토: tests/llm/reports/history/stability-refinement-review.json.
회귀 7건 추가. 최종 소스 222개와 테스트 사본의 해시 일치. 실제 모델 API 호출 없음.

별도 운영 측정: `python -m examples.llm.operational_probe --seconds 60 --tasks 5`.
655 Run 완료, 새 백엔드 결과 조회 성공, 60.517초, 요청 p95 0.446초,
이벤트 루프 최대 지연 0.016초. RSS 31,868→42,804 KiB, FD 7→7, 스레드 2→13.
초기화/스레드풀/대화 캐시를 포함한 짧은 측정이므로 메모리 누수 없음의 증거로 보지 않는다.
보고서: tests/llm/reports/history/stability-operational-probe.json. 임시 실행 자료는 OS의 /tmp 수명을 따른다.

메모리 내 메타데이터 100,000개에서 20개를 고르는 비교(파일 I/O 제외):
전체 정렬 중앙 0.1097초/임시 peak 7,887,848 bytes,
부분 정렬 중앙 0.0254초/임시 peak 2,272 bytes. 결과 동일.
보고서: tests/llm/reports/history/stability-query-benchmark.json. 전체 요청 지연/2GB RAG 성능을 의미하지 않는다.

---

## 2026-09-28 설정 저장 사전 검증과 UI 검증 일치

설정 일관성 재평가에서 남았던 세 항목을 보강했다.

- LargeLanguageModel이 EngineRegistry.validate_configuration을 ProjectManager/TaskManager에
  연결한다. Project 생성·저장·기본 프로젝트·백업 복원, Task 기본값·생성·저장·복제에
  등록 Engine의 동기 설정 계약을 적용한다. Engine 실행/준비/모델 함수는 호출하지 않는다.
- UI values.config.component_configurations에는 기본값을 병합한다. project.config는
  저장 원본을 유지한다. 중첩 required와 배열 항목, 로컬 ref 제약을 제거하지 않는다.
- ProjectHandle.validate_configuration/avalidate_configuration은 전체 ProjectConfig 후보를
  저장 없이 검증한다. expected_version으로 편집 시작 이후 변경을 검사하고, 반환 버전은
  실제 저장 원본에 대한 버전이므로 asave에도 전달한다.
- 같은 설정 키를 공유하는 Engine 스키마는 anyOf 대신 allOf로 결합한다.
- 사용자 정의 Engine의 configuration은 순수 동기 검증 계약이다. 해당 계약이 없으면
  configuration_schema로 명시된 입력을 검증한다. 두 계약 모두 없는 확장 및 미등록
  Engine의 의미는 추론하지 않는다. 독립 ProjectManager 사용자는 검증기를 주입한다.
- 회귀 10건: 생성/저장 실패 시 원본 보존, 기본 Project 포인터 미생성, Task 기본값,
  Graph/Pipeline 설정, 부분 중첩 설정, 배열/ref, 미리보기 CAS, 비활성 Component,
  공유 키 및 검증 입력 분리. 기존 도메인 경계를 유지했다.

최종 검증: **Linux Python 3.12.14, 767 tests, OK, 169.164초**.
명령: `/tmp/ish-output-8zss848v/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/config-validation-final-suite-python312.txt.
소스: tests/llm/reports/history/config-validation-final-snapshot.json. 검토: tests/llm/reports/history/config-validation-review.json.
테스트 소스 220개와 현재 파일의 SHA-256 일치. 실제 모델 API 호출은 하지 않았다.
사용법: [설정 일관성](settings-consistency.md).

---

## 2026-09-28 컴포넌트 설정 저장 경로 단일화

사용자가 ProjectConfig를 거치지 않는 컴포넌트 설정 경로 제거를 요청했다.
이 절은 아래의 과거 component.json/Project 이중 계층 설명을 대체한다.

- 설정 원본은 ProjectConfig.component_configurations뿐이다. Component.configure,
  stored_configuration, ComponentRegistry.configure, ToolPaths.configuration과
  ToolComponent의 직접 enable/disable/set_enabled를 제거했다.
- ComponentData.configure와 ToolData 편의 메서드는 항상 ProjectConfig 검증 후
  project.json을 원자적으로 저장한다. ProjectManager.configure_component도 이 경로를 쓴다.
- Component는 기본값/검증/해석과 자기 자료를 관리한다. 초기화는 records 디렉토리만 만들고
  복제의 설정은 Project가, 자료는 Component가 담당한다. 설정 제거는 기본값 복귀다.
- RAG 생성자의 분할/배치/캐시/document_kwargs/query_kwargs 설정 인자를 제거했다.
  주입 모델의 기본 인자보다 Project의 모델 인자가 우선하며 원본 클라이언트는 보존한다.
- UI 중복 입력인 최상위 component_configurations를 제거했다.
  values.config.component_configurations만 편집하고 components[name]은 조회/진단에 사용한다.
- 기존 component.json은 읽기/쓰기/자동 이전/삭제하지 않는다. 발견 시 명시적 오류를 낸다.
  이전 설정을 ProjectConfig로 옮기고 파일을 별도로 정리해야 한다.
- 직접 capability 해석은 전달된 Project 사본을 사용한다. 저장 후에는 최신 Project를
  조회해서 전달한다. 서비스/Facade는 기존처럼 최신 상태를 조회한다.
- 전체 문서와 예제/테스트를 새 저장 계약에 맞췄고, 파일 미생성·기본값 복귀·충돌·
  제거 API·모델 인자 우선순위 회귀 3건을 추가했다.

최종 검증: **Linux Python 3.12.14, 757 tests, OK, 178.605초**.
`/tmp/ish-output-8a9vvixk/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/component-config-only-final-suite-python312.txt.
소스: tests/llm/reports/history/component-config-only-final-snapshot.json. 검토: tests/llm/reports/history/component-config-only-review.json.
테스트 소스와 현재 실행 파일 해시 일치. 실제 모델 API 호출은 하지 않았다.

---

## 2026-09-28 ProjectConfig 컴포넌트 설정 연결

ProjectConfig에서 컴포넌트 설정 전달이 누락되어 보강했다.
사용법과 의도적으로 호스트에 남긴 항목은 [settings-consistency.md](settings-consistency.md),
실행 구조를 변경하지 않은 단순성 검토는 [code-simplicity-review.md](code-simplicity-review.md)에 있다.

- `component_configurations[name]`: component.json 위에 재귀 병합하는 Project 재정의.
  기본값 → 컴포넌트 파일 → Project → 명시적 호스트 값 순으로 적용하고 출처를 표시한다.
- 등록 이름/설정은 Component 계약으로 저장 전에 검증한다. 비활성 설정은 보관만 하고
  디렉토리나 capability를 만들지 않는다. Task의 컴포넌트 설정 덮어쓰기는 거부한다.
- 재정의는 project.json 한 번의 원자적 교체로 공개한다. ComponentData.configure 및 Tool
  편의 API는 재정의가 있으면 Project 항목을, 없으면 기존 컴포넌트 파일을 수정한다.
  저수준 Component.configure는 컴포넌트 파일 구현 계약으로 유지한다.
- 복제에서 두 설정 계층을 분리하고, 비활성화는 재정의를 보존하며 영구 제거는 삭제한다.
  Graph config_keys 필터로 재개 검증에서 컴포넌트 재정의를 제외할 수 없게 했다.
- UI Project 스키마에 등록 컴포넌트 설정과 지원 여부를 추가했다.
  외부 프로토콜 구현이 Project 설정을 지원하지 않으면 명시적으로 거부한다.
- policies.output으로 델타 저장 묶음 크기/대기/문자 상한을 조율한다.
  null은 ServiceConfig 기본값이며 Run 시작 시 저장한 정책 사본으로 실행한다.
- 새 테스트 9개: RAG 실제 모델 인자와 검색(모형 모델), Tool 선택/편의 수정/Loop 전달,
  사전 검증과 편집 충돌, 복제/재개방/비활성화/삭제, 미선택/미등록/Task 거부,
  사용자 컴포넌트, 저장 실패 시 무변경, Graph 설정 바인딩, 프로젝트별 출력 정책.

최종 검증: **Linux Python 3.12.14, 754 tests, OK, 181.302초**.
`/tmp/ish-output-_xcpv567/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/project-settings-final-suite-python312.txt.
소스 해시: tests/llm/reports/history/project-settings-final-snapshot.json. 검토: tests/llm/reports/history/project-settings-review.json.
실제 모델/API 키 호출과 장시간/대용량 부하 시험은 하지 않았다.

---

## 2026-09-28 설정 일관성

설정 리뷰 후 사용자가 Graph에 국한하지 않고 설정 일관성 보완을 요청했다.
사용법과 기본값 변경은 [settings-consistency.md](settings-consistency.md)에 있다.

- core/configuration.py: 기본값 → Project → Task → Agent → 명시적 호스트 값 병합,
  적용값·출처·편집 가능 여부·가려진 값 조회. 공급자 인자 병합은 실행 객체 동일성을 유지한다.
- Loop/Graph/Preparation/Pipeline은 configuration/schema와 실제 실행 해석을 공유한다.
  Graph는 실행별 사본과 중첩/재개 검증을 유지한다. Pipeline의 공통 설정 상속 및
  사용자 Engine 객체 수명은 유지하고 이름 있는 stages 설정을 추가했다.
- Loop Agent의 별도 8회/60초 기본값을 없애 일반 Loop와 통일했다. Agent 설정으로
  호스트 명시값을 덮어쓰지 않는다. 실행 객체 내부 속성 변경 대신 공개 설정 API를 사용한다.
- RAG의 분할·배치·모델 인자·검색 기본값·색인/DB 옵션을 component.json에 연결했다.
  호출별 사본으로 프로젝트 간 설정을 격리하고, 준비/검색 도중 변경 및 준비된 영속 작업의
  설정 불일치는 공개 전에 거부한다. 모델 구현과 함수를 JSON에 저장하지 않는다.
- Project.configuration은 effective_engines와 components[name].effective를 제공하고,
  Task.configuration 및 ComponentData.effective_configuration의 async API도 제공한다.
  동적 호스트 함수를 UI 조회에서 실행하지 않으며 미확정 값을 표시한다.
- test_settings_consistency.py의 새 12개 테스트로 실제 호출 인자, Graph 제한,
  Pipeline timeout, RAG 프로젝트 분리/검색/설정 변경 충돌/영속 준비 재개를 검사한다.
  기존 직접 내부 속성 변경 테스트는 공개 설정 API로 전환했다.

최종 검증: **Linux Python 3.12.14, 745 tests, OK, 163.081초**.
`/tmp/ish-settings-6kvb02tf/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/settings-final-suite-python312.txt. 스냅샷: tests/llm/reports/history/settings-final-snapshot.json.
실행 소스 해시 일치 및 169 Python 파일 정적 구문 검사: tests/llm/reports/history/settings-review.json.
실제 모델 API 호출과 대용량 부하 시험은 하지 않았다. RAG 전체 코퍼스 읽기,
조회 인덱스, UI 구독 해제 자원 정리 등 이전 리뷰의 성능/수명 항목은 이번 설정 작업과 별개다.

---

## 2026-09-28 공통 진단·조회·계획·진행 계약

사용자가 승인 공통 클래스와 같은 방식의 데이터 계약 정리를 요청했다.
자세한 사용법과 API 변경은 [data-contracts.md](data-contracts.md)에 있다.

- core/contracts.py: Diagnostic, ResourceRef, OperationProgress 및 JSON 변환/검증.
- core/views.py: TaskRuntimeView, RunView. task.run.status/astatus와 run.view/aview가 반환한다.
- core/plans.py: ResumePlan, RecoveryPlan, RecoveryResult, RetentionPlan. 실제 미리보기 및
  복구/보관 서비스에 연결했으며 적용 시 버전·원본·승인 검사는 그대로 유지한다.
- RunRequestError, ExecutionLimitError, ToolExecutionError와 ExecutionResult/Step의
  diagnostic을 연결했다. 진단은 재시도 권한이 아니고 Tool 효과/정책 판단은 기존 코드가 맡는다.
- EngineEvent.progress/diagnostic은 StepEventRecorder에서 소유권을 검사해 Step metadata에
  저장한다. 종료된 Step의 진행 phase도 종료 상태로 갱신한다. RAG job_progress/ajob_progress는
  같은 OperationProgress를 반환하며 Task/Run을 만들지 않는다. total 미상은 None이다.
- 새 객체는 속성으로 접근하고 UI JSON 경계에서 to_dict/from_dict를 사용한다. dict 접근
  별칭은 추가하지 않았다. 설정, Component 정의, LiteLLM 인자, Workflow 상태 및 RAG 검색
  결과/Component maintenance는 열린 dict를 유지한다. 저장 도메인 버전/경로는 바꾸지 않았다.
- tests/llm/test_contracts.py는 직렬화, 독립 사본, 실제 조회/계획 반환 타입, 재시도 정책 분리,
  진단·진행 영속화, 잘못된 소유권 거부와 RAG 작업 진행을 검증한다.

최종 검증: **Linux Python 3.12.14, 733 tests, OK, 156.838초**. 새 계약 테스트 11개.
`/tmp/ish-output-n_cfp_fy/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/contracts-verified-suite-python312.txt. 스냅샷: tests/llm/reports/history/contracts-verified-snapshot.json.
실행 코드 해시 일치; 스냅샷 이후 services/README.md 설명만 추가했다. 검토: tests/llm/reports/history/contracts-review.json.
실제 모델/API 키는 사용하지 않았으며 장시간·대용량 운영 실측은 이번 변경 범위가 아니다.

---

## 2026-09-28 승인·Graph/Agent·Memory 보강

사용자가 권장 기본안을 수락했다. 아래 변경은 Project → Task → Run → Engine → Step
경계와 공통 처리기 계약을 유지한다. API/설정/제약은 [domain-hardening.md](domain-hardening.md).

- InteractionView, 취소/만료 갱신, resume_plan, 저장 후 INTERACTION_CHANGED 알림을 연결했다.
  불확실한 노드/Tool 재실행도 공통 요청·응답 영수증으로 관리한다. 응답 저장은 실행하지 않는다.
- Project policies.approval은 기본 비활성이다. 호스트 카테고리 상한과 위험도 규칙 안에서만
  정책 응답을 남기며 unknown/불확실 재시도는 제외한다. 실제 실행은 명시적 resume가 필요하다.
- Graph cleanup_timeout 이후 남은 작업을 추적하고 같은 Task의 다음 작업/소유권 양도를
  보류한다. 취소 후 추가 Tool 실행과 노드 이벤트를 막는다. 백엔드 종료도 실제 정리 전에는
  소유권과 대화 저장소를 유지한다. 네이티브 코루틴 강제 종료를 보장하는 기능은 아니다.
- config_keys로 실행 외 설정 변경을 허용할 수 있다. completion/engines/task_defaults/policies와
  중첩 Graph별 설정은 항상 해당 엔진의 바인딩 검증을 거친다. 기본은 전체 설정 검증이다.
- 큰 과거 턴/Tool 교환을 분할 요약한다. 전체 단위를 처리하기 전에는 원문을 제거하지 않는다.
  과거 요약 커서는 원본/설정 해시와 함께 저장한다. 활성 교환 캐시는 재시작 시 원본에서 재계산한다.
- Memory recall_every, recall_query_chars, max_summary_calls, 제한된 레코드 캐시와 검색 순위
  주입을 추가했다. 후보 통합은 명시적 consolidate이며 버전·범위 검사와 복구 저널을 사용한다.
  중간 실패 시 CRUD를 차단하고 recover_consolidations로 복구한다. 후보 의미 정확성은 사람이 검토한다.

최종 검증: **Linux Python 3.12.14, 722 tests, OK, 165.378초**. 새 통합 테스트 14개.
`/tmp/ish-output-ro5orlgh/.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/domain-hardening-complete-suite-python312.txt. 실행 소스: tests/llm/reports/history/domain-hardening-complete-snapshot.json.
현재 코드 해시 일치, 검토 기록: tests/llm/reports/history/domain-hardening-review.json.

새 통합 테스트는 tests/llm/test_domain_hardening.py에 있다. 실제 공급자/모델/API 키 호출은 하지
않았다. 5 Task × 3시간/2GB 실측, 모델 요약 정확도와 기억 검색 재현율은 미검증 상태다.

---

# Development Handoff

## Latest: 운영 계약·복구·보관과 동적 UI 설정

- `LargeLanguageModel.project_schema(components=None)`은 등록된 Component/Engine과
  ProjectConfig에서 JSON Schema를 조합한다. `project.aconfiguration()`은 폼 구조의
  values/schema와 config_version/component_versions를 반환한다. 열린 키·호스트 전용
  설정·개별 레코드 스키마를 구분한다. `settings_name`이 있는 Engine의 실제 설정 키도 안내한다.
- ToolContract로 승인/격리/업무 키 요구를 사전 검사한다. ToolPolicy revision과 계약을
  Loop/Graph 재개 지문에 포함한다. `verify_operation`은 외부 probe 결과를 CAS로 저장한다.
  이전 체크포인트는 지문이 달라 재개가 거부될 수 있으며 과거 기록 조회는 유지된다.
- ProjectRecovery는 참조/출력/체크포인트/컴포넌트를 검사하고 안전한 상태만 명시적으로
  복구한다. Runtime 시작 복구와 함수를 공유하며 실제 LLM/Tool을 재실행하지 않는다.
- `retention.unit="run"`은 Task를 유지하면서 완료 Run/대화를 정리한다. 재개·사용량·외부
  원장·컴포넌트 출처를 보호한다. 의도/감사 저널, 부분 디렉토리 삭제 복구,
  정리 미완료 Task 실행 차단 및 `arecover_retention()`을 연결했다.
- Component maintenance 훅을 추가했다. RAG의 job_max_age_seconds로 완료/취소 색인
  작업을 정리하되 실패/대기/활성 worker는 보존한다. 기본은 무삭제다.
- 독립 RAG 모델 호출을 ComponentUsage에 연결했다. 예약과 한도 검사를 같은 잠금으로
  수행하고 원본 영수증은 소유 Component 아래에 둔다. Run Completion과 합쳐 Project
  한도를 검사하며, Tool 내 호출은 소유 Run 한도에도 포함한다. 사용량 누락/취소/손상은
  한도에서 사라지지 않는다. `project.amodel_usage()`와 Component.amodel_usage로 조회한다.

문서: [운영 및 UI 설정](operations-and-ui-settings.md), architecture.md, project-settings.md,
long-running.md, production-readiness.md. LargeLanguageModel 독스트링에도 공개 API를 추가했다.

최종 검사: **WSL Linux / Python 3.12.14, 694 tests, OK, 140.952초, skip 없음**.
`/tmp/ish-output-w66_jtsm/.venv/bin/python -m unittest discover -s tests/llm -t . -v` 실행.
로그: tests/llm/reports/history/operations-schema-final-suite-python312.txt. 소스: tests/llm/reports/history/operations-schema-final-snapshot.json.
검토: tests/llm/reports/history/operations-schema-review.json. 현재 소스 해시 일치. 런타임은 요청한 3.12.14만 사용했다. 실제 모델/API 키는 사용하지 않았다.
새 운영 테스트 20개, 기존 RAG 세대 충돌 테스트는 모델 예약 완료 이후 준비/공개 경계에서
삭제/재생성을 검증하도록 조정했다. 활성 모델 예약 중 삭제 거부는 별도로 검증했다.

남은 범위: 실제 5 Task × 3시간/2GB 부하 및 배포 환경 sandbox 검증, 전체 corpus JSON과
공유 잠금/영수증 집계 비용, 사용량 영수증 자동 삭제 및 공급자 청구 조정. 외부 멱등/결과
조회 없는 exactly-once는 보장하지 않는다. 사용자 직접 SDK는 명시적 관찰 계약이 필요하다.

## 이전: 구현 간 연결과 장시간 운영 보강

기존 Project → Task → Run → Engine → Step을 유지했다. Engine은 이벤트만 발행하고,
기록은 RunRepository/StepManager, 요약과 색인 처리는 소유 컴포넌트가 담당한다.

- Memory의 과거 턴/현재 Tool 교환 요약은 모델에 보낸 완전한 구간만 대체한다.
  이전의 입력 잘라내기 후 전체 구간 제거를 없앴고, 큰 단일 교환은 원문으로 남긴다.
  현재 작업 요약은 배치로 합치고 최신 접두 구간 캐시 한 개만 유지한다.
- Tool `effect=none` 최종 실패를 작업 원장 `not_applied`와 연결했다. 명시적 새 시도는
  이전 Run/Step/시각/증거를 보존한다. 앞선 시도가 불확실하거나 기록 실패이면 완화하지 않는다.
- EngineCheckpointScope가 중첩 Loop Agent 체크포인트를 부모 Graph 호출 경로에 연결한다.
  재시작 이후 완료 Tool 결과 재사용, 승인 대기/거절, 불확실한 자식 호출의 retry_nodes,
  Agent 누적 호출 예산과 대기 예약 복원을 지원한다. 새로운 Run 계층은 만들지 않는다.
- RAG worker는 OS 파일 잠금으로 생존 여부를 판정하며 반복 취소에도 핸들을 정리한다.
  임베딩/관계 추출 완료 배치를 저장하고 명시적 재시도에서 재사용한다. 공개 전 취소는
  검색 세대에 반영하지 않는다. Linux reflink 지원 시 DB 복사를 최적화하고 미지원 시 복사한다.
- BM25는 크기가 제한된 세대별 캐시를 사용한다. RunRepository의 사용량 투영은 변경 파일만
  다시 파싱하며 사용자 저장소의 list/load 재정의 계약을 보존한다.

설정/사용법은 docs/llm/long-running.md, 구조는 docs/llm/architecture.md,
운영 경계는 docs/llm/production-readiness.md에 반영했다. 실제 모델/API 키는 사용하지 않았다.

최종 검증: **WSL Linux / Python 3.12.14, 674 tests, OK, 139.658초, skip 없음**.
Linux 소스 스냅샷 `/tmp/ish-output-ruvr95gu`에서
`.venv/bin/python -m unittest discover -s tests/llm -t . -v`를 실행했다.
로그: tests/llm/reports/history/integration-hardening-release-suite-python312.txt.
소스 해시: tests/llm/reports/history/integration-hardening-release-snapshot.json. 검토: tests/llm/reports/history/integration-hardening-review.json.
660개 기준에서 14개 검사를 추가했다. 실제 Chroma/Kuzu, 배치 실패/취소/재개, 중첩 Agent
승인/거절/재시작/불확실 효과, 요약 범위 보존, 사용량/BM25 캐시를 검증했다.
현재 소스와 스냅샷 해시가 일치했다.
런타임 검사는 요청대로 3.12.14 단일 버전만 수행했다.

아직 별도 작업인 항목: 독립 RAG 호출까지 포함한 공통 사용량 예약, 비활성 Task 내부의
개별 Run/대화 보관 정리, corpus 전체 JSON/공유 잠금 비용 해소. 실제 5 Task × 3시간 및
2GB 코퍼스 부하 시험도 수행하지 않았다. 이 변경을 그 규모의 운영 보장으로 해석하지 않는다.

## 이전: 장시간 실행·조건부 재시도·복구·성능·UI API

Project → Task → Run → Engine → Step을 유지하면서 다음을 추가했다.

- 최상위 Loop 회차/개별 Tool 영수증 체크포인트와 명시적 재개. 완료 None 결과도 재사용한다.
  재사용 Step은 새 Run에서 출처를 연결하며 Tool을 다시 호출하지 않는다. Graph Agent의 내부
  Loop는 Graph 노드 경계를 유지한다. 불확실한 효과는 retry_nodes 승인이 필요하다.
- ToolExecutionError(effect, retryable), 호스트 retry_safe_tools와 프로젝트 tool_retry를 통한
  조건부 재시도. 일반 예외/취소/기한 초과/결과 저장 오류는 자동 재실행하지 않는다.
- ToolApprovalRequired를 통한 영속 승인 대기. 최상위 Loop와 Graph ToolNode를 decisions로
  재개한다. Graph waiting 상태 수정은 닫힌 resume_schema를 검증하고 새 Run에 저장한다.
- BaseEngine 호출 전 토큰 예약/Run·Project 누적 사용량 상한, 응답 전 429/502/503/504 재시도.
  텍스트 없는 Tool 인자 조각도 부분 응답으로 보며 자동 재시도하지 않는다.
- Memory의 현재 작업 Tool 교환 요약과 같은 Task의 원본 Tool 결과 부분 조회.
  새 공통 처리기 필드로 제거한 완료 교환을 검증하며 Memory 전용 Manager 분기는 없다.
- Task 기록의 기간/바이트/대화 토큰 보관 정책과 version 기반 미리보기·명시적 적용.
  실행/재개/작업 원장/사용량 집계에 필요한 기록은 보호한다. 기본은 무삭제다.
- Project/Component expected_version 편집 충돌 검사, Run 출력 저널 → Conversation 본문 복구,
  영구 삭제 의도 저널과 명시적 Project/Task 삭제 복구, UI 동일 잠금 출력/커서 조회.
- RAG 영속 문서 등록 작업·명시적 worker/재시도/취소, 완료 준비 결과 재사용, 임베딩 배치,
  변경 문서의 Chroma 벡터/Kuzu 관계 갱신. 첫 생성과 사용자 _build 확장은 유지한다.
- Run/Step 조회는 변경 파일의 작은 목록 메타데이터를 캐시하고 선택된 기록만 완전히 읽는다.
  사용자 저장소/Query의 구현 교체 계약은 유지한다.

ProjectConfig.policies에 tool_retry/provider_retry/usage/retention을 추가했다. 프로젝트별
정책과 컴포넌트별 처리 설정을 유지하며 기본 Task 수·Run 시간·사용량·보관 상한은 없다.
안전성 선언과 실제 실행기는 호스트가 주입한다. 사용법은 docs/llm/long-running.md,
현재 운영 경계는 docs/llm/production-readiness.md에 정리했다.

주의: 실제 5 Task × 3시간 및 2GB 문서 부하는 아직 수행하지 않았다. RAG corpus JSON과
DB 세대 복사, BM25 메모리 사용, Project 사용량 집계의 전체 기록 조회 비용은 남는다.
동기 SDK 스레드 강제 종료, 외부 효과 exactly-once, Agent 내부 임의 지점 재개를 보장하지 않는다.
유료 모델 호출은 수행하지 않았다. 기존 workspace 자료는 변경하지 않았다.

최종 검증: **WSL Linux / Python 3.12.14, 660 tests, OK, 180.876초, skip 없음**.
명령: `.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/long-running-verified-suite-python312.txt. 소스 해시: tests/llm/reports/history/long-running-verified-snapshot.json.
검토: tests/llm/reports/history/long-running-review.json. 기존 638개에서 22개 검사를 추가했고 실제 Chroma/Kuzu,
Loop 백엔드 재시작·결과 재사용, Graph 승인·상태 검토, 조건부 재시도·부분 응답,
정책 상한·보관·충돌·복구·페이지 조회·Memory 압축 및 RAG 준비 결과 재사용을 검증했다.
테스트 소스와 현재 소스 해시가 일치했다.

## Latest: ProjectConfig 정책 변경 캡슐화

ProjectConfig.configure_policies(changes)가 JSON 검사·재귀 병합·후보 전체 검증을 소유한다.
성공 시 정책만 반영하고 독립 사본을 반환하며, 실패 시 기존 메모리 설정도 보존한다.
ProjectManager는 최신 조회·잠금·저장을 담당하고 해당 메서드에 변경 처리를 위임한다.
별도 Policy 클래스나 실행 계층·저장 형식 변경은 없다. 직접 설정 객체를 수정하는 API는
파일을 저장하지 않는다. 사용 예는 docs/llm/project-policies.md를 참고한다.

검증: WSL Linux Python 3.12.14, `python -m unittest discover -s tests/llm -t . -v`,
638 tests, OK, 120.295초. 로그: tests/llm/reports/history/project-config-policy-refactor-suite-python312.txt.
추가 검사로 실패 시 메모리 설정 보존, 부분 병합, 입력/반환 사본 분리를 확인했다.
테스트 스냅샷과 작업 소스 해시 일치를 확인했다.

## Latest: 프로젝트별 실행 정책과 CompletionPolicy 명칭

ProjectConfig.policies에 context/completion/run JSON 설정을 저장한다. core/policies.py는
기본값·UI용 JSON Schema·검증을 소유하고 services/runtime/policies.py의 ProjectPolicyResolver가
ContextPolicy/CompletionPolicy/RunLimits를 구성한다. Project는 실행 객체를 저장하거나 실행하지 않는다.
기존 Completion 클래스 명칭을 CompletionPolicy로 통일하고 EngineContext 필드도 completion_policy로
변경했다. 과거 이름의 별칭은 없다. ServiceConfig의 context_policy/completion_policy/run_limits 값
인자는 제거했고 token_counters 및 선택적 policy_resolver로 구현만 주입한다. 공유 ProviderLimits와
호스트 Tool 실행/승인 계약, 저장·로그·캐시 구성은 백엔드에 남는다.

ProjectHandle/ProjectManager.configure_policies 및 aconfigure_policies는 최신 정책에 일부 변경만
병합한다. configuration은 policy_schema도 반환한다. 새 프로젝트는 고정 기본값을 명시해 저장하며,
임의 namespace는 유지하되 알려진 정책의 잘못된 값/오타는 저장 전에 거부한다. Task config와
task_defaults에는 policies를 허용하지 않는다. Memory의 자체 처리 설정은 컴포넌트에서 유지한다.

Run 시작 시 최신 Project 정책을 metadata.policies에 복사하며 실행 중 변경은 적용하지 않는다.
대기 요청은 실제 시작 때 최신 정책을 적용하고 대기열 상한은 수락 시 최신 값으로 판정한다.
미등록 계산기는 수락 전 policy_unavailable로 거부한다. 대기 중 설정 의존성이 달라지면 해당
Run을 실패로 기록하고 worker는 계속 동작한다. 기본 model_default는 LiteLLM token_counter에
model/messages/tools/tool_choice를 전달한다. 함수는 저장하지 않으며 환경별 이름 등록이 필요하다.
context_builder의 for_run은 policy keyword로 실행별 선택값을 받으며 for_clone 경로는 유지한다.

Graph 재개는 기존 설정 fingerprint 검사를 유지한다. 정책을 바꿨으면 재개를 거부하며 원래 설정을
복원하거나 새 요청을 제출해야 한다. 재개 원문은 체크포인트의 메시지 ID로 복원한다.
Project/Task/Run/Engine/Step 책임은 유지하며 원본 대화의 저장 범위를 줄이지 않는다.
API와 이전 ServiceConfig 사용 코드의 수정 예: docs/llm/project-policies.md.

검사 로그: tests/llm/reports/history/project-policies-release-suite-python312.txt. 해시: tests/llm/reports/history/project-policies-release-snapshot.json.
정적 검토: tests/llm/reports/history/project-policies-review.json. 도메인 의존성 검사 통과,
테스트 소스 스냅샷 해시 일치. 유료 공급자 호출은 수행하지 않았다.
최종 검사: **WSL Linux / Python 3.12.14, 636 tests, OK, 121.696 seconds, skip 없음**.
명령: `.venv/bin/python -m unittest discover -s tests/llm -t . -v`.

## Latest: 여러 컴포넌트의 모델 처리기 조합

components/processing.py에 CompletionMessage/CompletionRequest/CompletionObservation과
CompletionPipeline을 정의했다. 기존 dict 기반 prepare/finish 계약은 새 계약으로 교체했으며
호환 별칭은 없다. CompletionSession 기본 클래스를 상속해 필요한 훅만 구현한다.
priority/name 순으로 호출별 세션을 구성하고 역순으로 정리한다. 매 유효 모델 응답의
after_completion과 최종 응답의 finish를 구분하며 각 관찰자는 요청/응답/원본 대화의 독립 사본을
받는다. 요청은 CompletionPolicy 적용 이후, provider 어댑터 내부 기본값 적용 이전 스냅샷이다.
aclose는 동일 Task에서 협력적 기한을 적용하고 오류/취소/생성 도중 실패에도 나머지 세션을 닫는다.

원본 메시지 ID는 공급자 payload와 분리한다. Memory는 요약한 ID/내용이 보존됐을 때만 교체하고,
다른 처리기가 추가한 참고자료와 system/developer 메시지는 유지한다. 원문 편집/제거가 있으면
해당 호출의 요약 교체를 생략한다. Memory의 processing.priority는 기본 100이며 설정 가능하다.
공통 검증은 현재 입력, 원본 순서/role, 과거 턴, 현재 Tool transcript 및 Tool/stream/n/retry
계약을 보호한다. Model/Tool 권한 관리와 영속 계층을 처리기로 옮기지 않았다.

직접 소비자는 여전히 LoopEngine이며 Graph의 Loop Agent는 기존 capability 전달 경로를 쓴다.
Project/Task/Run/Step 모델·Manager, BaseEngine/GraphEngine, 영속 데이터 형식은 변경하지 않았다.
Skill/MCP 실제 구현을 새로 연결한 것은 아니다. 확장 예제·실행 범위·남은 제한은
docs/llm/completion-processing.md를 참고한다. 여러 처리기의 쓰기는 다중 컴포넌트 트랜잭션이 아니며,
이미 스트리밍한 텍스트를 사후 검증으로 철회하거나 실패한 Tool 효과를 자동 재시도하지 않는다.

신규 tests/llm/test_completion_processing.py는 Memory 전후 참고자료 주입, 편집된 원문 보존,
순서/출처/Tool 계약 검증, 관찰 사본과 예산, 부분 세션 생성 실패, 정리 실패/기한/ContextVar,
실제 Run 중단, 동시 Task 독립성을 검사한다. 검증 로그는 tests/llm/reports/history/processing-composition-release-suite-python312.txt,
소스 해시는 tests/llm/reports/history/processing-composition-release-snapshot.json, 정적 검토는 tests/llm/reports/history/processing-composition-review.json.
최종 검사: WSL Linux Python 3.12.14, **624 tests, OK, 121.925 seconds, skip 없음**.
명령: `.venv/bin/python -m unittest discover -s tests/llm -t . -v`. 실제 유료 모델 API는 호출하지 않았다.
도메인 의존성 검사와 검증 스냅샷 해시 일치도 확인했다.

## Latest: Memory 중심의 장기 문맥 처리

아래는 최초 도입 시점 기록이며 현재 처리기 API는 위 항목과 completion-processing.md를 따른다.
MemoryComponent가 completion_processors capability를 제공한다. Loop는 공통 세션의
prepare(request)/finish(messages, response) 이벤트 스트림만 호출한다. 컬렉션은 빈 tuple도
유효하며 커스텀 capability resolver는 이 요청을 반환해야 한다. 다른 컴포넌트의 처리기도
같은 계약으로 동작한다. Project/Task/Run/Step 모델과 Manager, BaseEngine/GraphEngine/
AgentNode에는 전용 로직을 추가하지 않았다. Graph Agent의 기존 capability 전달을 활용한다.

Memory 내부에 Task 범위 CRUD, 자동 confirmed 기억 검색, 예산 내 참고 데이터 구성,
큰 Tool 결과의 입력용 미리보기, 선택적 보조 모델 요약·후보 추출, 중복/만료/대체 제안 조회를
추가했다. 자동 검색/Tool 미리보기는 기본 활성, 요약·추출은 processing.completion.model과
명시적 설정이 필요하다. 보조 모델 관찰은 내부 Memory Step으로 기록한다. 실패 정책은
기본 raise, continue 선택 시 degraded fallback Step으로 기록한다. 취소는 항상 전파한다.

Task 요약은 memory/contexts/<task-id>.json의 파생 캐시다. 원문 ID/내용/상태와 설정의 해시,
처리 개수·경계를 확인해 증분 처리하며 최근 턴과 원본 대화는 보존한다. 실패/중단한 과거 턴도
상태를 전달하므로 이후 요약을 영구 차단하지 않는다. generation/revision과 컴포넌트
identity/설정으로 오래된 쓰기를 거부한다. 캐시의 이전 결과는 Run의 Step에 남고 캐시 파일에
전체 이력을 중복 누적하지 않는다. 백업에 캐시도 포함하며 새 Task ID를 만드는 clone에는
캐시를 복사하지 않는다. Task 범위 기억의 원래 task_id는 유지한다.

정확한 API/설정/제한: docs/llm/memory-processing.md, docs/llm/memory.md.
MemoryData에 summary/asummary, clear_summary/aclear_summary, review/areview를 추가했다.
중첩 Engine의 요약·추출은 기본 꺼짐이며 nested_processing=True로 명시적으로 켤 수 있다.
메인 요청 예산은 기존 CompletionPolicy이 최종 검사한다. 원본 Tool 결과/체크포인트를
덮어쓰거나 외부 작업을 재실행하지 않는다. 의미 검색/완전한 모순 판별/실모델 품질은 별도다.

최종 검사: WSL Linux / Python 3.12.14, **606 tests, OK, 105.965 seconds, skip 없음**.
명령: `.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/memory-processing-release-suite-python312.txt, 스냅샷: tests/llm/reports/history/memory-processing-release-snapshot.json.
신규 21개 통합 검사에는 Task 격리, 중단·재시작·동시 추출·캐시 ABA 충돌·백업·범용 처리기,
실패 후 요약 진행, 32턴/64메시지 원본 보존과 입력/캐시 크기 제한이 포함된다.
94개 Python 모듈 구문/도메인 의존성 및 테스트 스냅샷 해시 검사 통과:
tests/llm/reports/history/memory-processing-review.json. 유료 실모델 API나 장시간 soak를 실행한 것은 아니다.

## Latest: 프로젝트 장기 기억 컴포넌트

`components/memory`에 MemoryComponent/MemoryData를 추가했다. 기본 등록 목록과 새 기본
Project 선택 목록에 포함된다. 기존 Project의 선택 목록은 바꾸지 않는다. 일반 Project는
`components=["memory"]`로 선택하며 대화의 memory 저장 옵션과 독립적으로 파일에 저장한다.

공개 CRUD·키워드 검색·revision 충돌 검사·변경 이력·삭제/복원/영구 제거를 지원한다.
원자적 JSON 한 파일에 현재 값과 이력을 함께 저장하고 복제/백업에 삭제된 항목도 포함한다.
`tools` capability에는 memory_search/get/create/update/delete가 자동 연결되며 `memory`
capability는 수명 검사 핸들을 제공한다. ToolExecutor의 current_tool_call 문맥으로 출처를
기록하고 기존 ToolPolicy/Step 기록을 공유한다. ToolCall에 input_message_id를 추가했다.
Loop, Graph ToolNode, Agent의 허용 Tool 경로를 검증한다. 엔진의 기억 자동 주입은 없다.

모델 작성/수정은 candidate, 기본 검색은 confirmed다. 프로젝트 설정으로 조절하며 API 작성은
confirmed가 기본이다. 변경 시 expected_revision이 필수다. 기본 검색은 색인 없는 키워드
방식이고 수정 시 해당 기억의 전체 이력을 다시 쓴다. 원격/프로세스 Tool 실행기는 연결된
Memory 서비스 핸들러를 자동 직렬화하지 않으므로 자체 라우팅 계약이 필요하다.

사용법·설정·한계: docs/llm/memory.md. 통합 검사: tests/llm/test_memory_component.py.
실제 유료 모델 호출 대신 결정적 LiteLLM 형식 스트림을 사용했다.
최종 검사: WSL Linux / Python 3.12.14, **585 tests, OK, 96.291 seconds, skip 없음**.
명령: `.venv/bin/python -m unittest discover -s tests/llm -t . -v`.
로그: tests/llm/reports/history/memory-component-final-suite-python312.txt. 스냅샷 파일 해시가 작업 폴더와 일치하며
92개 llm 모듈의 구문/금지된 도메인 의존성 검사도 통과했다(tests/llm/reports/history/memory-component-review.json).

## Latest: 실행 자원, 출력 저장과 백업

ProviderCalls/ProviderLimits로 백엔드의 동기 completion 스트림 호출과 대기 수를 제한한다.
취소 후에도 실제 호출/close가 끝날 때까지 슬롯을 유지한다. 같은 백엔드의 Task/중첩
Engine이 공유하며 주입할 수도 있다. 초과는 provider_capacity로 보고한다.

OutputPolicy의 기본값은 즉시 저장(batch_size=1)이다. 선택적인 묶음 모드는 Engine을
하나의 Task에서 실행해 timeout 수명을 지키며 델타만 모은다. Tool 승인/Step/체크포인트/
종료는 저장 ACK 경계다. Conversation과 출력 저널 각각 묶음 append/fsync 후 알린다.
저장 작업은 취소 시 drain하지만 비동기 관찰 콜백까지 무한 대기하지 않는다.

RunRepository는 OutputJournal의 재생성 가능한 outputs.index.json을 사용한다.
after/limit 공개 API를 유지하고 추가된 구간만 인덱싱한다. 간격은 설정 가능하다.
단일 저장을 재정의한 사용자 저장소도 유지한다. 저수준 Manager의 기본 RunRepository도
TaskManager를 통해 공유하도록 보완했다. capability 탐색 상한은 RunLimits에 노출했다.

Project 백업은 runtime이 없는 file 저장 프로젝트를 대상으로 workspace 잠금과
Component.backup_scope 아래에서 수행한다. Component와 각 주입 Repository에 검증을
위임한다. DirectoryBackups는 해시 검사/스테이징 공개/원본 보존을 담당한다.
ProjectHandle.backup/abackup, Projects.restore_backup/arestore_backup과
upgrade_backup/aupgrade_backup을 제공한다. 복원은 기존 ID를 덮어쓰지 않는다.
RAG의 Chroma/Kuzu/문서는 같은 세대로 복사한다. paused Graph 백업 후 명시적 재개도 검사한다.

Project/Task/Run/Step JSON은 storage_version=1을 요구한다. 버전 없는 기존 데이터는
자동 수정하지 않으며 사본에서 명시적 변환이 필요하다. 과거 호환 리더는 추가하지 않았다.
ProjectConfig/Component의 열린 JSON 구조와 도메인 경계는 유지한다.

운영 설정/예제/제한: docs/llm/operational-storage.md. 회귀 검사:
tests/llm/test_operational_storage.py. 실제 공급자 API와 장시간 soak는 이번 자동 검사 범위가 아니다.

최종 검사: **567 tests, OK, 94.345 seconds, skip 없음**.
WSL Linux / Python 3.12.14에서 `.venv/bin/python -m unittest discover -s tests/llm -t . -v`를 실행했다.
스냅샷은 /tmp/ish-output-v3el3tbc이며 183개 파일 해시가 현재 작업 폴더와 일치한다.
로그: tests/llm/reports/history/reliability-v2-release-suite-python312.txt, 해시: tests/llm/reports/history/reliability-v2-release-snapshot.json.
신규 통합 검사 25개를 포함한다. 32개 대화 델타의 1회 fsync, 제한된 커서 디코딩,
취소된 공급자 슬롯/대기 제한, 저장 후 알림, 관찰자 취소, Graph pause 백업·복원·재개,
실제 Chroma/Kuzu 검색 복원, 손상/버전/실패 변환 거부를 검사했다.
전체 88개 llm Python 모듈의 AST도 검사했으며 core→services/engines/UI 또는
engines→lifecycle/파일 저장/UI의 금지된 직접 import는 없었다.
검토 기록: tests/llm/reports/history/reliability-v2-review.txt. 실제 유료 모델 API는 호출하지 않았다.

## Latest: BaseEngine 공통 출력 메서드

BaseEngine.run/action이 EngineDelta를 직접 yield할 수 있다. output_id를 생략한 새 델타를
생성할 수 있으며, BaseEngine은 복사본에 Step 소유권을 부여한다. str은 append, typed delta는
append/replace를 반영하고 최종 EngineOutput이 없으면 누적 텍스트로 결과를 만든다.
빈 replace는 본문을 지운다. 한 Step의 표시 범위 변경은 실패하며 내부 출력을 사용자 범위로
승격하지 않는다. 상속 인스턴스에는 실행별 출력 상태를 저장하지 않는다.

delta_event/output_event/step_completed_event는 순수 이벤트 생성 메서드다. Loop는 상속으로,
Graph/Agent/Pipeline은 정적 호출로 같은 소유권 규칙을 사용한다. Graph의 실행 순서/체크포인트와
RunManager/StepManager의 저장 책임은 유지한다. ToolExecutor도 기존 Step 결과 데이터 계약을 유지한다.
결과 검증을 BaseEngine의 실패 처리 안으로 옮겨 잘못된 최종 결과도 STEP_FAILED로 관찰한다.

추가 검토에서 순번 0이 저장 커서를 되돌리는 문제, 잘못된 Step output 타입이 저널에 기록될
가능성, ID 누락 시 역직렬화가 임의 ID를 만드는 문제를 보완했다. 순번/타입/ID 오류는 거부한다.
후속 후보는 출력 저널의 sequence→파일 위치 인덱스다. 현재 after 조회도 처음부터 읽으며
이번 변경에서는 저장 형식이나 Repository API를 바꾸지 않았다. docs/llm/engine-output.md 참고.

tests/llm/test_base_engine.py와 tests/llm/test_engine_outputs.py에 typed append/replace/빈 교체,
소유권 연결/독립 스냅샷, 내부 표시 범위, 잘못된 최종 결과/발행 순서와 정리, 실제 저장·중단·큐
계속 처리, 잘못된 Step 결과의 저장 전 거부를 보강했다. Python 3.12.14 단일 버전에서 검사한다.

최종 결과: **542 tests, OK, 130.177 seconds, skip 없음**.
로그는 tests/llm/reports/history/base-output-final-suite-python312.txt, 해시는 tests/llm/reports/history/base-output-final-snapshot.json이다.
Linux 스냅샷 /tmp/ish-output-wezwf16w에서 `.venv/bin/python -m unittest discover -s tests/llm -t . -v`를
실행했다. 검증한 `llm/`, `tests/llm/`, `examples/llm/`, `ish.platform/src/` 178개 파일은 현재 작업 폴더와 일치한다.
유료 모델 API는 호출하지 않았으며 실제 저장소/LangGraph/프로세스 및 결정적 모델 응답으로 검사했다.

## Previous: EngineDelta 명칭 통일

스트리밍 자료형을 EngineDelta로 통일하여 EngineEvent/EngineOutput과 같은 접두사를 사용한다.
공개 import, 타입 힌트, 생성/조회 경로, 검사와 문서를 함께 변경했다. 이전 이름의 별칭은
제공하지 않는다. EngineEvent.delta와 JSON 필드/저널 형식 및 실행 동작은 유지한다.

Linux Python 3.12.14 전체 검사: **534 tests, OK, 112.152 seconds, skip 없음**.
로그: tests/llm/reports/history/engine-delta-suite-python312.txt. 해시: tests/llm/reports/history/engine-delta-snapshot.json.
Linux 스냅샷 /tmp/ish-output-p_nzg6ib에서 `.venv/bin/python -m unittest discover -s tests/llm -t . -v`를
실행했고 검증한 178개 소스 파일이 작업 폴더와 일치함을 확인했다.

## Previous: 공통 Engine 출력 계약

core/results.py에 EngineDelta/EngineOutput을 추가했다. 텍스트 append/replace, 열린 JSON data와
metadata, output_id/step_id/visibility, Run 내 저장 sequence로 UI 출력 계약을 정의한다.
새 도메인이나 Manager는 만들지 않았다. Base/Loop/Graph/Agent/Pipeline/Tool이 공통 출력을
사용하며 최상위 결과는 ExecutionResult.output, 자식 결과는 Step.output으로 조회한다.

RunRepository는 Run/state/outputs.jsonl에 변경을 append/fsync한다. Run/Step JSON에는 확정
스냅샷을 저장한다. RunHandle.outputs/output_events 및 a 접두사 API는 같은 주입 저장소를
사용한다. 부분 출력·재시작·중단·구독 유실을 조회로 복구하며 실행을 다시 호출하지 않는다.
저널은 순차 읽기이며 위치 인덱스는 없다. memory 대화 선택과 별도로 Run/Step 출력은 영속화된다.

Loop 최종 답변은 마지막 LLM 응답이다. 중간 텍스트는 저널에 남고 대화 본문 교체도 새 JSONL
이벤트로 저장한다. Agent 내부 델타는 버리지 않고 internal로 분류한다. Graph 출력은 Workflow
outputs JSON이고 중첩 결과는 Step에 귀속한다. Pipeline은 stage별 kind=engine Step을 만들고
마지막 단계의 결과를 전달한다. 오류/중단/Run 완료 알림 계약은 계속 별도다.

BaseEngine 개발자는 문자열을 yield하거나 마지막 EngineOutput을 yield/return하면 된다.
직접 EngineEvent를 만들 때 TEXT_DELTA.delta / OUTPUT.output / STEP_COMPLETED.output을 쓴다.
event.text와 raw Step metadata.output/result에 대한 호환 별칭은 추가하지 않았다.
문서: docs/llm/engine-output.md, LargeLanguageModel 독스트링. 예제와 기존 검사를 새 계약으로 갱신했다.

tests/llm/test_engine_outputs.py는 JSON 왕복, 적용 순서/소유권, 저장 후 알림, 교체 델타, 부분 출력과
큐 보존, 재시작 재조회, 병렬 Agent, Pipeline 결과, 종료된 Step의 추가 출력 거부 및 실제
drop_oldest 구독 유실 후 복구를 검사한다. Linux Python 3.12.14 단일 버전으로 전체 회귀 검사한다.

최종 결과: **534 tests, OK, 96.922 seconds, skip 없음**.
로그는 tests/llm/reports/history/output-final-suite-python312.txt, 소스 해시는 tests/llm/reports/history/output-final-snapshot.json이다.
Linux 스냅샷 /tmp/ish-output-3fdp_fvt에서 `.venv/bin/python -m unittest discover -s tests/llm -t . -v`를
실행했다. `llm/`, `tests/llm/`, `examples/llm/`, `ish.platform/src/` 원본과 검증 스냅샷의 해시가 일치한다.
실제 저장소/LangGraph/프로세스 및 LiteLLM mock HTTP SSE를 검사했고 외부 유료 API는 호출하지 않았다.

## Previous: 중첩 GraphEngine / Workflow

내장 workflow 노드와 GraphEngine.for_agent를 구현했다. 직접 Workflow 참조는 부모 handlers를,
Graph Agent는 등록한 자식 엔진 handlers를 사용한다. 같은 Run에서 LangGraph 스케줄링을 유지하고
체크포인트 records/ToolPolicy/원장/CompletionPolicy을 공유한다. max_steps는 자식까지 합산하고
실제 작업만 조상 세마포어를 획득하므로 max_parallelism=1에서도 중첩이 교착되지 않는다.
max_nested_depth 기본 16, 범위 0~32이며 순환 참조/누락 정의/처리기를 실행 전 검증한다.
capability 탐색은 누락된 요청만 반복 구성한다. 자식 정의/revision/제한도 resume binding에 포함한다.

자식 입력/출력은 명시적 JSON Pointer/schema 계약으로 연결한다. subgraph Step 및
parent_step_id/node_checkpoint_key/node_path를 추가했고 별도 Run/Conversation은 생성하지 않는다.
Agent Graph 출력은 data다. Graph 조율 Agent의 모델/Skill/MCP 설정은 지원하지 않고 실행
노드에서 정의한다. Graph Agent 허용 목록은 Project Tool 선택이며 동적 MCP Tool을 이 경계
너머 허용하는 기능은 포함하지 않는다. 직접 Workflow 참조에서는 기존 MCP Agent 사용이 가능하다.

container=true인 순수 조율 노드는 미완료여도 자식 상태를 복구하고, 실제 started 작업은
retry_nodes 승인을 요구한다. 커스텀 graph_engine 처리기는 기본적으로 불확실한 작업으로 남는다.
resumable_container=True는 외부 효과 없이 자식만 조율하는 처리기에서만 선언한다.
Graph Agent의 Tool 시도/성공 횟수는 체크포인트에 저장/복원하고 승인 이후 효과 전에도 기록한다.
Agent 한도가 소진된 작업은 재시도 승인만으로 한도를 초기화하지 않는다.

통합 검사는 tests/llm/test_nested_graph.py다. 입력/출력, 3단계 중첩, 깊이/순환, 병렬 슬롯1,
중첩 loop pause/resume, Tool 권한/공유 예산, Graph Agent의 Skill을 쓰는 Loop 자식,
Agent 사용량 보존, timeout/interrupt와 큐 보존, 정의 변경, 실제 프로세스 os._exit 이후 복구를 검사한다.
사용법: docs/llm/nested-workflows.md. Linux Python 3.12.14 단일 버전에서 검증한다.

최종 결과: **524 tests, OK, 82.771 seconds, skip 없음**. 중첩 전용 통합 검사는 20개다.
`tests/llm/reports/history/nested-verified-suite-python312.txt`에 전체 로그를 저장했다. 실행 명령은 Linux 스냅샷 루트의
`.venv/bin/python -m unittest discover -s tests/llm -t . -v`다. 스냅샷 위치는
`/tmp/ish-nested-verified-zxo513z6`, 원본 소스 해시는 `tests/llm/reports/history/nested-verified-snapshot.json`에 기록했다.
검증한 `llm/`, `tests/llm/`, `examples/llm/`, `ish.platform/src/`와 작업 폴더의 해시가 일치한다.
실제 LangGraph/저장소와 프로세스 종료를 검사했으며 외부 유료 모델/MCP 서버는 호출하지 않았다.

## Previous: Agent 업무 정의와 실행 연결

AgentComponent를 engine/purpose가 필수인 재사용 업무 정의로 확장했다.
engine_options, completion, system_prompt, resources, tools, policy, 입출력 schema와
열린 확장 필드를 저장한다. 이전 최상위 loop/skills/rag/mcp 필드는 변환하지 않고 거부한다.
AgentNode는 engines 등록을 요구하며 순수 동기 for_agent(definition) 계약으로 실행기를
구성한다. LoopEngine은 서브클래스 메서드/provider를 보존해 호출별 복사본을 제공한다.
GraphEngine을 Agent 안에 중첩하여 별도 체크포인트 수명주기를 만드는 기능은 포함하지 않는다.

Skill 지침, Project RAG 검색 Tool, MCP 서버별 Tool 별칭을 연결한다. MCPComponent는
ToolRegistry를 제공하는 async context manager인 connector를 주입받으며 SDK별 전송 구현은
호스트가 제공한다. CRUD나 사전 검증에서는 접속하지 않는다. 연결/실행/취소 수명을 테스트했다.
GraphEngine의 additional_capabilities는 선택 Workflow가 요청한 리소스만 서비스에서 해결한다.
Agent policy는 timeout/max_tool_calls/require_tool을 제공하며 부모 ToolPolicy와 원장을 공유한다.
입출력 검증 실패는 자동 재실행하지 않고 Workflow 분기/수정 루프로 연결한다.

AgentData.snapshot/revise(expected_revision)로 UI 동시 편집을 검증한다. Agent Step에는
정의/버전/리소스/입출력을, 자식 Step에는 agent_step_id를 기록한다. 체크포인트는
Skill/MCP 정의와 어댑터 revision 변경도 검사한다. 엔진/호스트 코드 변경 시 revision을
올려야 한다. RAG corpus 전체를 버전 고정하는 기능은 포함하지 않는다.

사용법: docs/llm/agents.md, docs/llm/graph-engine.md, LargeLanguageModel 독스트링.
검사는 WSL Linux의 원본 해시 스냅샷과 Python 3.12.14 단일 버전으로 수행한다.
LLM 스트림/MCP 연결 어댑터는 결정적 테스트 더블이고 RAG 저장·검색 및 Run/Step 저장은
실제 구현을 사용한다. 외부 유료 API나 실제 MCP 서버 접속은 수행하지 않는다.

최종 검증: **504 tests, OK, 74.162 seconds, skip 없음**.
Linux snapshot: `/tmp/ish-agent-final-2ggyj7qx`.
`tests/llm/reports/history/agent-final-snapshot.json`에 위치와 해시를 기록했다. 스냅샷과 작업 폴더의
`llm/`, `tests/llm/`, `examples/llm/`, `ish.platform/src/` 파일 해시가 일치함을 확인했다.
로그: `tests/llm/reports/history/agent-final-suite-python312.txt`. 실행 명령(스냅샷 루트):
`.venv/bin/python -m unittest discover -s tests/llm -t . -v`.

## Previous: Linux 전용 실행 기반

Windows 실행 지원을 제거했다. 공통 require_linux는 Facade/CLI/잠금/프로세스 실행
진입점에서 부작용 전에 거부한다. PLUGIN_META 정적 분석과 모듈 import는 유지한다.
Windows Job/launcher/taskkill/msvcrt/PowerShell/junction 코드와 전용 검사를 제거했고,
기본 프로세스 Tool과 ProcessToolRunner는 Linux 그룹 종료 함수를 공유한다.
fcntl.flock, 디렉토리 fsync, /bin/sh, Bubblewrap과 부모 사망 게이트를 사용한다.
max_processes는 Linux에서 강제되지 않던 인자이므로 제거했다. memory_bytes는 기존처럼
프로세스별 RLIMIT_AS이며 cgroup 전체 한도를 의미하지 않는다. Python 버전 어댑터는 유지한다.

Linux에서 유효한 CON/콜론/끝 공백 등의 파일명은 더 이상 Windows 규칙으로 차단하지 않는다.
컴포넌트 식별자 문법과 내부 예약 경로, 상위 경로 탈출/심볼릭 링크 차단은 유지한다.
junction 검사는 실제 Linux 심볼릭 링크 검사로 대체했다. Bubblewrap 검사는 skip 없이
로컬 서버와 외부 파일에 대해 비격리 접근/격리 차단을 비교한다.

검증 환경: WSL Ubuntu, Python 3.12.14, Bubblewrap. `.venv-linux312`는 이 작업에서 생성한
Linux 전용 가상환경이다. 전체 검사는 이 환경을 사용한다. 이전 Windows 검사 결과는
아래 역사적 기록이며 현재 지원 OS에 대한 검증으로 간주하지 않는다.

검증 결과: WSL Ubuntu의 Linux 파일시스템(`/tmp/ish-llm-linux-validation-61yoy0za`)에
동일 소스/동일 의존성 버전을 배치하여 Python 3.12.14로 전체 **492 tests, OK,
102.877 seconds, skip 없음**을 확인했다. `tests/llm/reports/history/linux-validation-snapshot.json`은 원본 파일
해시와 스냅샷 위치를 기록하며 최종 작업 폴더와 비교한다. 전체 로그는
`tests/llm/reports/history/linux-native-suite-python312.txt`다. 실행 명령은 스냅샷 루트에서
`.venv/bin/python -m unittest discover -s tests/llm -t . -v`다.

Windows 마운트(`/mnt/d`) 첫 검사에서는 SDK 로딩 11.5초 및 이벤트 루프 지연으로 시간
기반 Graph 검사에 실패했다. 그 전체 실행은 중단하고 위 Linux 파일시스템에서 재검증했다.
한 중복 실행 검사에서는 첫 SDK import를 준비 단계로 옮겨 실행 대기 10초와 분리했다.
제품 실행 제한은 완화하지 않았다. 출력 파이프가 찬 상태에서 프로세스 생성과 취소가
겹치는 회귀 검사도 추가했다. 해당 최종 검사는 전체 492개에 포함된다.
관련 최종 검사 36개도 별도로 통과했다(`tests/llm/reports/history/linux-final-focused-python312.txt`).
Linux 소스 컴파일, uv pip check, 최종 wheel 빌드, git diff --check도 통과했다.
검증 스냅샷의 176개 파일 해시는 최종 원본과 일치했다. 이전 build/lib에 남아 있던
삭제된 과거 모듈이 wheel에 섞여 있어 추적되지 않는 build 생성물만 정리한 뒤 재빌드했고,
최종 wheel의 Python 모듈은 현재 원본과 바이트 단위로 비교했다.
대화/프로젝트 데이터의 자동 이전이나 실제 모델/외부 업무 API 호출은 수행하지 않았다.

## Goal

Continue development of the `llm` AI execution plugin for the `ish.platform`
Linux shell host, built with Python, asyncio and LiteLLM. The host owns its UI.

Read `AGENTS.md` and `docs/llm/architecture.md` before making architectural changes.

## Latest: Tool 프로세스 격리와 영속 작업 키

ToolPolicy.runner에 주입하는 ProcessToolRunner를 추가했다. Tool별로 등록한 절대 argv만
선택하고 ToolCall JSON을 stdin으로 전달한다. JSON 결과만 받으며 출력/시간/동시 실행에
한도를 적용한다. 기본 sandbox는 Linux Bubblewrap을 요구하며 사용 불가 시 실행하지 않는다.
쓰기 작업 디렉토리와 읽기 전용 의존성을 지정하고 네트워크는 기본 차단한다. 개발자만
allow_network=True로 호스트 네트워크 공유를 선택한다. Windows process 모드는 파일/네트워크
sandbox가 아니며, 기본 Python -I -S 게이트를 Job에 연결한 뒤 명령을 전달한다. venv launcher를
게이트로 쓰면 Job 연결 전 자식 생성 경쟁이 있으므로 거부한다. 명령의 venv 실행은 가능하다.
Job 종료/부모 종료와 POSIX 그룹/부모 사망 신호를 연결하고 출력 폭주 시에도 파이프를 정리한다.

ToolPolicy.operation_key는 호스트가 선택한 Task 범위 업무 키다. RunRepository에 연결한
OperationRepository가 Task/state/tool_operations/<sha256>.json에 started/완료 결과를 저장한다.
ToolOperations는 기존 StorageIO/소유권 잠금을 사용한다. 완료된 같은 요청은 결과를 재사용하고,
동일 키의 다른 인자는 operation_conflict, 미확정 started는 operation_uncertain으로 거부한다.
runner에는 안정적인 idempotency_key를 전달한다. Task.run.operation/aoperation으로 조회하고
reconcile_operation/areconcile_operation은 비활성 Task에서 외부 결과/근거를 명시해 확정한다.
자동 reset/재실행은 없으며 제공자 exactly-once를 라이브러리만으로 보장하지 않는다.

기본 Tool/내장 shell/Python 핸들러를 자동 격리하지 않는다. 명시적 runner 연결과 명령 등록이
필요하다. 기존 LiteLLM 스트림 스레드는 이번 프로세스 실행기에 포함하지 않는다.
설정·worker 예제·OS별 보장·복구 API: docs/llm/process-isolation.md, LargeLanguageModel 독스트링.

Python 3.12.14 단일 버전 검증:

```text
.venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
491 tests, OK (skipped=1), 519.215 seconds
log: tests/llm/reports/history/isolation-full-suite-python312.txt

# 최종 경로/디렉토리 동기화 보강 및 네트워크 공유 설정 검사 포함
.venv312/Scripts/python.exe -m unittest tests.llm.test_isolated_tools -q
17 tests, OK (skipped=1), 25.100 seconds
log: tests/llm/reports/history/isolation-final-focused-python312.txt
```

첫 전체 검사 수집 뒤 네트워크 공유 설정 회귀 1개를 추가했다. 위 최종 17개에 포함한다.
Windows 실제 프로세스/손자 종료, 부모 os._exit, Job 연결 실패, JSON/환경 격리, 출력 폭주,
외부 효과 직후 os._exit(23)와 재시작 중복 차단, Graph 병렬 같은 키 충돌을 검증했다.
Linux Bubblewrap 실환경 검사는 Windows에서 skip했으며 배포 전 Linux 검증이 필요하다.
외부 유료 모델/실제 외부 업무 API는 호출하지 않았다.

## Previous: 실행 정책, 대기열 UX와 조회 성능

ServiceConfig에 RunLimits, ToolPolicy, CompletionPolicy, conversation_cache_size를 추가했다.
RunLimits는 Task별 QUEUED admission(재개 포함)과 전체 Engine 실행 deadline을 제어한다.
RequestHandle.cancel은 실행 전 요청만 영속 취소하며 wait는 request_cancelled를 보고한다.
Runs.status/astatus는 과거 Run/Step 목록 없이 현재 Run과 제한된 대기 요청 ID를 조회한다.

ToolExecutor는 context.tool_scope를 통해 허용 목록/Run 호출 한도/async 승인/runner를 적용한다.
Loop, Graph ToolNode, Agent가 같은 Run 예산을 공유한다. STEP_UPDATED로 승인 결과를
기록한 뒤에만 실행하며 저장 실패/승인 거절/중단에서는 부작용을 시작하지 않는다.
scope와 콜백은 영속 모델에 들어가지 않는다. 임의 개발자 코드의 직접 I/O는 가로채지 않는다.

CompletionPolicy은 모델별 counter를 주입받아 전체 요청(도구 정의 포함)을 확인한다.
Loop는 각 호출 직전 토큰 계산을 스레드로 넘긴다. 과거 턴을 이진 탐색으로 선택하고 현재
사용자/Tool 문맥은 함께 보존한다. 자체 문맥이 너무 크면 context_budget_exceeded다.
원본 대화는 유지한다. 자동 요약, Loop 재개, 비용 상한을 추가한 것은 아니다.

EventSubscriptions는 선택적인 queued/drop_oldest와 callback_timeout, Subscription.stats를
제공한다. 관찰 알림을 놓친 UI는 저장소에서 재조회한다. Run 종료 알림도 누락 가능하므로
request.wait/조회로 확정한다. 승인/검증은 ToolPolicy/EventHandlers를 사용한다.
정체된 동기 콜백 스레드를 강제로 종료할 수는 없지만 해당 구독은 더 실행하지 않는다.

파일 대화 projection을 TaskManager 공통 팩토리의 LRU(기본 32개, 0으로 비활성화)로
공유하여 UI 조회마다 JSONL을 처음부터 재생하지 않는다. 이벤트 상태 집계는 증분 유지하고
제한된 오름차순 조회는 전체 참조 목록 복사를 생략한다. fsync/저장 후 통지는 유지한다.
메모리 대화와 사용자 주입 팩토리 수명은 바꾸지 않는다.

사용 예/한계는 docs/llm/runtime-reliability.md 및 LargeLanguageModel 독스트링에 있다.
운영 한도는 기본 None이므로 애플리케이션에서 명시적으로 설정한다. 일반 실행은 OS sandbox나
exactly-once 부작용을 보장하지 않는다. 실제 모델 비용을 발생시키는 호출은 검증에 사용하지 않았다.
새 tests/llm/test_runtime_reliability.py는 21개 테스트를 포함한다.

검증 환경은 Python 3.12.14 단일 버전이다.

```text
.venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
474 tests PASS, 366.671 seconds
log: tests/llm/reports/history/reliability-final-suite-python312.txt

# 마지막 구독 timeout 분류 교정과 추가 회귀 검사까지 포함한 최종 관련 검사
.venv312/Scripts/python.exe -m unittest tests.llm.test_runtime_reliability tests.llm.test_service_extensions -q
40 tests PASS, 26.796 seconds
log: tests/llm/reports/history/reliability-final-focused-python312.txt
```

전체 검사 수집 후 추가한 구독 자체 TimeoutError 회귀 1개는 위 40개에 포함한다.
compileall, 수정 Python 파일 구문/공백 검사, git diff --check, 오프라인 wheel 빌드도 통과했다.
빌드 로그: tests/llm/reports/history/reliability-build-python312.txt. 유료 API 키/모델 호출은 사용하지 않았다.

## Previous: Graph 노드 체크포인트와 명시적 재개

GraphEngine이 노드 실행 전 started/검증 후 completed, pause_before 경계에서 waiting을
EngineEvent로 전달한다. RunRepository/CheckpointRepository가 Run/state/checkpoints/graph에
초기 헤더와 변경된 노드만 원자적으로 저장한다. LangGraph 스케줄러는 유지하며 내부
checkpointer/interrupt()를 직접 사용하지 않는다. 재개 때 완료된 노드는 출력만 복원한다.
중첩/병렬/반복 키를 JSON 배열로 구분해 표시용 path 충돌을 피한다.

task.run.resume(run_id, engine=..., retry_nodes=...)은 원본과 연결된 새 QUEUED 요청/Run이다.
한 원본에서 중복 재개를 막고 admission/실행 시 설정과 체크포인트 digest를 검사한다.
PAUSED Run/Assistant 및 RunEventType.PAUSED를 추가했다. 일시정지는 Task 큐를 막지 않는다.
RunHandle.checkpoint/acheckpoint로 UI가 조회한다. 잘못된 재개는 resume_rejected로 거부한다.
재개 시도도 실패하면 그 최신 Run의 상속 체크포인트로 다음 시도를 만든다.
초기 복사 전 종료된 재개 시도는 원본 계보/digest를 검증해 복원한다. 초기화 완료 표시가
있는 체크포인트 누락은 손상으로 거부한다. resume/checkpoints/completions 메타데이터는
서비스 관리 필드로 사용자 이벤트 처리기의 갱신에서 보호한다.

대화는 본문 복제 없이 ID로 참조한다. file 저장에서 원래 문맥을 복원하고, memory가
소실된 뒤에는 재개를 거부한다. 노드 입력/출력에 사용된 값의 영속화는 원래 Step 계약과 같다.
Engine revision은 개발자가 처리기 코드 변경 시 갱신한다. 외부 파일/RAG 세대는 고정하지 않는다.

started 처리 노드는 실제 부작용이 발생했을 수 있어 정확한 키를 retry_nodes로 승인해야 한다.
Agent 내부 Tool 재개, 임의 상태 수정/승인 값 주입, 네이티브 interrupt 재개는 아직 없다.
Tool idempotency, UI Workflow 편집 충돌 검출, 보관/대규모 부하 검증이 후속 과제다.
UI 정의 저장 예제와 JSON 디렉토리는 docs/llm/graph-checkpoints.md에 설명했다.

체크포인트 전용 19개 테스트(실제 프로세스 강제 종료 포함)를 추가했다. 기존 Graph/Agent를
포함한 집중 검사 53개, 최종 체크포인트/서비스 확장 집중 검사 38개가 통과했다.
최종 검증: Python 3.12.14 전체 454개 테스트 통과(347.012초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/graph-checkpoint-final-suite-python312.txt. 외부 모델 API는 호출하지 않았다.
compileall, git diff --check, 오프라인 wheel 빌드와 최종 checkpoint/resume 코드의 wheel
포함 여부도 확인했다. 빌드 로그: tests/llm/reports/history/graph-checkpoint-build-python312.txt.

## Previous: Agent 통일 및 과거 구현 호환 계층 제거

AgentComponent/agents로 통일하고 subagents 패키지를 삭제했다. 테스트와 공개 예제도
purpose/completion.model 및 WorkflowGraph 버전 1을 사용한다. 일반 Component와
ProjectConfig의 열린 JSON 키는 유지하며 유효한 Agent/Workflow의 확장 필드도 보존한다.

제거한 실행/저장 호환 경로:
* ProjectConfig의 평면 model/temperature/api_base → completion 자동 변환.
* Task default_engine 필드 제거 후 읽기, Project components/conversation_storage 누락 보정.
* 버전 없는 Workflow CRUD/복제와 component.json 없는 디렉토리의 빈 설정 반환.
* 분리형 GraphRAG 이전 함수 및 벡터 전용 RAG 세대 읽기. 현재 세대는 graph 데이터와 DB가 필수다.
* Tool 전용 resolver 우회와 ProjectManager initializers 인자. resolve(project, names)와
  선택된 Component.initialize를 사용한다. Task 초기화는 ProjectManager가 계속 담당한다.

Python 버전 지원, SDK 응답의 dict/object 처리, 일반 capability 확장, 런타임 Tool 카탈로그와
프로젝트 정의 재정의는 현재 기능이므로 유지했다. 사용자 프로젝트 파일은 변경하지 않았다.
구형 데이터 자동 이전/삭제는 하지 않으며 잘못된 형식은 명시적으로 거부한다.
아래 Previous 절은 과거 개발 이력이며 현행 호환성 계약은 이 절과 architecture.md를 따른다.

검증: Python 3.12.14 전체 435개 테스트 통과(336.961초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/implementation-cleanup-final-python312.txt.
첫 전체 실행에서 구형 메타데이터 보정에 의존하던 테스트 두 개를 발견해 현재 계약으로
수정했다. 메모리 모드의 기존 파일 보존과 누락된 Project 필드 거부를 각각 검증한다.
compileall, git diff --check와 오프라인 wheel 빌드도 통과했다. wheel의 agents 포함 및
subagents/이전 RAG migration 모듈 제외를 확인했다. 외부 모델 API는 호출하지 않았다.
빌드 로그: tests/llm/reports/history/implementation-cleanup-build-python312.txt.

## Previous: GraphEngine 파일 통합

LangGraph 컴파일·실행 객체와 상태 타입을 engines/graph.py로 합치고 별도 실행 모듈을
제거했다. Run별 내부 실행 객체는 _WorkflowRuntime으로 유지하며 공개 GraphEngine과
실행 상태의 책임은 분리한다. LangGraph는 첫 execute에서 루프 밖에서 미리 로딩하고,
내부 메서드의 로컬 import와 타입 검사 전용 import로 플러그인 지연 로딩을 유지한다.
공개 API, Workflow JSON, 이벤트 ACK, 실행 제한·취소·영속화 동작은 바꾸지 않았다.

검증: Python 3.12.14 전체 434개 테스트 통과(411.411초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/graph-merge-suite-python312.txt. compileall과 오프라인 wheel 빌드도 통과했으며,
wheel에는 통합 graph.py만 있고 제거한 모듈은 포함되지 않는 것을 확인했다.
빌드 로그: tests/llm/reports/history/graph-merge-build-python312.txt.

## Previous: GraphEngine의 LangGraph + LiteLLM 실행 백엔드

GraphEngine의 수동 _walk 실행기를 제거하고 내부 실행 객체에서 Workflow v1을
LangGraph StateGraph로 컴파일한다. 노드/조건부 간선/병렬 하위 그래프/반복 후방 간선을
실제 LangGraph 스케줄러가 실행한다. 공개 GraphEngine/GraphNodeContext 및 Workflow JSON,
AgentNode → LoopEngine → LiteLLM 호출, Run/Step/Conversation 저장 책임은 유지했다.
상태 바인딩과 사전 검증은 기존 경로다. root graph Step에는 runtime="langgraph"를 추가했다.

Run별 실행 객체에 예산/세마포어/이벤트 연결을 두고, 병렬 경로의 임시 상태는 ContextVar로
격리한다. 반복 시 동일한 컴파일 그래프를 사용해도 임시 상태는 새로 만든다. Step 시작이
저장된 뒤 핸들러를 시작하는 ACK 순서를 유지한다. LangGraph의 NodeCancelledError는
기존 INTERRUPTED로 연결하고, 중첩 병렬의 중복 취소가 처리기의 비동기 finally 정리를
중단하지 않도록 처리기를 shield하고 취소를 한 번만 전달해 완료까지 기다린다.

자동 재시도/checkpointer는 사용하지 않으며 stale Run 재실행 규칙은 바꾸지 않았다.
네이티브 interrupt()는 아직 Run 일시정지/재개 계약이 없으므로 명시적 오류로 거부한다.
checkpoint 저장·Run 상태·Tool 부작용 재실행을 포함하는 명시적 재개는 후속 과제다.

LangGraph를 pyproject.toml/PLUGIN_META 기본 의존성에 추가했다. 검증 대상은
Python 3.12.14 + LangGraph 1.2.12다. 첫 Graph 실행에서
지연 import하므로 Loop 전용 사용과 플러그인 import는 LangGraph를 로딩하지 않는다.
langchain-litellm은 추가하지 않았다. 사용법/설치/제약은 docs/llm/graph-engine.md에 정리했다.

tests/llm/test_langgraph_engine.py에 12개 회귀 검사를 추가했다. 네이티브 노드 실행/예약 ID,
길이가 다른 경로와 중첩 병렬, 반복 시 임시 상태 격리, 30회 반복/공유 예산, 재시도 방지,
자발적 취소/명시적 중단/timeout의 비동기 정리, 동시 Task 및 지연 import를 확인한다.
CLI 코드 작성 예제는 문법 오류→테스트 실패→수정 성공을 거쳐 3회차에 completed가 되었고
실제 subprocess 테스트 5개가 통과했다. 로그: tests/llm/reports/history/langgraph-demo-python312.txt.
모델 응답은 fixture이며 외부 LLM API는 호출하지 않았다.

compileall, uv pip check, 오프라인 wheel 빌드 및 새 실행 모듈/의존성의 wheel 포함을 확인했다.
wheel 로그: tests/llm/reports/history/langgraph-build-python312.txt.

전체 검증: Python 3.12.14, 434개 테스트 모두 통과(304.524초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/langgraph-suite-python312.txt. 실제 Chroma/Kuzu 검색→Agent/Tool→코드 작성→정적/테스트
실패→재수정→Run/Step 기록과 재열기까지 기존 통합 검사를 포함한다.

## Previous: 범용 Agent와 Workflow 입출력·검증·재수정 통합

engines/agent.py의 AgentNode를 추가했다. 기존 구현은 examples/llm/code_workflow.py의
예제 전용 CodeAgent였으며 범용 Tool 정책 적용 노드는 아니었다. 새 처리기는 저장된
Agent의 모델/프롬프트/허용 Tool/Loop 제한을 적용해 기존 LoopEngine을 같은 Run에서
소비한다. 입력/Tool/임시 상태는 노드별로 분리하고 마지막 모델 턴을 text/JSON data로
반환한다. 하위 Step과 Completion 관찰은 원래 Run에 남는다. 내부 텍스트 출력은 opt-in이다.

Workflow/처리 노드의 inputs/outputs JSON Pointer 매핑과 input_schema/output_schema를
components/workflows/bindings.py에서 공통 검증한다. GraphEngine이 실행 시 매핑/검증하고
출력 계약이 통과한 후에만 상태를 변경한다. 루프 본문은 상위 상태를 공유한다. 기존
매핑 없는 처리기는 전체 state와 얕은 결과 병합을 유지한다. ToolNode도 매핑된 인자를 받는다.
예제의 별도 모델 호출 구현을 제거하고 AgentNode + 예제 파일 저장/검증 처리기로 분리했다.
그래프 조건과 매핑은 동일한 JSON Pointer 해석기를 사용하고 GraphEngine 공개 실행 API를
private 구현 뒤로 정리했다. Agent/Workflow 정의 저장과 Run/Step 수명 책임은 바뀌지 않았다.

신규 tests/llm/test_agent_workflow.py는 16개 통합/회귀 검사를 포함한다. 실제 Chroma/Kuzu 검색과
근거 관계를 Agent Tool 응답으로 전달하고 문법 오류→테스트 실패→수정 성공을 실제 파일,
AST/compile, Python subprocess unittest로 확인한다. 정책/Tool 제한/입출력 오류/병렬 분리/
취소 후 대기 요청/재열기와 이력 조회도 검사한다. 모델 completion/embedding/triple 응답만
고정 fixture다. 외부 모델 API는 호출하지 않았다. Skill/MCP/RAG 리소스 참조 자동 적용은
지원하지 않아 AgentNode에서 명시적으로 거부하며 RAG 검색은 허용 Tool로 연결한다.
사용법/계약: docs/llm/graph-engine.md. 직접 실행 예제: examples.llm.code_workflow.

검증: Python 3.12.14, 신규 16개 및 Graph/기본 Tool 회귀 33개, 전체 422개 통과(264.363초).
전체 명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
전체 로그: tests/llm/reports/history/workflow-integration-suite-python312.txt.
CLI 예제는 3회차에 실제 테스트 5개가 통과하고 completed로 끝났다.
로그: tests/llm/reports/history/agent-workflow-integration-python312.txt, tests/llm/reports/history/workflow-agent-demo-python312.txt.

## Previous: 조회·RAG 중복 작업 최적화

RequestHandle의 Run 연결 조회를 _load_run 한 트랜잭션으로 모아 result 조회 시 같은
Run을 두 번 읽던 경로를 줄였다. RunHandle.response는 Task를 한 번 읽어 재사용한다.
Run/Task/대화 저장소 주입과 수명·소유권 검사는 유지하며 조회 사이에 캐시하지 않는다.
Query는 오름차순 페이지까지만 입력을 소비한다. 커서/상태/offset/limit/역순 동작과
큰 정수 범위를 유지한다. RunResultQuery는 선택된 Run만 ExecutionResult로 변환하고,
사용자 Query 하위 클래스의 apply에는 기존 ExecutionResult 입력을 보장한다.
기본 파일 저장소는 정렬을 위해 여전히 전체 메타데이터를 읽는다.

RAGComponent.corpus_version으로 작은 identity/generation 포인터를 읽고 불변 snapshot을
재사용한다. 검색당 corpus.json 읽기는 한 번이며, rerank 이후 유효성 확인을 위한
중복 검색을 제거했다. publish의 변경 검사도 작은 포인터를 사용한다.
compact의 삭제 전 활성 코퍼스 읽기 검증은 유지한다. 관계 근거
문단은 문서별 ID 맵으로 조회하여 관계마다 문단 전체를 순회하지 않는다.
기존 CRUD/Tool API와 원자적 세대 공개/충돌/삭제/종료 검사는 유지한다. 신규 회귀 테스트는
실제 호출 횟수, Query 결과 동등성, 최신 상태 재조회, rerank 중 세대/선택 변경을 확인한다.
compact에서 활성 코퍼스를 읽을 수 없을 때 기존 세대를 삭제하지 않는 회귀 검사도 포함한다.
검증: Python 3.12.14, 전체 406개 통과(232.428초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/query-optimization-suite-python312.txt. 외부 모델 API 호출 없이 검증했다.

## Previous: 호환용 이름 별칭 제거

services/_compat의 13개 평면 모듈 별칭과 services.__path__ 확장을 제거했다.
import/monkeypatch는 lifecycle/runtime/history/infrastructure의 정식 경로를 쓴다.
RunManager의 runs= 인자와 .runs 속성을 제거하고 repository=/.repository로 통일했다.
ComponentData의 register/aregister 동의어는 제거하고 create/acreate를 사용한다.
Registry.register는 실제 런타임 등록 API로 유지한다. examples/markdown_rag.py의
호환 진입점도 제거했으며 examples.llm.rag_components가 현재 실행 경로다.
호출부·예제·문서·테스트를 함께 수정했다. 옛 import/속성/생성자 인자가 노출되지 않는지,
정식 패치 대상/독립 import 순서와 ComponentData 생성 API를 검증한다.
정상 패키지 재수출, 비동기 I/O API, 데이터 스키마 로딩과
명시적인 RAG 이전 기능은 이름 별칭이 아니므로 유지한다. 기존 저장 데이터는 수정하지 않았다.
검증: Python 3.12.14, 관련 50개 및 전체 396개 통과(227.547초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/alias-removal-suite-python312.txt. ish 실제 로더/worker import도 포함한다.

## Previous: rag 하나로 문서·관계 통합

공개 RAGComponent/선택 이름/디렉토리는 rag 하나다. components/graphrag를 제거하고
extraction, graph_indexing, graph_search를 components/rag로 옮겼다. 기본 등록 목록과
Agent 참조 검증/예제/공개 API 독스트링을 갱신했다. 문서 등록·수정에는 embedding과
extractor가 필요하며 벡터/트리플 준비 뒤 Chroma/Kuzu를 같은 세대로 공개한다.
asearch는 documents/entities/relations/sources를 함께 반환한다. 문서 목록만 필요하면
asearch_documents를 쓴다. 실제 검색된 문단의 출처 ID로 관계를 시작하고 깊이/개수를
제한하여 나가는 관계를 확장한다. 검색 시 별도 SLM 전처리는 요구하지 않는다.
rag 선택 시 rag_search 하나를 자동 제공하며 LoopEngine의 결과 전달·Step 기록을 유지한다.
기존 matches 응답 키는 documents로 바뀌었다. agraph_search는 정확한 이름용 보조 API다.

기존 graphrag 프로젝트는 migrate_legacy_graphrag(ProjectManager, id)로 명시적 병합한다.
모델 없이 저장 벡터/관계로 재구축하고 성공 뒤 선택을 바꾸며 원본 폴더는 보존한다.
ID 충돌/다른 임베딩 profile은 거부한다. 실패 후 재호출 가능하며 Agent/Workflow 문자열은
자동 변경하지 않는다. 과거 벡터 전용 rag는 읽을 수 있고 명시적 update로 관계를 생성한다.
문서: docs/llm/rag-components.md. 실제 DB·고정 모델 테스트로 통합 검색/순환/한도/출처/이전/
충돌/실패 후 재시도/원본 보존을 검사한다. 외부 모델 API는 호출하지 않았다.
검증: Python 3.12.14, 전체 395개 통과(233.377초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/unified-rag-suite-python312.txt.

## Previous: 컴포넌트 선택에 따른 검색 Tool 자동 제공

Project에서 rag/graphrag를 선택하면 검색 Tool을 자동 제공한다. RAGData의 활성화 메서드와
RAGComponent의 별도 설정·검증·분기를 제거했다. 과거 search_tools_enabled 값은 열린 데이터로
남아 있어도 동작에 영향을 주지 않는다. 신규 설정에는 이 필드를 만들지 않는다.
백엔드에 등록만 한 컴포넌트는 제공하지 않으며 Run마다 최신 프로젝트 선택을 적용한다.
이미 시작한 Run의 목록은 유지하지만 호출 시 프로젝트·컴포넌트 수명 검사는 그대로 수행한다.
예제와 공개 API 설명에서 추가 활성화 단계를 삭제했다. 검색·결과 전달·Step 기록 테스트도
별도 활성화 없이 실행하며 선택 변경/구버전 false 설정/재열기를 검증한다.
검증: Python 3.12.14, 관련 48개 및 전체 389개 통과(220.205초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/rag-auto-tools-suite-python312.txt. 실모델 API 호출 없이 검증했다.

## Previous: RAG 예제 통합과 외부 검색 이름 분리

examples/markdown_rag.py의 DemoRAGComponent/LocalIndexes/분할/추출 검증 중복 구현을
제거하고 examples/llm/rag_components.py 공개 API 예제로 연결했다. 현재 예제는 문서 CRUD/
직접 검색 및 --ask를 통한 내장 Tool/Loop 실행을 제공한다. 이전 모듈명만 유지하며
--output/--verify와 과거 독립 색인 형식은 지원하지 않는다. 과거 결과 파일은 변경하지 않았다.
BuiltinTools 외부 어댑터는 external_rag_search/external_graphrag_search로 이름을 변경했다.
기존 어댑터 키 사용 시 변경 안내 오류를 반환한다. 활성화 이름·저장 정의·Workflow 참조도
호출자가 변경해야 하며 사용자 프로젝트 데이터를 자동으로 수정하지 않는다.
내장 컴포넌트 Tool 이름·설정 기본값은 그대로다. 컴포넌트 선택 시 자동 노출 변경은 포함하지 않았다.
테스트는 공개 API 예제 실행/재열기/CRUD와 내장·외부 Tool 동시 노출을 검증한다.
사용법: docs/llm/markdown-rag-test.md, docs/llm/builtin-tools.md.
검증: Python 3.12.14, 관련 33개 및 전체 388개 통과(223.760초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/rag-cleanup-suite-python312.txt. 외부 모델 API는 호출하지 않았다.

## Previous: 프로젝트 검색 Tool과 Loop 연결

RAG/GraphRAG의 aenable_search_tools()로 현재 프로젝트의 검색 Tool을 노출한다.
별도 ToolComponent/호스트 어댑터 없이 tools capability로 합쳐지며 기본값은 비활성이다.
rag_search, graphrag_documents_search와 seed 기반 graphrag_search를 제공한다.
ComponentRegistry의 선택적 resolve_runtime/data_factory 계약으로 RunManager의 기존
ProjectAccess/StorageIO/수명 검사를 공유한다. Base/Registry에는 검색/Tool 실행 코드를 넣지 않았다.
검색 호출은 ToolExecutor → EngineEvent → StepManager 경로를 유지한다.
문서 결과는 출처/revision을, 관계 결과는 인용과 버전이 있는 sources를 포함한다.
사용법/설정/제약: docs/llm/rag-components.md. 통합 테스트: tests/llm/test_search_tools.py.
실제 DB와 결정적 모델 스트림으로 검증하며 실모델 호출은 하지 않는다.
검증: Python 3.12.14에서 새 통합 테스트 9개, 전체 383개 통과(214.392초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
전체 로그: tests/llm/reports/history/search-tools-suite-python312.txt.

## Previous: 프로젝트 설정 조회와 기본 프로젝트

project.configuration/aconfiguration은 저장된 Project/선택된 Component 설정을 한 번의
잠금 범위에서 조회한다. JSON 저장 원본과 Component 검증/소유권은 유지한다. YAML codec,
자동 UI 폼, 임의 component.json에서 모델 클라이언트 자동 조립은 추가하지 않았다.

backend.projects.get_default/aget_default는 첫 호출에 loop 기본 선택·전체 등록 Component·
file 대화 저장 프로젝트를 만들고 이후 재사용한다. 생성자에서 자동 생성하지 않는다.
사용자가 수정한 기존 설정은 유지하며 submit의 engine 인자는 계속 필수다.
기본 참조의 ID 예약/pending으로 중복 생성을 방지하고, 소프트 삭제는 자동 복원하지 않는다.
영구 삭제 뒤에는 새 ID, 복제에는 기본 참조를 복사하지 않는다.
설계 검토·API: docs/llm/project-settings.md. 새 테스트: tests/llm/test_default_project.py.
Python 3.12.14에서 새 테스트 10개와 전체 374개가 통과했다(207.611초).
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
전체 로그: tests/llm/reports/history/default-project-suite-python312.txt.

## Previous: inference 호환 패키지 제거

llm/inference의 네 파일은 재수출 별칭뿐이었고 제품 코드에서 사용하지 않았다.
남아 있던 markdown_rag 예제와 모델 테스트를 llm.components.rag import로 통일한 뒤
호환 패키지를 제거했다. 모델 테스트 파일은 tests/llm/test_rag_models.py로 옮겼다.
외부 코드가 이전 import를 사용한다면 llm.components.rag로 변경해야 한다.
모델 호출 구현이나 RAG/GraphRAG 동작, 도메인 경계는 바꾸지 않았다.
Python 3.12.14 전체 364개 테스트가 208.102초에 통과했다.
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
로그: tests/llm/reports/history/inference-cleanup-python312.txt.

## Previous: RAG / GraphRAG Component 공개 API

모델 호출·분할·색인·검색 구현을 components/rag와 components/graphrag로 모았다.
llm/inference는 같은 클래스의 호환 import만 유지한다. RAGComponent를 기본 등록에 추가하고
RAGData/GraphRAGData로 aadd/aupdate/adelete_document, aget/alist_documents, asearch,
agraph_search를 제공한다. GraphRAG는 RAG 파이프라인을 상속해 한 문서 집합으로 일반 검색과
관계 검색을 제공한다. 정의 records CRUD는 기존 의미 그대로 유지한다.

모델 준비는 잠금 밖, DB 세대 공개/검색은 공유 StorageIO 및 workspace 잠금 아래에서 수행한다.
새 DB를 완성·닫은 후 active.json을 원자적으로 교체한다. 동시 변경은 RAGConflictError다.
삭제·복제는 저장된 벡터/관계를 사용하며 모델 호출을 하지 않는다. 실패·취소·재열기·근거 보존을
실제 로컬 DB와 결정적 모델 응답으로 검사한다. 이번 변경에서는 실모델 API를 재호출하지 않았다.
기존 2026-09-25 실모델 검증은 예제 어댑터에 대한 결과다.

제약: DB 전체 재구축 및 직렬화된 저장 작업, 영속 job/진행률 없음, ATX Markdown 분할,
이름 기반 엔티티 통합/유한 방향 그래프 검색. 대규모 증분 색인·전역 그래프 요약은 미구현.
API와 수명 규칙: docs/llm/rag-components.md. 공개 API 예제: examples/llm/rag_components.py.
검증: tests/llm/test_rag_components.py의 새 테스트 19개. 최종 Python 3.12.14 전체 364개가
194.385초에 모두 통과했다. 로그: tests/llm/reports/history/rag-components-final-python312.txt.
첫 전체 실행 363개 통과 뒤 공개 포인터 교체 직후 fsync 오류에 대한 테스트를 추가했다.
공개를 시도한 세대는 오류 시에도 삭제하지 않아 active가 사라진 DB를 가리키지 않게 한다.

## Previous: 실제 마크다운 RAG / GraphRAG 통합 테스트

examples/markdown_rag.py는 고정 합성 운영 문서를 처리하는 독립 테스트 어댑터다.
정식 RAG Component/검색 API를 구현한 것은 아니다. DemoRAGComponent와 기존 GraphRAG
정의 저장소에 원문/절/chunk/추출 관계를 저장하며 BM25/Chroma/Kuzu 인덱스를 연결한다.
코어 라이브러리와 Project → Task → Run → Engine → Step 경계는 변경하지 않았다.

실제 Gemini embedding-001로 8개 문단의 3072차원 임베딩을 만들었다. 실제 Gemini 3.1
Flash Lite가 관계를 추출하고 LoopEngine의 rag_query/graphrag_query Tool로 질문에
답했다. 8개 엔티티/6개 관계, 3개 완료 Run/7개 완료 Step. 정답(15분/30일, 한지민/서울),
출처, 절/문서 확장, 다른 프로젝트 관계 격리, 별도 프로세스의 재조회까지 통과했다.
2.5 Flash는 404, 3.8 Flash는 503이어서 새 실행에서 3.1 Flash Lite를 사용했다.

성공 결과는 tests/llm/reports/history/workspaces/research-demo/markdown-rag-verified/result.json, 실행 로그는
tests/llm/reports/history/markdown-rag-verified-python312.txt. 제공된 키는 프로세스 환경에만 전달했다.
의존성 chromadb 1.5.9, kuzu 0.11.3, rank-bm25 0.2.2를 기존 패키지 버전 유지 조건으로
설치했다. Chroma 클라이언트도 close하여 Windows DB 파일 핸들이 남지 않게 했다.
재실행/제약: docs/llm/markdown-rag-test.md. tests/llm/test_markdown_rag.py의 로컬 3개 테스트 통과.
전체 회귀 검증: Python 3.12.14에서 345개 테스트가 282.068초에 모두 통과했다.
명령: .venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v
전체 로그: tests/llm/reports/history/markdown-rag-suite-python312.txt.

## Previous: services 책임별 폴더와 공개 API 배치

services의 13개 구현 모듈을 lifecycle/(access, projects, tasks, steps, components),
runtime/(runs, events, tools), history/(conversation, context),
infrastructure/(storage, locking, logging)로 이동했다. api/configuration/query/results는
루트에 남긴다. 도메인 경계, 실행 로직, 저장 형식, 공개 메서드 인자는 변경하지 않았다.

기존 평면 import는 services/_compat에 모았고, services.__path__에 그 디렉토리를 추가해
표준 import로 찾도록 했다. 별칭은 새 모듈과 같은 객체이므로 기존 monkeypatch와 pickle
클래스 참조가 동작한다. 클래스 __module__과 소스 위치는 새 경로다. 내부 import와 현재
사용법 문서는 새 경로를 사용한다. 과거의 run_manager/io/deletion 별칭을 복구하지 않는다.

52개 서비스 클래스의 순서는 필드/초기화 → 내부 구현 → 공개 API로 정리했다. getter/setter,
async_method 별칭과 dataclass 필드 순서는 보존한다. 17개 서비스 모듈의 AST를 변경 전과
비교해 import 경로와 메서드 순서 외 실행 코드가 동일함을 확인했고, 위치 변경을 정규화한
370개 호출 시그니처도 일치한다. 결과: tests/llm/reports/history/services-refactor-checks.txt.

tests.llm.test_service_layout의 4개 테스트는 기존/신규 모듈 동일성, 기존 패치 대상, pickle 참조와
독립 프로세스의 import 순서를 검사한다. 압축 패키지의 격리 import도 별도로 통과했다.
폴더별 책임과 작성 규칙: llm/services/README.md, docs/llm/architecture.md.

첫 전체 실행에서 기존 중단/usage 테스트의 직접 Repository 조회가 백그라운드 파일 교체와
겹쳐 Windows PermissionError가 났다. 테스트 조회만 공유 WorkspaceOwnership의 StorageIO로
직렬화했고 검증 조건은 유지했다. 해당 단독 테스트는 통과했다. 첫 실행 로그는
tests/llm/reports/history/services-refactor-first-run-python312.txt에 남겼다. 프로덕션 실행 로직은 수정하지 않았다.

최종 전체 검증: Windows / Python 3.12.14에서 342개 테스트가 268.133초에 통과했다.
명령: `.venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v`.
결과: tests/llm/reports/history/test-results-python312.txt. compileall과 최종 AST 재비교도 통과했다.

## Previous: 프로젝트별 대화 저장 방식

projects.create/acreate(conversation_storage="file"|"memory")로 프로젝트별로 저장소를
선택하고 project.json에 보관한다. 생략하면 백엔드 기본값을 기록한다. 저장 선택이 없는
기존 프로젝트는 종전처럼 백엔드 기본값을 따르며 읽기만으로 마이그레이션하지 않는다.
ProjectConfig는 계속 열린 설정 데이터이고 저장 선택은 Project의 수명 주기 속성이다.

TaskManager의 ProjectConversations가 현재 Project 선택을 조회해 실행/조회/문맥/복제의
저장소를 공유한다. Project 복제는 선택을 보존하고 다른 Project로 Task를 복제하면 대상
저장소를 쓴다. Project.save/asave에서 선택 변경은 Task가 없을 때만 허용한다. 소프트
삭제된 Task도 검사한다. 사용자 주입 팩토리와 고정된 file/memory 선택은 충돌 오류를 낸다.
백엔드 종료는 자신이 생성한 메모리만 지우고 직접 주입한 팩토리의 수명은 유지한다.

전용 검증: tests.llm.test_project_conversation_storage + tests.llm.test_memory_conversation의
16개 테스트가 Python 3.12.14에서 38.609초에 통과했다. 파일/메모리 혼합 실행, 재시작 시
선택 유지, 복제 격리, 빈 Project 수정, 기존 데이터 호환, 영구 삭제를 검증했다.
사용법: docs/llm/conversation-storage.md.

전체 검증: Windows / Python 3.12.14에서 338개 테스트가 332.652초에 통과했다.
명령: `.venv312/Scripts/python.exe -m unittest discover -s tests/llm -t . -v`.
출력: tests/llm/reports/history/test-results-python312.txt. 이번 변경은 외부 모델 API 호출 없이 검증했다.

## Previous: 파일/메모리 대화 저장 선택

LargeLanguageModel(conversation_storage="memory") 및 ServiceConfig(conversations="memory")
선택을 추가했다. 기본 파일 저장은 유지한다. Conversation 공통 메시지 API 아래에
ConversationStore(JSONL)와 MemoryConversationStore를 두고, MemoryConversations가
Task/workspace 경로별 저장소를 공유한다. TaskManager가 선택을 한 번 해석하며 실행,
Facade 조회, 문맥 구성, Task/Project 복제가 같은 팩토리를 사용한다.

메모리 선택은 백엔드마다 독립 팩토리를 생성한다. Task 런타임 종료는 대화를 유지하고
백엔드 종료는 자신이 생성한 메모리/대기 요청을 해제한다. 사용자 주입 팩토리는 호출자가
수명을 소유한다. 영구 Task/Project 삭제는 선택적 discard(task) 계약을 호출한다.
기존 파일 대화는 자동으로 읽거나 수정/이전하지 않는다. Project/Task/Run/Step과 로그는
파일에 남으며, 메시지가 없어진 이전 Run의 응답 조회는 KeyError다. stale Run/Step 복구와
자동 재실행 금지 규칙은 유지한다. CLI에도 --conversation-storage file|memory를 추가했다.

사용법: docs/llm/conversation-storage.md. 전용 tests/llm/test_memory_conversation.py의 10개
테스트가 통과했다. 메시지 스냅샷/동시 접근, JSON 값 검증, 실제 LoopEngine 문맥,
스트리밍/대기/중단/동시 Task, 복제/삭제/복원, 파일 격리, stale 실행 복구를 검증한다.

최종 검증: Windows / Python 3.12.14에서 전체 332개 테스트가 453.643초에 통과했다.
메모리 CLI의 응답 출력, JSONL 미생성, Run 메타데이터 유지를 별도 실행으로도 확인했다.
외부 모델 호출은 수행하지 않았다. 로그: tests/llm/reports/history/test-results-python312.txt, tests/llm/reports/history/memory-tests-python312.txt.

## Previous: 실제 Gemini LoopEngine 조사 실행과 서명 ID 호환성

`python -m examples.llm.bleach_research --model gemini/gemini-3.8-flash`로 실제 모델이
공식 페이지 3곳을 조회하고 한국어 보고서를 작성/저장/재조회하는 테스트에 성공했다.
템플릿을 쓰지 않고 실제 페이지 본문을 모델에 전달한다. `--env-file`로 인증 환경을
읽을 수 있고, 예제는 API 키를 ProjectConfig에 저장하지 않는다.

성공 결과: `tests/llm/reports/history/workspaces/research-demo/bleach-gemini-retry/report/bleach.md`와 `result.json`.
Run `89c3fdcf651c45f2b6fe02c4175012ae`는 completed, artifact_verified=true.
모델 호출 4회, Tool 호출 5회(웹 3 + 작성 1 + 재조회 1), 약 44.44초,
해당 Run의 총 토큰 25,978. 종료 이유는 tool_calls 3회 뒤 stop이다.
실행 로그: `tests/llm/reports/history/research-gemini-retry-python312.txt`.

실행 중 발견한 BaseEngine의 고정 Tool ID 256자 제한을 수정했다. LiteLLM은 Gemini
응답 서명을 ID에 포함하므로 ID를 자르지 않고 max_argument_chars 한도까지 보존한다.
긴 ID가 다음 assistant/tool 메시지에 그대로 전달되는 테스트와 한도 초과 거부 검증을
추가했다. 관련 48개 테스트 통과. 최종 전체 테스트는 Windows / Python 3.12.14에서
322개가 458.870초에 통과했다. 로그: `tests/llm/reports/history/test-results-python312.txt`.

초기 2.5 Flash 호출은 서버의 신규 사용자 모델 제한(404), 이후 실행은 기존 ID 제한과
일시적 503으로 실패했다. 각각 별도 출력 디렉토리에 Run 이력을 유지했다. 실패한 Run을
자동 재생하지 않고 새 Run으로 재시도했다.

## Previous: 블리치 웹 조회/파일 저장 통합 예제

`examples/llm/bleach_research.py`는 슈에이샤/VIZ/애니메이션 공식 페이지에 실제 HTTPS
요청을 보내고, GraphEngine의 ToolNode를 통해 web_fetch → file_create → file_read를
실행한다. 공식 자료를 검토한 요약 템플릿을 사용하며 LLM 호출이나 모의 LLM 응답은 없다.
Tool 어댑터는 세 URL만 허용하고 리디렉션을 따르지 않으며 응답 크기와 시간을 제한한다.
페이지 변경은 근거 문자열 검사로 감지하며, 저장 내용/해시를 재조회해 검증한다.

Windows / Python 3.12.14 실제 실행 성공. 결과는
`tests/llm/reports/history/workspaces/research-demo/bleach-live/report/bleach.md`, Run/Step 요약은 같은 상위 디렉토리의
`result.json`, 도메인 저장소는 `workspace/`에 있다. 로그는 `tests/llm/reports/history/research-demo-python312.txt`.
재실행: `python -m examples.llm.bleach_research` (새 출력 디렉토리 자동 생성).

## Previous: 기본 Tool 카탈로그와 공통 Tool 실행기

* `llm/components/tools/builtin`의 BuiltinTools는 파일 읽기/검색/생성/수정/이동/
  삭제/복구를 제공한다. 수정에는 SHA-256 버전 확인을 사용하고 삭제는 휴지통 이동이다.
* 명령 실행은 명시적으로 선택한다. 셸/프로세스, 사전 등록한 검사 명령, Git 조회를
  지원하며 시간/출력 제한과 종료 시 프로세스 정리를 제공한다. OS 샌드박스는 아니다.
* 웹/브라우저/커널/RAG/하네스 변경 제안은 주입된 async 어댑터만 등록한다.
  실제 외부 백엔드와 하네스 실행기는 포함하지 않는다.
* AgentData의 prompt/update_prompt와 async 대응 API가 프롬프트만 버전 확인 후
  갱신한다. BuiltinTools.bind_prompts로 Tool에 연결할 수 있다.
* ToolExecutor를 LoopEngine과 GraphEngine의 ToolNode가 공유한다. Tool 인자와
  JSON 결과는 EngineEvent를 거쳐 Step에 기록한다. 도메인 경계는 유지한다.
* 사용법과 각 Tool의 인자는 `docs/llm/builtin-tools.md`에 정리했다.

검증: Windows / Python 3.12.14에서 신규 16개를 포함한 전체 321개 테스트가
245.830초에 통과했다. 실제 파일/프로세스/임시 Git 저장소와 Loop/Graph 연결을
검증했고 외부 서비스 어댑터는 테스트 콜백으로 검증했다. 실제 외부 모델/웹/
커널/RAG 서비스는 호출하지 않았다. 로그: `tests/llm/reports/history/test-results-python312.txt`.

## Previous: GraphEngine과 코드 수정/검증 Workflow

* llm/engines/graph.py의 GraphEngine이 저장된 Workflow를 같은 Run에서 실행한다.
  handlers는 노드 type → async GraphNodeContext 호출 객체이며 반환 dict를 상태에 병합한다.
  workflow와 처리기 capability를 합치고 모든 노드/참조를 실행 전에 검증한다.
* branch/parallel/join/loop/end, 반복/노드 수/시간 제한, 처리기 동시 실행 제한을 지원한다.
  병렬 경로 상태는 분리하고 branches에 합치며 실패/중단 시 자식 작업을 취소하고 기다린다.
  Step 시작 이벤트 저장 확인 후 작업을 진행하는 방식으로 실행과 저장을 연결한다.
* StepEventRecorder가 완료/실패 등 최종 이벤트의 metadata를 해당 Step에 병합한다.
  graph Step에는 정의 스냅샷과 최종 출력, 노드 Step에는 경로/출력/분기/반복 정보를 남긴다.
* examples/llm/code_workflow.py는 Agent/Workflow 등록 후 재로드하여 실제 코드 파일을 작성하고
  AST/compile 검사와 별도 Python 프로세스의 unittest를 실행한다. 기본 scripted 모델 응답은
  문법 오류 → 잘못된 연산 → 올바른 구현 순서다. max-attempts=2이면 상한 실패를 재현한다.
* --model을 지정하면 저장된 Agent 설정으로 실제 LiteLLM completion을 호출한다.
  이번 검증은 고정 모델 응답을 사용했고 외부 모델 API를 호출하지 않았다.
  예제는 순수 add(a,b) 구현만 다루며 범용 코드 실행 샌드박스/품질 분석기는 아니다.
* 실행 예제 결과는 tests/llm/reports/history/workspaces/workflow-demo/와 tests/llm/reports/history/workflow-demo-python312.txt에 남겼다.
  .workflow-demo는 gitignore에 추가했다. 사용법은 docs/llm/graph-engine.md에 있다.
* tests/llm/test_graph_engine.py의 신규 회귀 테스트는 16개다.

검증: Windows / Python 3.12.14에서 전체 305개 테스트가 201.518초에 통과했다.
code_workflow 예제도 직접 실행하여 3회차의 5개 검증 테스트 통과와 Run completed를
확인했다. compileall, 오프라인 wheel 빌드와 GraphEngine 포함 확인도 통과했다.
전체 로그: tests/llm/reports/history/test-results-python312.txt, 예제 로그: tests/llm/reports/history/workflow-demo-python312.txt.

## Previous: Skill · MCP · GraphRAG · Agent · Workflow 정의 컴포넌트

* 기본 LargeLanguageModel 등록 목록에 skills/mcp/graphrag/agents/workflows를 추가했다.
  Project에서 선택한 컴포넌트만 생성하고 명시적 backend components 목록은 기본을 대체한다.
* DefinitionComponent는 열린 JSON 검증과 configuration/records capability 스냅샷을 제공한다.
  Agent는 목적/모델/프롬프트, Skill은 지침, MCP는 연결 설정, GraphRAG는 코퍼스의 소스와
  엔티티/관계를 관리한다. 모두 기존 Component CRUD/clone/잠금/비동기 핸들을 사용한다.
* WorkflowGraph 빌더와 schema_version 1 그래프 검증을 추가했다. branch 조건별 port,
  parallel/join, loop.body/반복 상한을 지원하는 정의 형식이며 일반 간선은 DAG다.
  WorkflowData.validate/graph와 비동기 별칭으로 저장된 그래프를 검증/복원할 수 있다.
* 버전 없는 기존 Workflow/Subagent 데이터는 보존한다. workflows capability로 내보낼
  때는 버전 1로 명시적 변환해야 한다. Subagent에서 Agent로 자동 이전하지 않는다.
* 실제 GraphEngine 스케줄링, MCP 연결/도구 발견, GraphRAG 인덱싱/검색은 구현하지 않았다.
  다른 컴포넌트 ID/노드 처리기 등록 검증은 향후 실행기의 준비 단계 책임이다.
* API, 그래프 제어 계약, persistence와 제한은 docs/llm/component-definitions.md에 있다.
  tests/llm/test_definition_components.py에 추가 회귀 테스트 21개가 있다.

검증: Windows / Python 3.12.14 전체 289개 테스트가 186.881초에 통과했다.
기존 ToolData 테스트의 기본 핸들 검증 대상은 편의 API가 생긴 Workflow 대신 사용자 정의
Notes로 변경했다. compileall, 문서 예제 7개 임시 workspace 실행, 오프라인 wheel 빌드와
신규 모듈 포함 확인도 통과했다. 모델 API/MCP 서버/실제 검색 인덱스는 실행하지 않았다.
전체 테스트 로그는 tests/llm/reports/history/test-results-python312.txt에 있다.

## Previous: 선언 기반 실행과 서비스 확장 API

* ServiceConfig가 Project/Task/Run/Step 저장소와 문맥/로그/이벤트 처리를 조립한다.
  Facade와 실행, Project/Task 결과 조회가 동일한 Run/Step 저장소를 사용한다.
* Engine.required_capabilities는 기본 빈 튜플이다. LoopEngine은 tools를 선언하고
  PipelineEngine은 단계들의 요구를 합친다. context.tools를 사용하는 기존 사용자
  Engine은 required_capabilities = ("tools",)를 추가해야 한다.
* EventHandlers가 문자열 사용자 이벤트를 처리한다. async 처리기 완료 후 관찰자를
  호출하며 실패/중단은 기존 Run/Step 수명 주기를 따른다. EventContext.update_metadata는
  서비스의 저장 경로를 사용한다. 내장 이벤트 처리기를 덮어쓸 수는 없다.
* EventSubscriptions는 다중 inline/queued 관찰자를 제공한다. 큐는 유한하고 가득 차면
  생산자가 대기한다. 동기 queued 콜백은 스레드로 실행하며 백엔드 종료 시 알림을 배출한다.
  관찰자 안에서 같은 백엔드의 wait/shutdown을 기다리는 방식은 지원하지 않는다.
* ContextPolicy는 full/recent/completed/recent_completed/budget 프리셋을 제공한다.
  budget은 문자 수이며 현재 입력을 잘라내지 않는다. 저장/복제 대화는 그대로 유지한다.
* Query(after, status, offset, limit, descending)를 주요 도메인/결과/대화 조회에 적용한다.
  파일 정렬을 위한 전체 스캔은 남아 있으며 별도 인덱스/DB를 도입하지 않았다.
* DomainLogger/LogSettings로 기본 회전 파일 로그의 설정이나 출력 sink를 교체한다.
  workspace 소유권 범위에 로그 문맥을 적용하여 백엔드별로 격리한다.
* TaskManager.update_details와 TaskHandle.save(title/metadata)는 실행 중에도 가능하다.
  런타임 상태 저장 시 최신 제목/metadata를 보존한다. config/복제/삭제의 해제 규칙은 유지한다.
* ComponentData.bind_runtime으로 Facade 실행기/수명 검사를 명시적으로 연결한다.
* llm의 50개 Python 모듈에 한국어 파일 설명을 추가하고 주요 공개 클래스/경계에 주석을
  보강했다. API 예시는 docs/llm/service-extensions.md에 있다.
* 현재 ish.platform의 Python 의존성 파서 변경에 맞춰 tests/llm/test_plugin.py를 갱신했다.
  호스트 소스 자체는 수정하지 않았다.

검증: Windows / Python 3.12.14에서 전체 268개 테스트가 191.815초에 통과했다.
추가 회귀 테스트는 19개이며 tests/llm/reports/history/test-results-python312.txt에 전체 결과가 있다.
compileall, 오프라인 custom_engine 예제, 한국어 모듈 설명 50/50 확인,
wheel 빌드와 신규 확장 모듈 포함 확인도 통과했다. 이번 변경에서 실제 모델 API나
Linux PTY를 실행하지는 않았다.

## Previous: request handles, facade results, async CRUD and Engine settings

* Facade submit returns RequestHandle (id is still the user Message ID).
  request.wait(timeout=...) returns the matching terminal RunHandle; failures and
  interruptions remain inspectable results. Lower-level RunManager.submit still
  returns Message. task.run.request/arequest reopens persisted requests by ID.
* RunManager.wait_request uses lifecycle signals and worker completion, always
  checking persisted state; no polling or per-request Future cache. Cancelling or
  timing out a wait never cancels execution. Shutdown preserves queued input and
  wakes waiters; stale Runs are recovered as interrupted, never replayed.
* project.results/task.results list/load and alist/aload query Run observations.
  run.result/aresult and response/aresponse expose execution metadata and Assistant
  messages. request.result/aresult is None until its Run exists. No new result files.
* Facade storage calls add a-prefixed methods while keeping synchronous ones.
  aget_data/aconversation and components.aget avoid hidden property/lookup I/O.
  ComponentData/ToolData provide async CRUD/selection; custom handles opt in via
  storage.async_method. Backend tracks and drains admitted facade storage work
  on cancellation/shutdown, with one bounded StorageIO lane and existing locking.
* LoopEngine uses the registered Run engine name as its settings section. Optional
  settings_name explicitly selects another section, including Pipeline stages.
  Shared Engine instances keep per-Run settings isolated. CLI/custom example now
  use async CRUD and per-request waiting.

Validation: 249 tests passed on Windows/Python 3.12.14 in 130.253 seconds;
see tests/llm/reports/history/test-results-python312.txt. Fifteen added cases cover per-request identity,
multiple waiters, timeout/cancellation isolation, failure/interruption, shutdown,
queued/stale recovery, storage failure, scoped results, async lifecycle/component
CRUD, off-loop responsiveness/cancellation drain, workspace ownership, aliases
and explicit Pipeline stage settings. The offline custom Engine example,
compilation, whitespace check and wheel build also passed. No live model calls
were made for this change. Full-scope scans remain; pagination is not introduced.

## Previous: component-specific handles and Tool selection conveniences

* Component.data_class optionally selects a ComponentData subclass; the default
  remains the generic handle. Registration rejects invalid classes before use.
  ProjectManager constructs the selected handle without knowing Tool types.
* ToolComponent declares ToolData in components/tools/data.py. Both manager and
  backend APIs provide enable(*names), disable(*names), set_enabled(names) and
  enabled(), with common CRUD/configure unchanged.
* ToolComponent owns selection semantics; ToolData holds the workspace lock for
  the complete operation, reloads Project lifecycle/selection and logs mutations.
  Other config keys survive selection edits. Add/remove are idempotent and ordered;
  replacement rejects duplicate names. Read results are detached.
* Definitions and handlers remain independent of selection editing. Registration
  does not auto-enable; missing handlers are still rejected only during execution.
  Existing persistence and per-Run snapshots are unchanged. CLI uses enable("add").

Validation: all 234 tests passed on Windows/Python 3.12.14 in 111.731 seconds.
Output: `tests/llm/reports/history/test-results-python312.txt`. Coverage includes concurrent selection edits,
workspace ownership, stale/deleted handles, reload/clone isolation, extension-key
preservation, invalid input/write failures, generic handle opt-in and LoopEngine
tool binding. Wheel build passed. No live model calls were made.

## Previous: explicit per-request Engine selection

* `task.run.submit(content, engine="name")` and RunManager.submit require the
  engine keyword on every request. Omission is TypeError; empty/invalid values
  use RunRequestError(engine_required) before persistence. Unknown registrations
  remain engine_not_registered. No selection falls back to Project or Task.
* ProjectConfig no longer inserts default_engine. Legacy arbitrary Project keys
  round-trip but do not select Engines. Task's default_engine field/creation
  argument is removed; its repository reads old task.json files by discarding
  the legacy field. Queued request metadata is the sole source of engine choice.
* A restored queued request without an Engine becomes a failed Run with
  engine_required (empty Run.engine), without model/tool execution. Failure
  notification is published, and later queued requests continue normally.
* CLI requires `--engine loop`. Registration still makes Engines available;
  it does not automatically select one. Examples/tests use explicit selection.

Validation: 227 tests passed on Windows/Python 3.12.14 in 128.568 seconds.
Output: `tests/llm/reports/history/test-results-python312.txt`. Added mandatory-selection, legacy metadata,
missing-engine queue recovery and CLI rejection tests.llm. The streaming request
configuration test now explicitly sets its expected 60-second timeout rather
than assuming that value is the current Engine default. No Engine timeout
defaults were changed. Offline custom Engine example, compilation, wheel build
and wheel API inspection passed. No live model calls were made.

## Previous: LargeLanguageModel backend and Python 3.12.14 validation

* `llm/llm.py` now defines LargeLanguageModel for retrieval through
  `llm = plugin.get("llm"); LargeLanguageModel = llm.LargeLanguageModel`.
* ProjectManager creates TaskManager by default and accepts component instances
  as a list, building ComponentRegistry internally. Injection remains supported.
* `backend.projects.create/load/list` returns service handles. Navigate through
  `project.tasks.create/load`, `task.run.submit/start/wait_idle/interrupt/shutdown`,
  `task.run.list/load`, `run.engine` and `run.steps.list/load`.
* Project/Task handles support lifecycle CRUD and `.data` snapshots. They are
  separate from persisted models. `project.components.tools.register(dict)`
  persists definitions without requiring handlers; runtime binding is unchanged.
* One Task has one cached RunManager even across repeated loads. Backend shutdown
  drains all managers and cancellation. Same-process/event-loop ownership is
  enforced; the host must await shutdown. CLI uses the same facade.
* Python 3.12.14 was installed locally under `.python/` with uv in `.devtools/`;
  `.venv312` has project dependencies. Only that version is used for this change's
  tests.llm. `.python-version` pins it; older compatibility helpers remain in place.

Validation: 223 tests passed on Windows/Python 3.12.14 in 112.809 seconds
(LiteLLM 1.101.0). Full output: `tests/llm/reports/history/test-results-python312.txt`. The initial fresh-env
run revealed a tokenizer download in the inference SDK test; it now uses a local
vocabulary and cost map like the existing streaming SDK test. No live LLM calls
were made. Editable install, wheel build/content check, dependency check,
compilation and the offline custom Engine example passed. The real host loader
retrieved LargeLanguageModel and executed an offline backend request. Linux/WSL
PTY and full retrieval dependency integration remain unverified.

## Previous: llm plugin package and entry point

* Renamed the AI package `ish/` to `llm/` and `demo.py` to `llm.py`; all internal,
  test and example imports use `llm`. Distribution name is `ish-llm`.
* `llm/llm.py` declares literal PLUGIN_META with LiteLLM, jsonschema, chromadb,
  kuzu and rank-bm25|rank_bm25 dependencies. No other plugins are required.
* Register `prompt.set_tool("llm", plugin_name="llm", function_name="main")`.
  The worker entry accepts explicit strings and exits with the Run's outcome.
  Each call creates a fresh Project/Task and closes its RunManager.
* Existing persistence and `.ish.lock` are unchanged. No persistent UI or
  background service is added. Retrieval dependencies do not imply retrieval
  implementations.
* Tests exercise metadata, argument isolation, streaming/failure persistence,
  shutdown and the real platform loader plus fresh-process pickle imports,
  with package installation mocked. Linux/WSL PTYs and dependency installation
  require separate validation. See README for installation and limitations.

## Previous: Task-bound Run API, open settings and independent component data

* RunManager requires task= at construction. start/submit/wait_idle/interrupt no
  longer take Project/Task arguments; shutdown affects only its bound Task.
  Applications share the workspace repository/TaskManager/registries across
  per-Task managers. Queue durability, cancellation drain and stale-Run rules stay.
* Registration validation rejects unknown Engines/components before admitting new
  messages. RunRequestError.code identifies the rejection. Run.error_code and
  query views expose execution outcomes; raw error details are no longer masked.
* on_run_event receives separate RunEvent STARTED/COMPLETED/FAILED/INTERRUPTED
  snapshots after persistence. Notifications run on the event loop, isolate
  observer errors, and include newly interrupted stale Runs during recovery.
* ProjectConfig is an open dict subclass, with constructor kwargs, attribute
  shortcuts, to_dict/serialize/deserialize and arbitrary top-level keys. Existing
  reserved sections/JSON types remain validated; no key-name secret restrictions,
  obsolete-reference stripping or URL credential/query stripping remains.
* Tool CRUD/configuration/clone no longer needs handlers. Runtime resolution alone
  binds handlers. Other components retain data-only CRUD. Components declare a
  capabilities tuple and resolve only the requested name; exports was removed.
* Updated all caller examples and regression tests for Task-bound managers, and
  added focused public API tests for notifications, request validation, per-Task
  shutdown/recovery, open configuration and independent/lazy component behavior.

## Previous: generic Project component storage

* Component now declares name/directory and implements JSON codecs, configuration,
  named record CRUD, safe directory initialization/removal and definition cloning.
  Registry no longer imports tools or requires resolve_tools; optional runtime
  exports are generic. ComponentToolResolver is the Tool-specific adapter, and
  RunManager still accepts capabilities=components and custom tool resolvers.
* Tools, Workflows and Subagents inherit the base. tools/component.json retains
  enabled names and extra config; optional records/<tool-name>.json stores native
  function definitions with extension keys, validated against registered handlers.
  Subagents and Workflows store open dict model settings/graph documents. Neither
  class executes nodes, validates a fixed graph format, or adds another Run layer.
* projects.component(project, name) returns ComponentData for locked, lifecycle-
  checked CRUD. create/set_components initialize declared directories. Disable
  retains data; remove_component(permanent=True) requires detached Tasks and
  disables before safe tree removal. Failure can leave disabled partial data.
* Base clones configuration and records only, retaining IDs for graph references.
  Old tools config and empty workflow roots remain readable; legacy graph artifacts
  are retained without implicit import. Python custom components must now declare
  directory and inherit the base or implement the expanded ProjectComponent API.
* Tests cover open-key round trips, update/replace/delete, atomic-write failure,
  cloning, lifecycle/ownership, registry/path rejection, Windows junctions, legacy
  workflows, concurrent locked updates, generic export/import boundaries and
  persisted tool definitions reaching LoopEngine with trusted handler execution.

## Previous: Run-owned results and reusable model inference

* Removed ProjectExecutionStore and Project summary writes. CompletionResult stays
  in Run metadata; RunRepository finalizes interrupted observations on terminal
  saves/recovery. ExecutionResult is now an in-memory view of one Run.
* RunResultQuery provides Project/Task load/list via projects.results, tasks.results
  and manager.results. Queries hold workspace ownership, support deleted/running
  filters and injectable Run readers. Permanent Task deletion removes Run history;
  cloning does not copy it. Old Project state/executions files are ignored and left
  untouched, never used to resurrect missing Runs.
* Added inference/EmbeddingModel and RerankModel calling LiteLLM aembedding/arerank.
  Open kwargs, SDK response types, errors and cancellation are preserved. Inputs and
  option containers are isolated while SDK handles retain identity. No Run/Step
  creation, engine registration, file writes or automatic usage events occur here.
* Shared request-container copying lives in providers/parameters.py; BaseEngine's
  public helper delegates there. Inference does not import Engines/services/core
  or eagerly import the SDK. ProjectConfig.data.inference can hold optional model
  defaults, with Task overrides; RAG/index lifecycle remains with components.
* Tests cover Run-only persistence, Task/Project query equivalence and filtering,
  legacy summary isolation, recovery, deletion/clone policy, provider dispatch,
  model argument forwarding, concurrency, cancellation and import boundaries.

## Previous: flexible configuration and Project execution results

* ProjectConfig now holds default_engine/completion/engines/task_defaults/data.
  Task.config is persisted, saved, cloned and exposed through EngineContext.settings.
  Loop resolves Project -> Task -> constructor overrides on a per-Run instance.
  Old stored flat provider fields migrate on load; Python callers use the new API.
* CompletionResult/ExecutionResult live in core/results.py. COMPLETION events carry
  stable per-call observations. BaseEngine forwards observations alongside text;
  include_events=False is a standalone text-only opt-out. Raw provider payloads,
  prompts, headers and credentials are not copied into result files.
* RunManager records call snapshots in Run metadata then writes atomic Project
  state/executions/<run-id>.json summaries via ProjectExecutionStore. ProjectManager
  exposes results.load/list. Every terminal Run gets a summary, including fake,
  preparation-only, failed and interrupted Runs. Recovery rebuilds missing summaries
  and interrupts stale observations without replay. Incomplete usage remains unknown.
* Removed the application credential service and its paths/CLI argument/imports.
  SDK environment or runtime-only completion_kwargs handles authentication. Updated
  obsolete documentation/instructions and maintained credential-free JSON/logs.
* Added regression coverage for configuration persistence/precedence/isolation,
  migration, summary aggregation, usage-only chunks, failed/partial completions,
  recovery, clone/delete policy and payload exclusion. Updated old event-count
  assumptions and TaskManager.save to persist the new configuration.

## Previous: BaseEngine and inherited LiteLLM Loop (2026-09-08)

* Renamed StepEngine to BaseEngine and moved it into engines/base.py. Removed
  engines/step.py and private engines/_completion.py. Public exports, preparation,
  examples, demo, manual API example and automated tests use the new API.
* BaseEngine centralizes Step events/cleanup and reusable stream_completion():
  dict/SDK chunk text, fragmented tool calls, bounded output and finish validation.
  A per-call response dict receives an assistant message after successful assembly.
  Request containers are copied while live SDK handles keep identity.
* Rebuilt loop.py as one LoopEngine(BaseEngine) class. It uses inherited step()
  and stream_completion(). LoopOptions/options=, LoopEngineError, _Turn and
  _ToolCall are removed. All application limits are explicit constructor kwargs;
  unrestricted provider options continue to use completion_kwargs.
* Project -> Task -> Run -> Engine -> Step ownership and persistence are unchanged.
  No tool execution in BaseEngine and no automatic stale-Run retry were introduced.
* Added coverage for minimal inherited completion with persistent Steps, SDK/dict
  chunks, interleaved tool assembly, size/finish errors, simultaneous stream state,
  SDK client identity, request copying and early close. Updated type-hint
  checks and direct Loop limit validation. No live provider calls are required.

## Previous: common StepEngine authoring API

* Added engines/step.py::StepEngine, publicly available from llm.engines together
  with EngineContext/EngineRegistry. Developers implement run(context) or pass
  action=, yielding text or awaiting a no-output operation. Step IDs, lifecycle
  events, safe failures, optional timeout and iterator cleanup are centralized.
* LoopEngine completion/tool stages and PreparationStep use the same helper.
  Loop-specific options/parser helpers moved to private engines/_completion.py;
  imports of LoopOptions/LoopEngineError from llm.engines.loop remain valid.
* One StepEngine call creates one Step inside the existing Run. Pipeline composes
  multiple stages. No changes to persistence, managers, OS ownership, or providers.
  LLM step failures now use the generic safe 'LLM iteration failed' message.
* Added examples/llm/custom_engine.py (offline Echo Engine with full service setup),
  a README authoring guide, and regression tests for subclass/callback actions,
  persistent events, metadata/state isolation, failure/cancellation/timeout,
  generic iterators, invalid output, and early-close/cleanup failures.

## Previous: open LiteLLM parameters and preparation pipelines

* LoopEngine stays LiteLLM-specific; completion_kwargs accepts a mapping or sync
  context factory. Project values are defaults. SDK client/callback identities are
  retained; option containers are copied per execution/request. Unknown LiteLLM
  options pass through, while Loop messages/tools/stream/single-choice ownership
  remains enforced. system_prompt accepts a string or sync context factory.
* LoopOptions now contains application limits only. max_tokens moved to
  completion_kwargs. Provider timeout/retries can be set there independently of
  the application deadline; no automatic tool/Run replay is introduced.
* PipelineEngine composes stages inside one Run. PreparationStep wraps an async
  callback with observable Step events and timeout. EngineContext.state holds
  Run-local outputs. Preparation failure/cancellation stops later stages; queues,
  persistence and recovery stay with the existing managers.
* No embedding/rerank or generic provider abstraction was added. No RAG or shell
  execution implementation is claimed; developers supply callbacks/components.
* README has runnable API patterns, including document preparation and prompt
  factories. tests/llm/test_pipeline.py covers order, state, failure, cancellation,
  timeouts, concurrency, nested stages and iterator closing. The actual LiteLLM
  mock-SSE test now passes a live client and completion options through kwargs.

## Previous: service module consolidation (2026-09-07)

* Renamed conversation_context.py to context.py, retaining ConversationContextBuilder.
* Merged io.py (StorageIO/drain_on_cancel) and deletion.py (remove_owned_tree)
  into storage.py. Sections distinguish metadata primitives, deletion, and async I/O.
* Moved RunEventPublisher from events.py into runs.py as a separate class.
* Updated implementation/test imports, patch targets, and README examples.llm. Removed
  the old modules; there are 11 functional modules plus services/__init__.py.
* Kept access.py to avoid a ProjectManager/TaskManager dependency cycle, and kept
  locking/logging/secrets separate because they have distinct shared responsibilities.
* AST comparison verified that all 10 moved/existing storage/context/publisher
  definitions retain identical implementations. Execution and persistence formats
  are unchanged; use the new import paths documented in architecture.md.

## Previous: workspace locking and storage I/O (2026-09-07)

* WorkspaceOwnership uses stable `<projects-root>/.ish.lock` with nonblocking OS
  locks (Windows byte-range/POSIX flock). Lifecycle transactions are guarded;
  runtime leases span recovery through shutdown/drain, including idle workers.
  Process death releases the lock without PID timeouts or deleting the file.
* Share one repository/service container per workspace. Attached Task IDs are
  coordinated across its TaskManagers; other repository instances fail fast with
  WorkspaceBusyError. This allows one owning process, not distributed workers.
* StorageIO offloads ordered persistence/context work with one in-flight thread
  operation per RunManager. Cancellation drains writes. Accepted submit/start/
  shutdown calls finish before forwarding cancellation. Run preparation supports
  interrupt before the Engine child exists.
* ConversationStore caches incremental replay, detects replacement/truncation,
  protects its projection with an instance lock, and returns detached snapshots.
  RunManager reuses one store per attached Task. Per-delta operational logs and
  repeated directory sync/tail checks are removed; every delta still fsyncs.
* JSON/JSONL domain formats and hierarchy remain unchanged. The infrastructure
  lock file lives outside Project trees. Lower-level writes bypassing managers
  need an ownership scope. Async UIs can use StorageIO for synchronous CRUD.
  Custom synchronous storage/context/capability adapters run on worker threads.
* Fourteen new tests cover actual subprocess conflicts/crash release, shared
  attachment, incremental decode work, cache invalidation, fsync failure, loop
  responsiveness, cancellation drain, accepted input and shutdown ordering.

Older sections describe historical milestones. Tool permissions, structured tool
history, Linux/network-filesystem validation, live-provider testing, backup,
migration and production-load verification remain outstanding.

## First Task Progress (2026-09-06)

The workspace initially contained only `AGENTS.md`, this handoff, and the
architecture document, with no implementation, tests, or Git metadata.
The Suggested First Codex Task below has now been implemented as a foundation:

* persistent domain models and major paths with the documented service boundaries
* atomic JSON metadata and durable append-only conversation events
* deterministic FakeStreamingEngine with chunks, delay/gate controls, failure injection, and cancellation handling
* RunManager durable queues, per-Task serial execution, cross-Task concurrency, streaming, interrupt, graceful shutdown, and restart recovery
* StepManager and StepEventRecorder with terminal-state validation
* basic Project/Task lifecycle, soft-delete/restore, and safe conversation snapshot cloning
* standard-library integration tests, including abrupt subprocess termination and restart

Regression tests also cover cancellation before the Engine starts, burst queue
context ordering, the crash gap between Run creation and input commitment,
cloned conversation order, generic async iterator Engines, and shutdown while
a caller waits for the queue to drain. The architecture document describes the
implemented scope and its single-process ownership contract. `README.md`
contains a runnable usage example.

Run all tests with:

```sh
python -m unittest discover -s tests/llm -t . -v
```

## LiteLLM LoopEngine Progress (2026-09-06)

The user explicitly requested LoopEngine next, using synchronous
`litellm.completion(stream=True, ...)`. That request supersedes the original
SingleEngine-first sequencing below.

Implemented:

* LoopEngine: streaming content, fragmented tool calls, registered async tools, and bounded iteration
* dedicated stream thread and bounded async bridge, with late-delta suppression on cancellation
* per-request/tool timeouts, no automatic retries, and validation before tool batches execute
* provider configuration (superseded by the extensible settings API above)
* RunManager `on_event` callback after persistence, and `python -m llm.llm` for streaming output
* regression tests for real-time delivery, tools, failure, cancellation, timeouts, and cross-Task concurrency
* an installed LiteLLM SDK integration test with mock SSE transport; no live model request

Install dependencies with `python -m pip install -e .`, then run the full suite
using `python -m unittest discover -s tests/llm -t . -v`. The implementation was checked
with LiteLLM 1.100.0. See README for usage and the architecture document for
thread cleanup and transient tool-transcript limitations.

Next work: durable structured tool messages through ConversationStore,
provider-specific compatibility tests, and TUI integration. A dedicated
SingleEngine and GraphEngine remain unimplemented. The broader original
milestone below therefore remains partially outstanding.

## Repository Consistency Refactor

RunManager now lives alongside RunRepository in `llm/services/runs.py`.
TaskRuntime is defined in `llm/services/tasks.py`, with scheduling still owned
by RunManager. The former `run_manager.py` file was removed, and source,
test, demo, and README imports use `from llm.services.runs import RunManager`.

TaskRepository and StepRepository were added to their respective service modules.
TaskManager and StepManager delegate JSON persistence, path resolution, and
listing to these repositories. Lifecycle behavior remains in the managers;
TaskManager's active-Run guard reloads through TaskRepository. All four metadata
domains now have a Repository/Manager pair. ConversationStore keeps its
append-only Message event format, and Engine remains persistence-independent.

TaskManager/StepManager accept optional `repository=` injection. RunManager also
exposes `repository=`, while retaining `runs=` and `.runs` for existing callers.
The persisted formats, paths, and runtime/recovery behavior are unchanged.
Additional tests cover repository injection without metadata files, ownership
checks, round trips, task filtering, and duplicate Step IDs.

## Current Architecture

Service boundary/component update (2026-09-07):

* ProjectAccess is shared by ProjectManager/TaskManager/RunManager through service composition. Current persisted Project state is checked at mutation and execution entry points. Public save cannot resurrect deleted records or overwrite managed Task runtime state.
* TaskManager must be bound by ProjectManager or receive `project_access=`. RunManager still owns runtime scheduling; TaskRuntime remains in tasks.py.
* RunEventPublisher isolates UI observer failures and logs them safely; callbacks are still synchronous/nonblocking.
* ConversationContextBuilder handles Run and clone turn ordering. Both managers accept an injectable conversation factory; RunManager defaults to TaskManager's factory/builder.
* Project has persisted component names (old metadata defaults to none). ProjectManager.create accepts selected names and delegates directory creation to registered components.
* ProjectPaths no longer exposes tools/workflows. ToolPaths and WorkflowPaths own their respective directories.
* ToolComponent persists enabled tool names; WorkflowComponent initializes a directory only. RunManager accepts `capabilities=components` and injects fresh Project-scoped tools per Run. LoopEngine no longer accepts global `tools=`.
* Save Project configuration before execution; stale caller configuration is no longer the runtime source of truth. Task public edits require a detached runtime.
* Register implementations/handlers again on startup; persisted names do not dynamically load code. Component initialization is idempotent and failures can leave retained partial directories; see architecture/README for failure and clone policies.

Latest organization/lifecycle changes:

* Removed `llm/engines/fake.py`; deterministic tests use `tests/llm/support/fake_engine.py`.
* Moved Tool/ToolRegistry to `llm/components/tools` and provider transport to `llm/providers/litellm.py`.
* Reserved `components/rag`, `mcp`, `skills`, `subagents`, and `workflows` for future CRUD/adapters; no CRUD implementations are claimed for these packages.
* Categorized comments in core models; new ProjectConfig defaults to `loop`.
* Renamed Project/Task `soft_delete` to `delete(..., permanent=False)`, with guarded permanent removal and parent-scope deletion logging.
* Shared TaskManager tracks runtime attachment; shut down RunManager before lifecycle deletion/cloning. This is not a cross-process lock.
* Services write safe structured, rotating `logs/service.log` files in Project/Task/Run/Step scopes using standard-library logging.
* Added regression coverage for permanent deletion, stale restore, ownership and linked-path rejection, runtime attachment, safe log content, rotation, and logging failures.

Persisted formats are unchanged. Explicit old `fake` engine selections must be
updated by the caller for real execution. Production limitations and next steps
are recorded in `docs/llm/production-readiness.md`.

The intended hierarchy is:

Project
→ Task
→ Run
→ Engine
→ Step

Conversation belongs to Task.

Persistent state and runtime state are intentionally separated.

## Implemented or Designed Components

### Core

* Project
* ProjectPaths
* Task
* TaskPaths
* TaskStatus
* Message
* MessageRole
* MessageStatus
* Run
* RunPaths
* RunStatus
* Step
* StepPaths
* StepStatus
* Engine protocol
* EngineContext
* EngineEvent
* EngineEventType
* EngineRegistry

### Services

* ProjectManager
* TaskManager
* ConversationStore
* RunManager
* StepManager
* StepEventRecorder

### Persistence Behavior

Project, Task, Run, and Step metadata use JSON files with atomic replacement.

Conversation history uses append-only JSONL events.

Conversation events currently include concepts equivalent to:

* message.create
* message.delta
* message.status
* message.metadata
* message.run

## Execution Flow

User input follows this path:

TUI
→ RunManager.submit()
→ ConversationStore persists Message as QUEUED
→ TaskRuntime asyncio.Queue receives message ID
→ Task worker selects queued Message
→ Run created
→ Message QUEUED → COMMITTED
→ Task binds current Run
→ EngineRegistry resolves Engine
→ Engine.execute()
→ EngineEvent stream

Text events:

EngineEvent(TEXT_DELTA)
→ ConversationStore appends Assistant delta
→ TUI receives streaming update

Step events:

EngineEvent(STEP_*)
→ StepEventRecorder
→ StepManager
→ persistent Step state

Run completion:

Assistant STREAMING → COMPLETED
Run RUNNING → COMPLETED
Task RUNNING → IDLE

## Concurrency Model

One Task has one serial Run worker.

Multiple Tasks may have independent workers and execute concurrently.

This is intentional.

Do not introduce concurrent Runs inside a single Task unless the architecture is explicitly redesigned.

GraphEngine may internally execute independent graph nodes concurrently, but those executions must remain Steps belonging to the same Run.

## Queue Semantics

Every incoming user request is first persisted as QUEUED.

If the Task is idle, the worker consumes it quickly.

If the Task already has a running Run, the request remains QUEUED.

When execution begins:

QUEUED → COMMITTED

The persisted conversation is the source of truth.

The asyncio.Queue is runtime acceleration only.

## Interrupt Semantics

Normal user input does not interrupt the current Run.

Explicit interrupt cancels only the active Run.

Queued requests are preserved.

Partial Assistant output becomes INTERRUPTED.

The Task worker remains alive and proceeds with the next queued request.

## Recovery Policy

On application restart:

* stale RUNNING/PENDING Run → INTERRUPTED
* stale RUNNING/PENDING Step → INTERRUPTED
* stale STREAMING Assistant Message → INTERRUPTED
* queued user messages → restored into runtime queue

Do not automatically replay stale Runs.

A stale Run may already have produced side effects.

## ProjectManager Boundary

ProjectManager is an orchestration service.

It delegates:

* persistence to ProjectRepository
* component initialization to initializer services
* migration to ProjectMigrator
* Task cleanup to TaskManager or TaskMaintenance

Do not move Memory, Workflow, Tool, or Task directory internals into ProjectManager.

## TaskManager Boundary

TaskManager owns Task lifecycle and Task-level directory structure.

It does not execute Runs or Engines.

## ConversationStore Boundary

ConversationStore owns conversation persistence.

Do not store conversation content in ProjectManager logs.

Do not rewrite the complete conversation file for every streaming chunk.

## RunManager Boundary

RunManager owns:

* runtime Task workers
* durable queue restoration
* Run lifecycle orchestration
* Engine execution
* interrupt handling
* Assistant streaming persistence

RunManager should remain independent of prompt-toolkit.

## StepManager Boundary

StepManager owns persistent Step lifecycle.

StepManager must not execute the action represented by a Step.

Actual work belongs to Engine/tool implementations.

## Immediate Recommended Work

### 1. Add tests before expanding Engine complexity

Implement tests covering:

* Task creation
* Message persistence
* queued input
* Run creation
* streaming Assistant response
* second request during active Run
* explicit interrupt
* stale Run recovery
* stale Step recovery
* concurrent independent Tasks

Use a deterministic fake Engine before using a live LLM API.

### 2. Implement a FakeStreamingEngine

Create a test Engine that:

* yields deterministic text chunks
* emits Step events
* can sleep between chunks
* can intentionally raise an error
* can react to cancellation

This should validate RunManager without external APIs.

### 3. Implement production SingleEngine

After the fake-engine tests are stable, implement SingleEngine using LiteLLM async streaming.

SingleEngine should:

* construct model messages from EngineContext
* use Project configuration
* use SDK environment or runtime-only credentials
* emit an LLM Step
* emit TEXT_DELTA events while streaming
* propagate cancellation correctly
* avoid storing provider response objects directly in Step metadata

### 4. Add graceful shutdown

On application shutdown:

* stop accepting new submissions
* preserve queued Messages
* interrupt active Runs safely
* flush persistence
* stop Task workers
* close provider resources where needed

### 5. Implement LoopEngine only after SingleEngine is stable

LoopEngine should use the same RunManager and StepManager infrastructure rather than creating a parallel execution architecture.

Likely Step sequence:

LLM reason
→ tool action
→ observation
→ LLM reason
→ tool action
→ observation

### 6. Implement GraphEngine after LoopEngine boundaries are proven

GraphEngine executes predefined workflow graphs.

Graph nodes should normally map to Steps.

Independent graph nodes may execute concurrently inside one Run.

## Suggested First Codex Task

Inspect the repository before changing code.

Then:

1. verify the current Project/Task/Message/Run/Step implementations against `AGENTS.md`
2. add a deterministic FakeStreamingEngine
3. add integration tests for RunManager queue, streaming, interrupt, and recovery
4. fix implementation defects revealed by those tests without collapsing the existing service boundaries
5. run the full test suite

Do not implement LoopEngine or GraphEngine during this first task unless explicitly requested.

## Important Constraints

Do not:

* put asyncio.Queue inside Task
* put asyncio.Task inside persisted domain objects
* store API keys in project.json
* log Authorization headers
* automatically retry stale Runs
* expose queued future user Messages to the currently running Engine context
* directly couple Engine implementations to prompt-toolkit
* let ProjectManager become a general application God class

## Definition of Done for the Next Milestone

The next milestone is complete when:

* fake-engine integration tests pass
* streaming works
* multiple Task workers can run concurrently
* one Task still executes Runs serially
* queued input survives restart
* interrupt preserves queued input
* stale Run and Step state recover safely
* SingleEngine works through LiteLLM
* no secret values are persisted in ordinary project configuration

## 공통 사용자 요청/승인 데이터

- `llm/core/interactions.py`: InteractionRequest/InteractionOption/InteractionResponse 공개 클래스.
- Loop, Graph ToolNode/pause_before, 중첩 Loop Agent가 공통 요청을 체크포인트에 기록한다.
- `run.ainteractions()`, `run.ainteraction_responses()`, `run.arespond(request.respond("approve"))`.
- 저장한 응답은 명시적인 `task.run.resume(run.id, engine=run.engine)`에서 사용한다.
- 응답은 Run.state/interactions에 원자적으로 저장한다. 기존 호스트 ToolPolicy 권한은 유지한다.
- 요청 버전/지문, 응답 충돌, 만료(큐 실행 시작 포함), 입력 JSON Schema를 검증한다.
- 승인 요청 UI 정보와 ProjectConfig 자동 승인 정책 해석기는 별개다. 자동 승인 규칙은 추가하지 않았다.
- 상세 계약과 UI 예시는 `docs/llm/interactions.md`를 참고한다.

검증: Linux Python 3.12.14 전체 unittest 708개 통과 (141.890초, skip 없음).
로그: `tests/llm/reports/history/interactions-final-suite-python312.txt`. 최종 소스와 테스트 스냅샷 SHA-256 일치.
