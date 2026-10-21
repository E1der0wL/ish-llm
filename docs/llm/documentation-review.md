# main 문서·코드 대조와 갱신 기록

이 문서는 문서 최신화 시점의 기록이다. 이후 host 수정·P0 검증 도구·회귀 결과는
[P0 실행 기록](p0-verification.md)에 별도로 남긴다.
2026-10-21 `docs/` 전체 재검토와 H2-a 문서 갱신은 아래 후속 기록 및
[수명주기 검증](lifecycle-verification.md)을 따른다. 아래 main 기준 수치를 현재 브랜치의
회귀 결과로 승계하지 않는다.

기준은 `94229cb865374d99f20ec48c5e48269488041a4e`, 검토일은 2026-10-20이다.
작업 브랜치는 `refactor/llm-clean`이며 기존 backend 브랜치의 제품 변경을 가져오지 않았다.
이번 단계는 **설계·사용 문서 갱신**이다. 제품 코드·테스트·CI·제품 프롬프트/스킬은 변경하지 않는다.

## 범위와 증거

[전체 목록](../document-map.md)의 기존 118개 문서, 21,910행을 읽었다. `docs/` 79개,
`llm/` 29개, `hub/` 10개이며 제품 자산 9개도 검토 범위에 포함한다. 코드 블록을 제외한
870개 절, 기존 Python 예제 175개, 상대 파일 링크 547건을 색인했다. 기존 예제는 모두
AST 문법 파싱에 성공했지만 일부 설정 예제는 실제 validator에서 거부됐다. 문법 성공을
예제 실행 성공으로 보고하지 않는다.

이전 main 감사의 제품 Python 377개/47,490행, 테스트·예제 포함 627개 AST와 호출식 23,518건,
Schema·Reader/Writer·실제 호출 경로·회귀 결과를 문서와 대조했다. 문자열/AST 후보가 모두
위반이라는 뜻은 아니며 각 owner와 실행 의미로 판정했다. 아래 표는 구체적으로 확인한 차이다.
동적 플러그인·실모델·모든 문장까지 실행 검증했다는 주장은 하지 않는다.

## 문서 충돌의 정리

| 충돌 | 적용할 계약과 이번 처리 |
|---|---|
| Host 마지막 override / Project 권한 | [Project authority](project-authority.md)·[CONFIGURATION](../../llm/CONFIGURATION.md)의 현재 계약 유지. Host 기술 deny·자원과 실행 정책 구분 |
| 열린 ProjectConfig/정의 / 닫힌 Schema | [Schema ownership](schema-ownership.md) 유지. metadata·SDK·선택 handler 인자만 선언된 owner가 검증하는 확장 영역 |
| 출력 두 파일 비원자 / 현재 transaction | [저장 트랜잭션](storage-transactions.md)을 정규 참조로 통일. 외부 Tool 효과는 이 원자성 밖에 있음 |
| Step 완료와 checkpoint 분리 / 첨부 receipt 함께 확정 | STEP_COMPLETED에 첨부된 receipt는 함께 확정; 별도 CHECKPOINT 이벤트는 자기 저장 단위. 성공 알림은 commit 뒤 |
| 기본 로그 회전·RAG 배치·Memory 자동 정책 / 명시 설정 | 오래된 수치를 현재 기본값으로 복원하지 않고 예제/설명을 수정 |
| AgentComponent·옛 지문 보존 / B/C 직접 전환 | Naming의 의미 규칙 보존, 기존 API를 영구 보존하는 예외 제거. 현재 API와 [미구현 목표](clean-refactor.md) 구분 |
| 명시 backup upgrade 허용 / 출시 전 호환 금지 | 실제 main API의 잔존 사실은 기록하고 P1 제거 대상으로 지정. 현재 backup/restore 유지 |
| Hub 기본 classifier 없음 / 모델 위험 평가 연결 | 실제 bootstrap 동작과 모델 scheme을 명시. 분류는 승인 권한이 아님 |
| 옛 키 조작·편집 UX / 전용 현행 UX 문서 | 큰 Hub README 이력은 [history](../hub/history.md)에 보존하고 현행 진입점은 전용 문서로 연결 |
| Schema default 금지 / Tool Python 인자 default | 전자는 설정 생성 금지, 후자는 Tool 작성자의 명시 인자 계약. 일괄 삭제하지 않음 |
| config/policy 객체 null 금지 / nullable leaf 허용 | envelope와 leaf의 owner 계약을 구분. Null을 빈 객체로 바꾸거나 모든 Null을 일괄 거부하지 않음 |
| 일반 Facade·sync/async·실제 SDK Adapter / 호환 wrapper 금지 | 현재 역할과 구 API 변환을 구별. 정규 공개 경계나 외부 프로토콜 연결 자체를 삭제하지 않음 |

이전 대형 아키텍처/API/운영 준비/Hub 문서는 같은 디렉터리의 history 문서에 원문을 보존했다.
review/audit/handoff 등 과거 기록에는 상태 표지를 추가했다. 날짜별 시험 수는 현재 결과로 승계하지 않는다.
설정·명명·저장·안전성의 정규 문서를 링크하며 여러 문서에 새로운 정책 선언을 복제하지 않는다.

## 감사표: 문서·파일·심볼·처리 상태

**문서 수정 완료**는 제품 결함 해결을 뜻하지 않는다. P0와 P1-a는 검증을 완료했고,
P1-b는 아래 항목별 상태와 [실행 기록](p1-verification.md)을 따른다. P1-c와 A–F는 미완료다.
파일 경로는 저장소 루트 기준이다. 구체적인 선언 책임과 gate는 [구현 계획](clean-refactor.md)에 있다.

| ID | 문서 근거 | 소스·심볼·확인 증거 | 처리/남은 작업 |
|---|---|---|---|
| D01 | CONFIGURATION의 Missing/Null/Value | `llm/core/configuration.py`의 `resolve_config`, `llm/services/runtime/tools.py`의 `ToolExecutionScope`에서 `or {}` 사용; falsey 객체 수락 관찰 | **P1-a 검증 완료**. 해당 설정·scope 경계는 타입 검사 후 presence 처리; False/0/[]와 nullable leaf 구별. 다른 owner의 fallback 정리는 P1-c |
| D02 | Schema·현재 저장 형식 | `llm/services/runtime/checkpoints.py:CheckpointRepository.load`, `llm/services/runtime/operations.py:OperationRepository.load`, TestWorkspace/Vision/Preferences Reader의 bool/float 버전 수락 관찰 | **P1-a 검증 완료**. owner별 정확한 타입·버전 검사와 current roundtrip·실패 원본 보존 시험 통과. 추가 선언 정리는 P1-c |
| D03 | 명시 실행 정책·단일 선언 | `llm/components/tools/registry.py:ToolContract`, `llm/components/tools/decorator.py`의 revision/effect/isolation/approval/operation-key 기본 선언 반복 | **P1-c 해당 계약군 검증 완료**. 필수 ToolContract·effect/isolation owner와 명시 등록으로 단일화. LLM 1,707·Hub 540개 통과; 실패·수정 근거는 [P1 기록](p1-verification.md) |
| D04 | 부모 Tool scope·권한 비상승 | `llm/services/runtime/tools.py`, `llm/engines/agents.py`에서 scope 없으면 `ToolExecutionScope(ToolRuntime())` 생성 | **P1/C 미수정**. 정규 Run 경로의 scope 필수, trusted 직접 helper 경계 별도. 정상 Run 승인 우회를 재현한 결과는 아님 |
| D05 | 구현체별 Schema/설정 소유 | Loop layout/option 목록/Schema, effect/isolation 목록, 저장 버전 Reader·Writer의 반복 선언 | **P1-c 일부 검증 완료**. Tool effect/isolation은 owner enum으로 전환. 나머지 layout/Schema·상태·버전 정리는 남음; 전역 constants 집합이나 후보 일괄 삭제 금지 |
| D06 | 구 저장 형식 금지 | `hub/config/preferences.py`가 mouse_mode/recovery_max_attempts/provider_error_details를 제거; `test_global_preferences`가 변환 요구 | **P1-b 검증 완료**. 옛 키를 명시 거부하고 실패 원본 보존 시험으로 전환 |
| D07 | 구 저장 형식 금지 | `hub/config/theme.py:HubTheme.from_preferences`가 accent→accent1 변환·옛 widget 키 제거; 실제 수락 재현 | **P1-b 검증 완료**. 변환 Reader 제거, 현재 UI 파생 색상 property 유지 |
| D08 | Hub 언어 설정 | `hub/config/preferences.py`의 general→profile→config 순서; legacy profile 시험 | **P1-b 검증 완료**. 완전한 현재 profile만 읽음. Hub 생성 초기값 저장과 명시 앱 초기 언어 선택은 유지 |
| D09 | Hub 요청 환경·승인 binding | `hub/backend/execution/environment.py:EnvironmentEngine.for_request/bind_context`의 환경 누락 fallback | **문서 수정 / C 미수정**. ish 캡처·독립 실행 명시 환경, cwd/env/Tool revision 보존 |
| D10 | 관찰 구독 계약 | `llm/llm.py`, `llm/services/runtime/runs.py`의 직접 on_event/on_run_event와 EventSubscriptions 이중 경로 | **P1-b 검증 완료**. 실제 소비자를 구독 API로 직접 전환. durable EventHandlers의 실행 계약 유지 |
| D11 | 백업/복원과 구 형식 | `DirectoryBackups.upgrade`, `ProjectManager.upgrade_backup`, 공개 Projects 경로 | **P1-b 검증 완료**. upgrade 제거; checksum·Component 검증·staging·현재 backup/restore/clone 유지 |
| D12 | Provider·Processor 계약 | `llm/engines/loop/engine.py:execute`, `llm/components/processing.py`, Tool 정의·worker·RAG·Vision의 canonical dict | **A 미구현**. 공통 typed 모델과 실제 Adapter로 소비자 전환; SDK opaque/usage·pair 유지 |
| D13 | 재개 증거·불확실 효과 | `LoopEngine.validate_resume`가 requires_retry 누락 시 tool: 키로 추론; started 레코드가 키에 따라 수락/거부됨 | **P1-b 검증 완료**. started Loop의 container/requires_retry 필수·정확한 bool 검증. 키 이름 추론 제거, 실제 효과 중복 방지 시험 통과 |
| D14 | Naming 의미·공개 API | `llm/engines/registry.py`, `llm/components/agents`, Loop binding, `test_naming_contract`의 옛 Schema digest 고정 | **문서 수정 / B/C 미구현**. 의미 규칙 보존, 구 API·옛 지문 지원 의무 제거; 현재 계약 시험으로 직접 전환 |
| D15 | long-running의 provider 예제 | `LoopEngine.resolve_config`가 평면 provider 거부, policy.provider 수락 | **문서 수정**. 정규 경로 사용; 변환기 추가 없음 |
| D16 | Prompts README | `RAGComponent.parameter_layout/validate_config`, `PromptComponent.schema`, `TripleExtractor.extract` | **문서 수정**. config.extraction.prompt_id·policy.extraction.repair_attempts·metadata. null/누락은 추가 Prompt 없음 |
| D17 | Memory 사용법 | `MemoryComponent.validate_config/search`, `MemoryData._tool_write`; config.ui 거부·검색 전략 필수 | **문서 수정**. 닫힌 content·명시 검색/쓰기·필수 처리 설정; CRUD 초기 형식과 실행 정책 구분 |
| D18 | RAG 장기 작업 | `RAGJobs`의 max_active 명시 적용, RAGComponent의 옛 embedding_batch_size 거부·청크별 embedding | **문서 수정**. 명시 concurrency/extraction_batch_size/cache; 옛 배치 Reader 추가 없음 |
| D19 | 출력·완료·receipt 저장 | TransactionManager와 STEP_COMPLETED 첨부 checkpoint 저장, `test_execution_boundaries`/`test_transactions` | **문서 수정**. 저장 ACK·rollback·commit 후 알림; 외부 효과 불확실성 유지 |
| D20 | 로그 회전 | `llm/services/infrastructure/logging.py:LogConfig`의 크기/개수는 None, 명시 조합만 회전 | **문서 수정**. 옛 1 MiB/3개 설명은 history로 보존; 숨은 운영값 복원 없음 |
| D21 | Hub 위험 분류 | `hub/backend/bootstrap.py`, `hub/backend/tools/advice.py:classify_model_risk`, hub-model-risk-v1 | **문서 수정**. trusted hub-risk-v1과 구분; 모델 위험 설명은 승인 증거 아님. 앱 선택 단일 선언은 P1 |
| D22 | 닫힌 설정·자료 | `ProjectConfig`, 내장 Component Schema와 명시 metadata | **문서 수정**. 열린 SDK/metadata와 backend 필드 구분 |
| D23 | 공통 정책 목록 | `llm/core/policies.py:describe_policies`, ToolExecutionScope | **문서 수정**. CONFIGURATION 목록에 tools 보완 |
| D24 | 격리·CI 필수 gate | main 감사 시 `.github/workflows/tests.yml`의 bwrap 실패 뒤 Skip; 32 LLM 모듈만 선택 | **P0 검증 완료**. 전체 ID 배정·Skip 실패 처리, bwrap에 한정한 AppArmor 설정과 전역 제한 유지. 후속 P1-b 원격 두 job도 전체 통과; [실행 기록](p1-verification.md) |
| D25 | 실제 ish host 계약 | main의 `_library_available`; 실제 host의 `_is_library_available` | **P0 검증 완료**. assertion을 보존하고 실제 host로 수정 전/후 확인. P1-b 전체 회귀도 host 시험 포함 |
| D26 | 결과 조회·unknown usage | `llm/core/results.py:CompletionResult.for_run/ExecutionResult.from_run` | **유지**. 현재 취소/실패 관찰 투영은 legacy migration이 아님; unknown usage를 0으로 바꾸지 않음 |
| D27 | Processor hook | CompletionPipeline의 after_completion 관찰/finish; review_final 없음 | **목표 문서화 / F 미구현**. 별도 verdict·예산·checkpoint, 관찰자를 재실행 권한으로 바꾸지 않음 |
| D28 | 외부 불변식·기술 자원 | SDK retry guard/cost map/stream/n/no-cache, IPC 크기, CRUD 초기 상태 | **유지·owner별 정리**. default/compatibility라는 이름만으로 제거하지 않음 |
| D29 | 현재 Agent·1:1 실행 | AgentComponent의 purpose/engine, Session.current_run_id, Message.run_id, Run.engine | **목표 문서화 / B–D 미구현**. 독립 Agent·단일 binding·1:N/FIFO/실제 병렬, 현행과 목표 구분 |
| D30 | Hub 조회·복구·렌더링 | worker 소유 loop, immutable snapshot, resume_plan/restart, journal 조회 | **유지·C/D 직접 이관**. UI 단일 active ID를 권한 원본으로 삼지 않음 |
| D31 | Hub 생성 시에만 초기값·사용자 빈 목록 우선 | `SettingsService.create_project/save_all`이 components를 덮어씀; `HubBuiltinTools.ensure_selected`가 재열기 때 Component·enabled 저장 | **P1-c 첫 계약군 검증 완료**. 누락 필드만 생성 초기화. 재열기 쓰기 경로 제거·비활성 Tool 실제 효과 0·전체 2,240개 통과; [기록](p1-verification.md) |
| D32 | 현재 형식만 읽는 preset | `PresetStore.validate/read`의 version 중복·미선택 본문 미검증·빠진 전역 설정 복구 | **P1-c 첫 계약군 검증 완료**. owner 버전·PreferencesStore 필드 검증 공유. 전체 저장 preset 검증·원본 보존 |
| D33 | 명시 Provider 설정·호스트 자원 | `resolve_provider_options`의 복사 전 변환, `ProviderCalls`/counter registry/Memory의 falsey fallback | **해당 계약군 구현·검증 완료**. 잘못된 타입 거부, nullable 해제·0 대기 보존, RetryPolicy의 최초 호출 계산 공유. 기존 작업을 `7b1120b`로 보존 |
| D34 | nullable 해제와 parent narrowing | CONFIGURATION의 provider=null 해제 설명만 읽으면 Session에서 상위 max_attempts 한도를 제거해도 된다고 오해할 수 있음 | **문서 명확화·집중 검증 완료**. 현재 narrowing 거부를 유지하고 한도 없는 상위 정책의 해제와 구분 |
| D35 | 현재 승인 요청 Reader의 필수 필드 | `InteractionRequest.from_dict`가 expires_at 누락을 None으로 채움. 만료된 요청을 복사해 필드를 제거하면 expired=False로 읽힘 | **H1 승인 보강·검증 완료**. 현재 형식 Reader가 누락을 거부하며 추가 Run·모델 호출·파일 효과 없음. [수정 전후 기록](lifecycle-verification.md) |
| D36 | 영속 자료 owner·손상 원본 보존 | 승인 응답 파일 소실·취소 필드 누락 시 pending, Memory/RAG identity 소실 시 조회 중 재생성 | **승인 필수 상태·RAG identity는 H1 보강·검증 완료**. Memory identity와 owner별 격리/삭제는 남음. [수명주기 계약](data-lifecycle.md) |
| D37 | 삭제로 예산·원본 초기화 금지 | SessionManager.delete 뒤 같은 Project 호출량 1→0과 추가 호출; RAG active.json 소실 후 compact가 실제 세대 삭제 | **H1 포인터·Session quota 보호 검증 완료**. 현재 Project와 활성 Run 정책 사본의 증거를 함께 보호. 상위 삭제·전체 참조 보호는 H2–H5에 남음. [수정 전후 기록](lifecycle-verification.md) |
| D38 | 복구와 손상 폐기 구분 | ConversationStore._repair_tail가 newline이 사라진 기존 메시지를 다음 append에서 제거 | **미수정**. 입증된 미확정 쓰기의 undo만 허용, 불명확한 원본 절단 금지 |
| D39 | owner별 백업·참조 검증 | 손상 승인 envelope의 backup 수락 재현; Agent.references의 Prompt/MCP 참조 누락은 소스 대조 | **승인 backup 검증 연결 완료**. 나머지 owner validators/실제 참조 보강은 남음. 중앙에서 하위 형식 해석 금지 |
| D40 | Application 보존·대상 선정, Backend 안전한 삭제 | HistoryRetention/RAG maintenance·publish/로그 회전의 선정·시점이 Backend에 남음 | **계약 명시·이관 미완료**. 기술적 transaction GC와 분리, 호환 wrapper 없이 직접 이관 |

D16의 추가 대조: 고정 `EXTRACTION_CONTRACT`와 사용자 의미 지침 `default_prompt()`는 다르다.
`prompt_id=null` 자체는 유효하며 별도 Prompt가 없다는 뜻이다. 이를 필수 Prompt 누락 결함으로
분류하지 않는다. Memory의 의미 추출 prompt 필수 계약과 RAG의 이 선택 기능도 합치지 않는다.

## 검증 기준선과 남은 실패

아래 전체 회귀는 **앞선 동일 main 감사에서 실제 실행한 결과**다. 이번 문서 변경 뒤 재실행한
결과로 표시하지 않는다. Python 3.12.14, 외부 참조 `ish.platform/src/ish`, host SHA
`d833790959a76ac457fdeea3f21a6bea1eecfe12`를 사용했다. 외부 host는 커밋 대상이 아니다.

| 범위 | 실행 | 통과 | 실패 | 오류 | Skip | exit |
|---|---:|---:|---:|---:|---:|---:|
| LLM 전체 | 1,635 | 1,634 | 1 | 0 | 0 | 1 |
| Hub 전체 | 534 | 534 | 0 | 0 | 0 | 0 |
| host 3개 모듈 재실행 | 44 | 43 | 1 | 0 | 0 | 1 |

host 44개는 전체 집합의 부분집합이다. 고유 전체 2,169개 중 2,168 pass / 1 fail이다.
실패는 `PluginEntryTests.test_real_platform_loader_and_fresh_worker_import`의 위 D25이며 미수정이다.

```bash
# 실제 사용한 전체 기준선 명령. X11 도구 경로는 당시 Cloud 환경 경로다.
export TERM=xterm-256color
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
.venv-linux312/bin/python -m unittest discover -v -s tests/llm -t .
.venv-linux312/bin/python -m unittest discover -v -s tests/hub -t .
.venv-linux312/bin/python -m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration
```

Xvfb/xclip 관련 9개는 위 Hub 기준선에 포함되어 실제 통과했다. 실제 사용자 데스크톱이나
GitHub runner 검증을 뜻하지 않는다. 실제 인증 모델, 커널 short-write 장애 주입은 미검증이다.

## CI 조사와 복구 계획

[main run 39079942891](https://github.com/E1der0wL/ish-llm/actions/runs/39079942891)과
[기존 PR run 39329566513](https://github.com/E1der0wL/ish-llm/actions/runs/39329566513)의
sandbox 설치/smoke 단계 실패와 후속 단계 Skip을 확인했다. 원시 로그 다운로드는 Azure blob
Forbidden이어서 AppArmor/seccomp/userns/runner 정책 중 정확한 원인을 확정하지 못했다.
이전 보고서의 RTM_NEWADDR 오류는 참고 증거이며 새로 확보한 원시 로그가 아니다.
로컬 bwrap 0.12.0 smoke 성공을 GitHub 복구 성공으로 바꾸지 않는다.

현행 workflow의 LLM 선택 32개 모듈은 531개 테스트에 해당하며 전체 discovery 1,635개 중
1,104개가 미배정이다. Hub는 전체 534개를 선택한다. P0에서 다음을 수행한다.

1. 실제 실패 명령/exit·runner image/kernel/bwrap 버전·namespace/security profile·원시 로그를 확보한다.
2. 제품과 같은 명시 network/mount/capability 조건으로 실패를 재현하고 설치/runner 지원 문제를 구분한다.
3. core·실제 host/X11·실제 sandbox job을 분리하되 모두 필수 gate로 두고 발견된 모든 test ID를 배정한다.
4. network deny·파일 경계·승인 전 spawn 0·프로세스 종료·효과 후 crash를 실제 runner로 검증한다.
5. 비격리 fallback, network 공유, 전역 보안 해제, Skip 확대, continue-on-error는 사용하지 않는다.

이번 단계에서는 workflow를 수정하거나 CI 복구 성공을 주장하지 않는다.

## 다음 구현 파일과 중단 조건

[전체 단계 계획](clean-refactor.md)은 P0 → P1-a/b/c → A → B/C → D1/D2 → E → F다.
다음 단계의 실제 변경 대상은 `tests/llm/test_plugin.py`, `.github/workflows/tests.yml`,
설정 감사 규칙 및 관련 테스트다. 이어 `llm/core/configuration.py`,
`llm/services/runtime/{tools,checkpoints,operations}.py`, 해당 저장 owner와
`hub/config/{preferences,theme}.py`를 작은 검증 단위로 정리한다.

R3 `max_tool_calls=1`은 root/nested·승인→Resume·재시작을 통과해도 첫 Tool 실제 효과 1회,
두 번째 handler 진입/효과 0회여야 한다. 전달을 의도적으로 누락한 mutation에서 시험이 실패해야 한다.
새 R3 시험과 A–F 기능은 아직 구현하지 않았다. 승인 우회·한도 소실·Tool 중복 실행·자료 손실 위험·
미해결 핵심 회귀가 있으면 다음 구현 단계로 진행하지 않는다.

## 첫 문서 최신화 단계에서 실행한 검증

Python 3.12.14에서 다음을 실행했다. 제품 코드 변경이 없어 전체 LLM/Hub를 반복 실행하지 않았으며,
위의 기존 전체 실패 1건을 이번 집중 통과 결과로 덮지 않는다.

```bash
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  .venv-linux312/bin/python -m unittest -v \
  tests.llm.test_definition_components tests.llm.test_explicit_configuration \
  tests.llm.test_settings_contract tests.llm.test_schema_ownership

# 작업 공간 밖에 둔 이번 문서 검증 도구; 제품/테스트 소스로 커밋하지 않음
LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  .venv-linux312/bin/python /workspace/llm-docs-refresh/run_examples.py
.venv-linux312/bin/python /workspace/llm-docs-refresh/check_docs.py
git diff --check
```

| 검사 | 실제 결과 | 검증 범위 |
|---|---|---|
| 기존 집중 회귀 | 74 pass / 0 fail / 0 error / 0 Skip, 6.092초, exit 0 | 정의 문서 실행·명시 설정·설정 전달·Schema owner |
| 수정 예제 실행 | 9 pass, exit 0 | Echo Run·Memory CRUD/CAS·Prompt CRUD·RAG 설정·Loop/Memory/retention/Tool 정책 설정 |
| 문서 정적 검사 | 125개 문서, Python fence 178개 문법 오류 0, exit 0 | 현재 118개와 새 7개 문서; 모든 예제를 실행했다는 의미는 아님 |
| 상대 링크·절 | 파일 링크 859건, Markdown anchor 21건, 잔여 누락 0 | 링크 수는 중복 출현 포함. 외부 URL 접속/GitHub 렌더링 전체를 검증하지 않음 |
| 원문 보존 | 4개 history 본문의 바이트가 main 원문과 동일 | 추가한 과거 기록 안내를 제외한 원문 비교 |
| 제품 보존 | Python 627개+제품 자산 9개, 총 636개 SHA-256 동일 | 제품 구현·테스트·런타임 자산 변경 없음 |
| diff 공백 검사 | exit 0 | Git diff 검사 |

첫 링크 검사는 과거 API 문서의 웹 조회 예제 anchor 1건 때문에 exit 1이었다. 원문을 고치지 않고
현행 builtin-tools 안내에 해당 절과 현재 실행 안내를 제공한 뒤 통과했다.
예제 검증기의 첫 실행은 4 pass/5 fail이었다. Memory 예제가 닫은 backend 핸들을 검증기가
후속 예제에 재사용한 오류였으며, 검증기의 namespace 수명을 분리한 뒤 동일 문서로 9개가 통과했다.
제품이나 assertion을 바꾸어 처리하지 않았다.

검증 도구·JSON·집중 로그는 작업 공간의 `/workspace/llm-docs-refresh/`에 보존한다.
이 디렉터리는 저장소 밖의 작업 증거이며 clone만으로 제공되는 제품 파일이 아니다.
RAG embedding/추출·Memory 요약·retention 삭제를 실제 실행한 결과는 아니다.
새 `max_tool_calls=1` 예제의 설정 전달 확인도 **R3의 실제 두 번째 Tool 효과 차단 시험을 대신하지 않는다**.
실제 승인·Resume·새 A–F·CI·인증 모델·kernel short-write의 미검증 상태는 위 계획대로 유지한다.

## 후속 정정: Resume의 Tool 예산

첫 문서 커밋 `f7dad4c`에는 runtime-reliability의 “새 Run이므로 새 예산”이라는 충돌 문장이
남아 있었다. 이를 정정하고 long-running에 실행 수명·모델 사용량·공통/Agent Tool 한도·
개별 응답 Tool 개수·조건부 retry의 owner와 복원 범위를 구분했다. 완료 결과 재사용과 사용량 복원,
상한 전달은 각각 검증해야 하며 현재 정책·binding과 이전 승인/효과 영수증도 따로 대조한다.
제품 구현은 변경하지 않았다.

Python 3.12.14에서 아래 기존 집중 시험 **5 pass / 0 fail / 0 error / 0 Skip**, 2.390초, exit 0을 확인했다.

```bash
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  .venv-linux312/bin/python -m unittest -v \
  tests.llm.test_agent_workflow.AgentWorkflowTests.test_require_tool_and_agent_call_budget_are_enforced \
  tests.llm.test_nested_graph.NestedGraphTests.test_loop_agent_approval_reopen_preserves_tool_receipts_and_budget \
  tests.llm.test_nested_graph.NestedGraphTests.test_graph_agent_pause_reuses_tool_receipt_under_parent_run_policy \
  tests.llm.test_nested_graph.NestedGraphTests.test_graph_agent_explicit_retry_uses_new_runs_parent_budget \
  tests.llm.test_retry.ToolRetryTests.test_failed_repair_budget_cannot_reset_on_explicit_resume
```

`test_graph_agent_explicit_retry_uses_new_runs_parent_budget`라는 기존 시험명은 예산 초기화의 근거가
아니다. 동일 invocation의 명시 재시도와 새로운 논리 호출을 구분해야 한다. 새 R3의 한도 1·
재개 뒤 새로운 두 번째 효과 차단·상한 전달 누락 mutation을 위 시험들로 대체하지 않는다.
원본 로그는 작업 공간 `/workspace/llm-docs-clarifications/resume-tests.log`에 보존한다.
전체 회귀·실인증 모델·kernel short-write는 이번 후속 문서 작업에서 실행하지 않았다.

## 후속 명확화: Hub가 소유한 생성 초기값

사용자의 추가 원칙에 따라 [Hub 초기값 계약](../hub/presets-and-resume.md#hub-초기값의-소유권과-저장-계약)을
정규 기준으로 명시했다. Hub 기능/프리셋 owner의 단일 선언 → 새 Project의 누락값에 적용 →
Backend 검증 → 실제 저장·조회·편집을 허용한다. 매번 수동 선택을 요구하지 않으며,
사용자의 유효한 0/False/None/빈 목록을 보존한다. 기존 Project 재열기/실행 때 재주입하지 않고
사용자가 선택한 명시적 프리셋 재적용은 별도 설정 편집으로 검증한다.
Backend는 Hub 프리셋 이름이나 선택 규칙에 의존하지 않는다. 안전성 검증은 초기값에도 동일하다.

CONFIGURATION의 “default 출처 없음”은 Backend에 숨은 해석 계층이 없다는 뜻으로 좁혔다.
저장된 Hub 초기값은 일반 Project 출처이며 별도 Backend preset 계층을 만들지 않는다.
클린 설계의 정적 규칙 S2와 P1-c gate도 합법적인 생성 프리셋을 위반으로 오판하지 않도록 수정했다.
허용되지 않은 False/null 등을 유효값으로 바꾸라는 요구가 아니며 필드 owner의 Schema를 따른다.

Python 3.12.14에서 아래 기존 시험 **12 pass / 0 fail / 0 error / 0 Skip**, 13.716초, exit 0을 확인했다.

```bash
TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION \
  .venv-linux312/bin/python -m unittest -v \
  tests.hub.test_bootstrap \
  tests.hub.test_response_reliability.ReliabilityTests.test_creation_policy_is_editable_and_existing_projects_are_not_reseeded \
  tests.hub.test_presets_widgets.PresetBackendTests tests.hub.test_project_authority
```

로그는 `/workspace/llm-docs-clarifications/hub-preset-tests.log`에 보존한다. 이는 모든 필드의
생성/편집/재열기 M/N/V 조합을 새로 검증했다는 뜻이 아니며 그 전수 gate는 P1-c에 남긴다.
후속 문서 125개의 Python fence 178개는 문법 오류가 없고 상대 파일/절 링크의 잔여 누락은 0이다.
제품 Python 627개와 실행 자산 9개의 SHA-256, 과거 본문 보존 4개도 동일하며 `git diff --check`가 통과했다.
검사 도구·JSON은 `/workspace/llm-docs-clarifications/`에 보존한다. 제품/테스트/CI 구현 변경은 없다.

## 2026-10-21 전체 docs 재검토와 H2-a 갱신

사용자의 요청에 따라 `docs/`의 91개 Markdown 전문을 읽었다. 루트 2개, Hub 26개,
LLM 63개이며 읽기 시작 snapshot은 1,537,531 bytes다. 114개 파일/행 구간으로 나누어
끝까지 검토하고 이후 편집 전 원본 hash가 모두 같음을 확인했다. 목록·파일별 SHA-256,
전문 구간·읽은 구간·검토 메모는 `/workspace/llm-clean-resume/docs-review/`에 보존한다.
과거 요구·작업·검증 기록도 읽었으나 현재 구현의 호환성·기본값·Skip·게시 권한으로 해석하지 않는다.

적용 순서는 사용자 지침 → 현행 수명주기·명명·명시 설정·소유권 → A–F 기능/안전성 →
과거 기록이다. H2-a 전체 회귀가 통과한 뒤 다음 문서를 우선 갱신했다.

| 문서/계약 | 반영 내용 |
|---|---|
| lifecycle-verification, data-lifecycle, clean-refactor | H2-a 실제 집중/전체 결과와 소스 hash, 원장 소실의 별도 실패 2건, H2–H5/P1/A–F 미완료 구분 |
| storage-transactions, conversation-storage | 근거 없는 Conversation 끝줄 절단 제거와 원본 보존; 입증된 transaction undo 유지 |
| memory, Refinement README | identity 발급/조회 분리와 필수 이력·평가 컨테이너·상태/receipt 일치 |
| project-settings | 부모의 유한한 timeout을 Session null로 해제한다는 오래된 예제를 수정; 현행 narrowing 계약과 일치 |

출력 저널의 tail 무시, 일부 오래된 UI 설명, test-workspace missing-record 삭제와
상위 참조/효과 보호는 현재 코드·별도 owner 감사와 대조할 잔여 항목이다. 문서 검토가
모든 예제의 실행이나 이 항목들의 해결을 뜻하지 않는다. H2의 추가 원장 실패를 전체 suite
성공으로 덮지 않으며 실제 인증 모델·전원 손실·네트워크 파일시스템은 미검증이다.

## 2026-10-21 H2-b 검증 후 문서 우선 갱신

외부 작업 원장의 필수 manifest·파일 목록·리비전·상태별 증거를 보강하고 전체 LLM/Hub
회귀가 통과한 뒤 lifecycle-verification, data-lifecycle, clean-refactor, process-isolation,
storage-transactions를 먼저 갱신했다. H2-a에서 발견한 원장 두 실패는 보강된 범위로 기록하고
원래 실패 수·로그를 보존했다. 새 Checkpoint 파일 소실 후 중복 효과는 별도 미해결 항목이며
H2/P1/A–F 완료로 표시하지 않는다. 문서 재검토 기록은 위 91개 전문 검토를 이어간다.

## 2026-10-21 H2-c 검증 후 문서 우선 갱신

Checkpoint 필수 파일·리비전·현재 Run 조회 보강의 전체 LLM/Hub 회귀가 통과한 뒤
lifecycle-verification, data-lifecycle, clean-refactor, checkpoints, graph-checkpoints,
storage-transactions를 먼저 갱신했다. 변경 노드만 쓰던 설명을 manifest/Run 리비전의
원자 갱신으로 고쳤고 추가 I/O의 대용량 운영 비용은 미측정으로 남겼다. H2-b 때의
Checkpoint 실패 기록은 보존하고, 출력 저널의 별도 실패 3개와 H2–H5 잔여 작업을 구분했다.

## 2026-10-21 H2-d 검증 후 문서 우선 갱신

출력 원본·필수 manifest·현재 출력 필드 보강의 집중/전체 회귀가 통과한 뒤
lifecycle-verification, data-lifecycle, clean-refactor, engine-output, storage-transactions,
operational-storage를 먼저 갱신했다. 과거 끝줄 무시 설명을 제거하고 검증된 원본에서만
파생 인덱스를 재생성하도록 고쳤다. 원본 해시의 추가 I/O와 대용량 비용 미측정도 명시했다.
첫 전체 실패와 ENOSPC 주입 경계 수정, Hub 이후 예제 하나만 바뀐 소스 증거를 보존했다.
출력 3개 실패의 해결과 별도 Component 4개 실패·나머지 H2–H5 작업을 구분했다.

## 2026-10-21 H2-e 검증 후 문서 우선 갱신

Component 저장소 이력·사용량 영수증 보강의 전체 LLM/Hub 회귀가 통과한 뒤
lifecycle-verification, data-lifecycle, clean-refactor, operations-and-ui-settings,
storage-transactions와 Component README를 먼저 갱신했다. 선택 해제와 저장소 생성 이력을
구분하고 비활성 owner의 백업·quota 보호, 필수 manifest와 현재 영수증 필드를 명시했다.
무제한 호출에서도 수행하는 저장 원본 검증과 quota 합산 생략을 구분했다. 기존 테스트 자료의
실패·수정과 성능 예제의 누락된 소비 경로도 보존하며 소규모 smoke를 성능 개선으로 주장하지 않는다.
기존 4개 실패의 해결과 새 개별 Component 기록 소실 2개 실패·나머지 H2–H5 작업을 구분했다.

## 2026-10-21 H2-f 검증 후 문서 우선 갱신

개별 Component 원본·목록 보강의 전체 LLM/Hub 회귀가 통과한 뒤 lifecycle-verification,
data-lifecycle, clean-refactor, storage-transactions, Memory 문서와 Component README를
먼저 갱신했다. 원본 목록의 필수 존재와 원자 저장, 캐시 전 검증, File 패키지와 Vision 삭제
표식의 소실 차단을 기록했다. 초기 집중 실패와 fixture 수정, 의미 검증의 독립성을 보존했다.
H2-e의 두 개별 기록 실패 해결과 별도 Tool 패키지 실패·나머지 H2–H5 작업을 구분했다.


## 2026-10-21 검증 실행 정책 정리

사용자의 최신 지침을 [검증 정책](verification-policy.md)의 단일 선언으로 반영했다.
clean-refactor·data-lifecycle의 매 소단계 전체 회귀/Push 표현을 Level 1–3와 묶음 게이트로
바꾸고, lifecycle-verification·p1-verification의 과거 결과와 당시 지침은 그대로 보존했다.
storage-transactions에는 원자성·장애 주입 계약 유지와 영향 불명확 시 승격 조건을 연결했다.

변경 owner는 tests.llm.run_regression이다. 기존 --suite의 전체 발견·실행 경로를 유지하고
--modules가 발견된 정확한 모듈 전체만 선택하게 했다. 빈·중복·미발견 모듈과 discovery 오류를
먼저 거부한다. 부분 실행의 available/discovered/started와 scope를 보고하며 기존 엄격한 결과
집계를 공유한다. 실제 CLI 부분 실행과 기본 전체 경로, 누락된 import 오류 차단, 잘못된 선택,
unexpected success 거부 시험 5개를 추가했다. CI workflow·제품 실행·저장 계약은 이 정책 변경에
포함하지 않았다. 동시에 진행 중인 H2-g 제품 변경은 별도 검증 범위다.

Level 2 검증은 검사 도구의 직접 소비자와 공통 안전성 12개 모듈을 선택했다. 작업 경로는
/workspace/ish-llm, Python은 .venv-linux312/bin/python 3.12.14, host는
 d833790959a76ac457fdeea3f21a6bea1eecfe12다. 고정 lock과 기존 격리 조건을 유지했다.
TERM=xterm-256color, LITELLM_LOCAL_MODEL_COST_MAP=True, LITELLM_MODE=PRODUCTION,
HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb,
HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip으로 실행했다.

```sh
.venv-linux312/bin/python -m tests.llm.run_regression --suite all --modules tests.llm.test_regression_runner tests.llm.test_documentation_checks tests.llm.test_architecture_checks tests.llm.test_checks_output tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_nested_graph tests.llm.test_transactions --output /workspace/llm-clean-resume/verification-policy/focused.json
.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --output /workspace/llm-clean-resume/verification-policy/architecture.json
.venv-linux312/bin/python -m tests.llm.check_budget_mutation
.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/verification-policy/docs.json
```

집중/통합/안전성은 132 discovered/started/pass, failure/error/Skip/expected failure/
unexpected success/missing/unexpected/duplicate 0, 41.392초, exit 0이다. 전체 discovery 2,366개
중 선택한 132개만 실행했다. 정적 감사 new/stale/S1 0, 남은 후보 553개, exit 0이다.
R3 mutation은 실제 두 번째 파일 효과 assertion 실패를 검출했으며 detector exit 0이다.
변이 내부의 의도된 실패는 일반 회귀 성공·실패에 합산하지 않는다. 문서 검사도 exit 0이다.

이 실행 중 제품·시험 소스를 수정하지 않았다. 실행 후 Python 659개 manifest는
9693966b4c0f432960807c4aeb5738b52874601a1d980f4010aed32e2fc4d90d이며,
HEAD 0a2f3a0 위의 H2-g 미커밋 변경과 정책 실행기 변경을 포함한다. 이 hash를 실행 전
측정값이라고 보고하지 않는다. 기존 결과를 재사용한 실행이 아니며 원시 증거는
/workspace/llm-clean-resume/verification-policy/에 남긴다.

이번 정책 소단계에서 전체 회귀는 수행하지 않았다. 영향은 실행기의 명시적 선택과 보고에
한정하고 기본 전체 경로를 별도로 검증했다. 다음 Level 3은 H2-g의 Tool/Hub 소비자 통합
게이트이며 새 실행기와 테스트도 포함한다. 이미 실행한 H2-g 최초 전체의 LLM 1,821 pass와
Hub 539 pass/1 error는 그 실행의 별도 결과다. 합쳐서 정책 변경의 전체 통과로 보고하지 않는다.
GitHub API 조회가 Forbidden으로 거부되어 현재 원격 CI 성공은 확인하지 못했다.


## 2026-10-21 H2-g 검증 후 문서 우선 갱신

Tool owner의 패키지 존재·리비전·worker 소유권 검증과 Hub 외부 가져오기 경계를 실제 구현에
맞췄다. Tool README의 manifest 없음·source만 backup 표현을 수정했고 저장 transaction 표를
보완했다. 최초 Hub 소비자 오류와 수정 후 결과, 별도 RAG Job의 두 실패는
[수명주기 검증](lifecycle-verification.md)에 보존했다. H2 전체 완료로 표시하지 않는다.

사용자의 효율 지침에 따라 기존 38개 데이터군 감사와 확정 계약을 기준으로 변경된 owner만
갱신한다. 원감사 외부 경로 /workspace/llm-clean-p1/lifecycle-audit/REPORT.md는 이번 환경에
없으므로 존재하는 커밋 문서의 감사 요약과 후속 증거를 사용한다. 원문 전체를 재검토한 것으로
보고하지 않는다. 필요한 소스 범위·검증 요약·실패 항목부터 확인하며, 변경으로 근거가 무효화된
범위만 추가 조사한다. 필수 안전성·실패 보존·상위 완료 조건은 유지한다.


## 2026-10-21 H2-h Level 2 후 문서 우선 갱신

RAG Job 존재 목록·lease의 소유 구현체와 원자 저장, 입력 소실의 기존 재개 진단 계약을
현재 구현에 맞췄다. 592개 선택 검증과 전체 회귀 유예, 별도 준비/배치 영수증 소실 실패를
[수명주기 검증](lifecycle-verification.md)에 구분했다. H2 전체·원격 CI 완료로 표시하지 않는다.
기존 38개 데이터군 원감사와 이전 실행 결과는 다시 작성하지 않았다.

## 2026-10-21 H2-i 묶음 전체 게이트 후 문서 우선 갱신

JobStorage의 준비/배치 존재 증거와 공개 후 원자 정리 계약, H2-h/i 전체 회귀와 실패 이력을
[수명주기 검증](lifecycle-verification.md)에 갱신했다. 기존 준비 영수증 소실 재현의 해결과
별도 Memory 요약 소실 2개 실패를 구분했다. 기존 38개 데이터군과 docs 전문 검토 결과를
재사용하며 변경된 owner만 갱신했다. H2 전체·원격 CI 완료로 표시하지 않는다.
