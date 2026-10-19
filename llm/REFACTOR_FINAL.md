# `llm/` 백엔드 구조 개편 최종 검증 보고서

**현재 게이트 상태: A~F 구현 및 이 Cloud 환경의 전체 회귀 검증 완료.**
최종 동결 소스에서 LLM 1,747개, Hub 534개, 중복 포함 호스트 대상 44개가
모두 통과했고 실패·Skip은 0개입니다. 기존 Hub API 호환 검증도 통과했습니다.
실제 인증 모델 호출, 커널 short-write 장애 주입, CI/운영 검증은 별도 미검증이며
배포 준비 완료를 의미하지 않습니다.

## 1. 저장소·범위·커밋

대상은 `E1der0wL/ish-llm`입니다. 작업 시작 HEAD는
`7984c8e225ca07711c866607d110727dfbb3c391`이며 기존 호스트 검증의
`status-before.txt`/`status-after.txt`는 모두 clean입니다. Codex 관리 로컬
`work`를 유지하고 `HEAD:refactor/llm-backend`로 단계별 Push했습니다.
F 착수 시 HEAD `d5cddc05d339940faa07e4dc8240bf706617967b`도 clean이었습니다.
최종 F SHA는 이 보고서를 포함한 커밋이며, 최종 응답과 `git log -1`에서 확인합니다.

`main`은 `94229cb865374d99f20ec48c5e48269488041a4e`를 유지합니다.
변경 범위는 `llm/`과 `tests/llm/`입니다. `hub/`, `tests/hub/`, `examples/`는
기준 main과 바이트가 같습니다. 변경 파일 전수 목록은
[REFACTOR_CHANGED_FILES.txt](REFACTOR_CHANGED_FILES.txt)에 있습니다.
`ish.platform/`은 ignored 외부 참조이며 커밋하지 않았습니다.
PR은 생성하지 않았고 원격 브랜치를 유지합니다. main 병합은 하지 않았습니다.

| 단계 | 커밋 | 상태와 검증된 결과 |
|---|---|---|
| A Provider 독립화 | `7984c8e225ca07711c866607d110727dfbb3c391` | 기존 구현·감사·golden 보존; 후속 전체 회귀에 포함 |
| 호스트 테스트 수정 | `044c5b82c90302ca8934c74bcdda1c91995f9a3d` | API 호출 한 곳만 수정; 44 대상 / 1,658 LLM / 534 Hub 통과 |
| B Agent 도메인 | `9d37f939bb93206271e4d5cd8a66a937ad93caf0` | 1,671 LLM / 534 Hub 통과 |
| C Agent 기반 Run | `3076192f58f5b803136b2a1bcf14a5fc9946f6fd` | 1,687 LLM / 534 Hub / 44 대상 통과 |
| D 다중 Agent Dispatcher | `4ffbb5e69abdfe7579e0db83db9531a209b3ae3e` | 1,712 LLM / 534 Hub / 44 대상 통과 |
| E Session 전송 | `d5cddc05d339940faa07e4dc8240bf706617967b` | 1,726 LLM / 534 Hub / 44 대상 통과 |
| F 최종 후보 검토 | 이 보고서를 포함한 최종 커밋 | 1,747 LLM / 534 Hub / 44 대상 통과; 구현 및 Cloud 전체 회귀 검증 완료 |

각 단계의 실패→원인→수정→재검증과 실제 명령/시간은
[REFACTOR_BF.md](REFACTOR_BF.md)에 남겼습니다. A 원본은
[providers/REFACTOR_A.md](providers/REFACTOR_A.md)입니다.

## 2. 소유 관계와 실행 계약

영속 소유 관계:

```text
Project
├── Agents                 (정의·revision)
├── Sessions
│   └── Session
│       ├── Messages       (User/Assistant, 실행 참조)
│       └── Runs
│           └── Steps
└── Components             (Memory/Goal/Tool/Prompt 등)
```

호출 흐름은 `Run → Agent → Engine → CompletionAdapter → SDK`입니다.
EngineRegistry/Adapter는 Backend/Host 실행 환경에 있습니다. Agent, User Message,
Run, Assistant Message 사이의 ID 연결은 참조이며 추가 소유 계층이 아닙니다.
Graph/`agent_run`의 하위 Agent는 부모 Run의 Step이고 별도 최상위 Run을 만들지 않습니다.

Project Agent의 실행 정의 필수 필드는 `engine` 하나입니다. 선택 필드는
purpose/description/metadata, prompt/completion/engine_options, input/output schema,
tools/resources/policy/output_format, `completion_processors`입니다.
`project.agents`는 sync/async create/load/list/snapshot/revise/delete 등을 제공하며
`revise`는 expected_revision을 요구합니다. 저장은 기존
`agents/records/<id>.json`의 `{storage_version:1,id,definition,revision}` envelope입니다.
구 raw 정의는 명시적으로 읽되 조회만으로 파일을 다시 쓰지 않습니다.

`AgentComponent`/`AgentData`는 동일 카탈로그에 위임합니다. 기존 Component 쓰기
입력의 `purpose` 필수 계약은 유지하고, engine-only 정의 생성은 새 Project facade를
사용합니다. 비활성 Component 때문에 Agent 정의가 삭제되지 않으며, Session/Run/
Workflow/리소스 참조와 CAS를 검사합니다. clone/backup/restore도 같은 소유 관계를
따릅니다. 자동 일괄 파일 이동이나 파괴적인 마이그레이션은 없습니다.

모든 새 Run은 `agent_id`와 frozen Agent/Engine/resource binding을 가집니다.
기존 `engine=` 호출은 안정적인 호환 Agent로 정규화해 같은 실행 경로를 사용합니다.
실행 설정은 revision으로 기록하며 runtime 자격 증명을 Run 이력에 복제하지 않습니다.
정의/Engine/resource 변경으로 재개 binding이 달라지면 새 요청이 필요합니다.

## 3. Session·Message·Run 동시성

Session의 `agents.attach/detach/list`는 Project Agent ID 참조를 관리합니다.
새 Session v2는 `agent_ids`와 `current_run_ids`를 갖고, singular `current_run_id`는
활성 Run이 하나일 때만 그 ID입니다. 구 Session/Run/Message 읽기 계약은 유지합니다.

`session.run.submit(text, agent_id=...)`, `agent_ids=(...)`, `recipients="all"`을
지원합니다. `all`은 접수 시점 집합을 동결합니다. 대상 생략은 단일 참가자일 때만
결정되며 모호하면 거부합니다. 하나의 User에 여러 형제 Run이 같은
`input_message_id`를 참조하고 각 Run은 별도의 Assistant/Step/Usage를 갖습니다.
`RequestHandle.runs/aruns/wait_all`로 전체 결과를 조회합니다. 기존 single
wait/result는 다중 결과를 임의로 하나 선택하지 않습니다.

동일 Agent는 FIFO, 서로 다른 Agent는 병렬입니다. `max_parallel_runs`를 지키며
Run별 실행/취소/종료 상태를 관리합니다. 개별 Run 취소는 형제를 중단하지 않고
Session interrupt는 현재 실행 집합을 다룹니다. 접수와 그룹 확정에는 기존
Workspace transaction의 COMMITTED 경계를 사용합니다. 검증된 미시작 pending만
복구 실행하며, 이미 시작했거나 효과가 불확실한 Run/Tool은 자동 재실행하지 않습니다.
공통 문맥 snapshot, 형제 그룹 단위 retention/삭제 보호, inert clone과 pending
backup 복원은 아래 테스트에 포함됩니다.

## 4. Session 간 상호작용

공개 `source.exchange.send(target.id, text, agent_ids=(...), key=...,
source_run_id=None, deadline=None)`는 같은 Project의 대상 User 접수를 사용합니다.
별도 실행 큐/스케줄러를 만들지 않습니다. source 소유 영수증과 target User가 같은
transaction에서 확정됩니다. 같은 key/내용은 원본을 재조회하고, 다른 내용/대상/
수신자/출처/기한은 충돌합니다. 영수증이 없어진 손상 상태를 새 전송으로 덮지 않습니다.

권한은 신뢰하는 Backend 호스트의 소유 Project 범위이며, 선택적 동기 bool
`session_authorizer`가 send/execute/inspect/conversation/query/cancel/resume을
각각 좁힙니다. dispatch 때 다시 검사합니다. source Agent/Message는 실제 source
Run에서 유도합니다. 다른 Project, A→B→A, 반복 Session 또는 16개를 넘는 계보는
거부합니다. 원래 deadline/계보는 Resume/Restart 후에도 유지됩니다. 원본 Memory,
내부 문맥, 승인 권한을 대상에 복사하지 않습니다.

`inspect/ainspect`, `get/aget`, `snapshot/asnapshot`, `wait`, `cancel`, `resume`을
제공합니다. sender의 wait 취소/timeout은 target을 중단하지 않습니다. 대상의 Tool은
대상 승인 정책을 사용합니다. 자동 모델 Tool은 등록하지 않습니다. 이 API는 portable
file ConversationStore를 요구합니다. 영수증을 보관하는 동안 연결 Session/turn의
삭제·retention이 보수적으로 제한됩니다. 전체 Project 삭제는 전체 소유 영역을 지웁니다.

## 5. 최종 후보 검토

`CompletionSession.review_final(CompletionReviewObservation) -> CompletionReview`
계약과 사용 예제는 [services/README.md](services/README.md)에 있습니다.
명시적 `review_revision`이 있는 Processor만 참여합니다. 모든 검증기의 accept가
필요하며 block > continue > accept입니다. 기본값은 추가 continue 8회, 후보별
attempt 3회, 각 훅 30초, 합계 feedback 4,096자입니다.

검토용 관찰은 준비 전후 공통 transcript/response/Tool 정의 사본이며 Provider
parameters/자격 증명은 없습니다. 검증기는 순수 결정을 반환하며 Tool 승인이나
원본 메시지/Engine 권한을 바꾸지 않습니다. 후보 delta/output은 internal이고
Assistant 본문은 최종 수락 OUTPUT으로만 확정됩니다. 검토 없는 기존 스트리밍은
유지합니다. continue는 같은 Run의 의미적 iteration이고 기존 ToolExecutor를 씁니다.

Checkpoint는 후보, 시도 예약, 결정/피드백/출처를 구별해 저장합니다. 저장된 결정은
재사용하고 결정 미확정 때만 같은 후보를 제한적으로 다시 검토합니다. Graph 및
agent_run의 하위 검토는 Step scope에 있습니다. Resume quota는 이전 모델 및
Component 영수증을 합산하고 미확인 사용량을 0으로 바꾸지 않습니다. 검토 Run의
원래 Run timeout 절대 기한은 승인 대기 시간을 포함하며 Resume로 늘어나지 않습니다.

기존 prepare/after_completion의 saved-response 재실행 금지, Memory의 지연 준비
경계, 임의 finish 확장의 반복 가능성은 유지합니다. 정확히 한 번의 사용자 알림이나
임의 Python 확장 효과를 보장하지 않습니다. 신뢰하는 확장 코드의 계약이며 sandbox가
아닙니다. 업무별 최종 성공 규칙은 구현하지 않았습니다.

## 6. Provider 및 실제 구형 데이터

§4-1/§4-2의 전체 경로별 수정·유지 판단은 A 보고서의 표를 보존했습니다.
현재 전수 검색은 [PROVIDER_AUDIT_FINAL.json](PROVIDER_AUDIT_FINAL.json)의
**50 파일 / 417 일치 행 / 미분류 0**입니다. 문자열 검색을 의미 검증으로 대체하지
않으며, SDK 해석/변환, 의도적인 v1 호환 바인딩 투영, 영속 도메인 데이터를 구분합니다.
재현 명령은 `python -m tests.llm.support.provider_audit`입니다.

비 OpenAI CommonProvider는 실제 Loop/Tool/Memory/Processor/usage/Resume 경로를
통과합니다. F의 실제 모델 입력과 검토도 이 Adapter를 사용합니다. 기존 LiteLLM
mock HTTP/SSE 테스트는 인증된 외부 모델 호출 검증과 구분합니다.

| 실제 생성 fixture | 생산 코드 SHA | archive SHA-256 |
|---|---|---|
| provider_v1/workspace | `94229cb865374d99f20ec48c5e48269488041a4e` | `12d62a0018b463898afe2ae40fc075fdb32ced9f136ba3ed6b8b30ec08549a15` |
| provider_v1/graph | 동일 main SHA | `f0d37f45d6ca4f9cf0a5939ccbf5b578da353545768ecb584d69058de1b9ab3e` |
| agent_v1/workspace | `7984c8e225ca07711c866607d110727dfbb3c391` | `f7c6cd0036169ed5b558ab9eb96f4f61e79891bde7736122e6327671e1476418` |
| agent_run_v2/workspace | `3076192f58f5b803136b2a1bcf14a5fc9946f6fd` | `07f80b40b3c5e67f497355c0e919379bed73a601fe383b854e4424c16adba27f` |

각 `tests/llm/fixtures/<name>/`에 생산 스크립트와 파일별 해시 manifest가 있습니다.
변경 전 코드로 생성했으며 현재 구현으로 재생성하지 않았습니다. 독립 사본을 열어
raw Agent, engine-only Run/queue, Session v1/singular ID, Run v2, Message 참조,
완료 Tool 영수증과 미결 승인/Loop/Graph Checkpoint를 검증합니다.

## 7. 장애 경계 F01~F14

아래 상태는 각 단계 및 최종 전체 게이트의 실제 통과 증거입니다. 최종 F 전체
결과는 §9에 있습니다. `python -m unittest -v tests.llm.<module>`로 재현합니다.
지점 이름이 seed 역할을 하며 D child=91, E child=93, F child=94 종료 코드와
reached marker를 검증합니다. 영수증/Run/Message ID는 원시 로그의
`D_FANOUT_CRASH`/`D_TOOL_CRASH`/`E_F13`/`F_F14` 증거 및 해당 테스트 assertion에 있습니다.

| ID | 상태 | 모듈·재현 지점 및 확인 사항 |
|---|---|---|
| F01 | 통과 | `test_multi_agent_crash.MultiAgentCrashTests.test_f01_f06_f12_atomic_fanout_and_no_automatic_replay`: before_user/written_user; 새 owner에 User/Run 0, 접수 ACK 없음 |
| F02 | 통과 | 같은 테스트 accepted; durable User 1, 재기동 후 원본 ID에서 단일 fan-out |
| F03 | 통과 | 같은 테스트 partial_group/group; 그룹 rollback 또는 전체 복원, 부분 그룹을 성공으로 승격하지 않음 |
| F04 | 통과 | 같은 테스트 group/start/binding + missing_active_index 보호; 미시작만 실행하고 불확실 시작은 interrupted, 활성 집합 복구 |
| F05 | 통과 | `test_agent_runs` queued_definition_change / changed_paused_agent / queued_resume_rechecks_engine_configuration; `test_agent_domain` CAS/write rollback; 다른 Engine으로 전환 없음 |
| F06 | 통과 | `test_multi_agent_crash` stream; `test_common_provider` first_chunk_boundary / cancel_after_received_envelope; 부분 출처·재시도·닫기 계약 |
| F07 | 통과 | `test_multi_agent_crash.MultiAgentCrashTests.test_f07_f10_parallel_approval_claim_effect_receipt_survive_process_death`: approval/approved; 승인 전 효과 0, 재개 승인 scope 보존 |
| F08 | 통과 | 같은 테스트 claim; started operation을 근거 없이 자동 재실행하지 않음 |
| F09 | 통과 | 같은 테스트 effect; fsync된 실제 효과 1, 완료 영수증 없는 상태에서 자동 재실행 0 |
| F10 | 통과 | 같은 테스트 receipt/completed; 저장 영수증 재사용, 효과 1 유지 |
| F11 | 통과(Checkpoint 범위) | `test_multi_agent_crash.MultiAgentCrashTests.test_f11_damaged_checkpoint_after_parallel_crash_is_not_replayed`; `test_locking_io.ProjectionTests.test_fsync_failure_does_not_acknowledge_or_cache_unwritten_delta`; `test_components.ComponentTests.test_invalid_json_and_failed_atomic_replacement_keep_original_data`; digest/revision/부분 JSON 거부. 커널 short-write는 별도 미검증 |
| F12 | 통과 | `test_multi_agent_crash` one_finished; 독립 Assistant/Run 종료와 남은 형제의 상태; C01/C09 활성 집합 확인 |
| F13 | 통과 | `test_cross_session_crash`: before_user/target_written/receipt_written/committed; fresh owner의 영수증↔대상 User 및 동일 key 재조회, 효과 1 |
| F14 | 통과 | `test_completion_review_crash`: responded/attempt/computed/continue/accept/published; 명시 Resume 전체 모델 3회·효과 1회, 저장 결정 재사용, 미확정 결정만 제한 재검토 |

F01/F02/F03/F13의 수락 확정점은 Workspace transaction COMMITTED marker입니다.
파일 교체 하나만으로 그룹 전체 접수 성공을 추측하지 않습니다. F14의 모델 응답,
검토 시도, 결정, 최종 OUTPUT은 서로 다른 durable 경계이며 각각 종료를 주입했습니다.

## 8. 결정적 동시성 C01~C12

기본 클래스는 `tests.llm.test_multi_run_interleavings.MultiRunInterleavings`입니다.
표에서 클래스가 생략된 `test_*`는 이 클래스의 메서드입니다. 혼합 부하는
`test_twenty_controlled_mixed_schedules_preserve_run_step_and_active_ids`의
**schedule 0..19**, **4 Session × 3 Agent**, 6가지 종료 순서를 사용합니다.
`D_SCHEDULE_EVIDENCE`에 schedule/order/Session/User/Run/status가 기록됩니다.
Event를 Engine 내부에 두고 모든 진입을 확인한 뒤 해제하며 timeout은 데드락 탐지용입니다.

| ID | 상태 | 실제 검증 |
|---|---|---|
| C01 | 통과 | 위 20 schedule 및 `test_multi_agent.DispatcherTests.test_two_engines_really_overlap_and_complete_independently`; 종료 순서와 Run/Step/Assistant 귀속 |
| C02 | 통과 | 혼합 schedule의 실패 ↔ 형제 정상 완료; 오류·상태 격리 |
| C03 | 통과 | `test_agent_timeout_preserves_other_active_run_and_tool_effect` 및 혼합 취소; 형제 효과/활성 ID 보존 |
| C04 | 통과 | `test_approval_pause_and_resumption_do_not_block_another_agent`; 승인 전 효과 0, 다른 Agent 완료 |
| C05 | 통과 | `test_simultaneous_same_agent_submits_preserve_acceptance_fifo`, `test_completed_recipient_releases_lane_while_sibling_keeps_running` |
| C06 | 통과 | `test_parallel_tool_claims_share_one_receipt_and_do_not_overwrite`; 공유 operation 충돌/영수증/효과 보존 |
| C07 | 통과 | `test_parallel_quota_reservations_are_atomic_and_unknown_usage_stays_unknown`; 원자적 quota와 unknown 유지 |
| C08 | 통과 | `test_all_is_frozen_pending_cancel_and_parallel_limit_changes` + Agent binding 변경/삭제 보호; frozen 대상과 실행 권한 유지 |
| C09 | 통과 | 20 종료 순서와 pending/start 취소 경계; `test_cross_session_requests`의 동시 진입한 두 target Engine 공통 Event 해제 후 독립 종료·활성 집합 확인 |
| C10 | 통과 | `test_shared_turn_clone_backup_delete_and_atomic_retention`, `test_pending_fanout_backup_restores_only_proven_unstarted_recipient` |
| C11 | 통과 | `test_cross_session_requests.test_immediate_multi_recipient_acceptance_idempotency_and_reopen`; source + 두 target Engine 동시 진입, A→B→A 거부, sender 대기 취소 독립 |
| C12 | 통과 | `test_completion_review.CompletionReviewTests.test_c12_concurrent_agents_keep_validator_instances_feedback_and_candidates_separate`; 두 reviewer Event 진입, 한쪽 완료 후 다른 쪽 continue, 독립 인스턴스/feedback/candidate/ID; `F_C12` 증거 |

## 9. 테스트 환경·실제 결과·실패 이력

Python **3.12.14**, LiteLLM 1.103.1, OpenAI 2.54.0.
실제 host `E1der0wL/ish` SHA는
`d833790959a76ac457fdeea3f21a6bea1eecfe12`이고 경로는
`ish.platform/src/ish`입니다. Xvfb `2:21.1.16-1.3+deb13u4`, xclip `0.13-4`를
검증된 Debian 저장소에서 로컬 추출했습니다. 시스템 패키지 변경이나 Skip 확대는 없습니다.

```sh
export TERM=xterm-256color LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_MODE=PRODUCTION
export HUB_TEST_XVFB=/workspace/.setup/x11/root/usr/bin/Xvfb
export HUB_TEST_XCLIP=/workspace/.setup/x11/root/usr/bin/xclip
.venv-linux312/bin/python -m unittest -v tests.llm.test_plugin tests.llm.test_tool_packages tests.hub.test_ish_integration
.venv-linux312/bin/python -m unittest discover -v -s tests/llm -t .
.venv-linux312/bin/python -m unittest discover -v -s tests/hub -t .
```

기존 Bubblewrap의 UID mapping을 허용하는 실행 권한으로 테스트했으며 격리를
끄지 않았습니다. CI 실행 결과는 없고, 아래는 이 Cloud 환경에서의 실제 실행입니다.

| F 최종 게이트 | 결과 |
|---|---|
| 세 호스트 대상 모듈 | 44 통과, 실패/Skip 0, exit 0, 18.860초 |
| `tests/llm/` 전체 | 1,747 통과, 실패/Skip 0, exit 0, 413.616초 |
| `tests/hub/` 전체 | 534 통과, 실패/Skip 0, exit 0, 549.426초; X11 9개 포함 |
| 동결 소스 | 636 Python 파일, 모든 게이트 종료 후 파일 집합·해시 일치 재확인 완료 |

고유 전체 회귀는 LLM+Hub **2,281개**이며 대상 44개를 여기에 다시 더하지
않습니다. 로그/종료 코드/해시 재확인 결과는
`/workspace/refactor-bf-evidence/f-final-verification.json`에도 기록했습니다.

호스트 최소 수정 전: 대상 43 통과/1 실패, LLM 1,657 통과/1 실패,
Hub 525 통과/9 X11 Skip. 수정 후: 대상 44, LLM 1,658, Hub 534 모두 통과.
`_library_available` → `_is_library_available` 호출 한 곳만 바꾸고 assertion을
유지했습니다. X11 9개도 실제 실행됐습니다. 이후 단계의 신규 테스트는 위 표와
단계별 표에서 별도로 집계하며 중복 포함된 대상 44개를 고유 총수에 더하지 않습니다.

모든 실패 로그는 성공으로 바꾸지 않고 보존했습니다. 특히 D Hub 첫 최종 실행은
534 중 1 실패였습니다. 변경 없는 idle polling 테스트에서 0.521초 coroutine stall이
0.5초 idle 간격을 넘었으며, 실제 LLM 호출이 없는 Hub timing 경로였습니다.
동일 테스트/모듈/단독 전체 재실행은 통과했지만 timing 민감성이 사라졌다고 주장하지
않습니다. E 첫 전체 LLM은 외부 Component 조회 계약 의존 오류 1개로 실패했고,
수정 전 exact 재현→수정 후 66개→최종 1,726개 통과를 기록했습니다. F의 Steering,
Graph/위임 컨텍스트 및 새 테스트의 잘못된 기대값도 실패·수정·재실행을 모두 기록했습니다.
상세 명령과 종료 코드는 `/workspace/refactor-bf-evidence/` 및 단계 보고서에 있습니다.

## 10. Hub 인수 계약과 남은 범위

공개 시그니처와 예제는 [services/README.md](services/README.md)를 기준으로 합니다.
기존 Hub는 이번 작업에서 수정하지 않았습니다. 기존 API의 호환성은 실제 전체 Hub
게이트로 확인하고, 신규 다중 Agent/Session 전송/검토 UI는 후속 Hub 작업 범위입니다.
후속 화면은 public Project.agents, Session.agents/run/exchange, RequestHandle,
Run의 response/steps/checkpoint/interactions/usage 조회를 사용해야 합니다.
비공개 EngineContext/ComponentRegistry 저장 구현을 읽을 필요가 없습니다.

호스트 API의 확인된 테스트 수정 지점은 `tests/llm/test_plugin.py`입니다.
기존 단일 Engine/Run 사용 경로의 회귀를 해결하기 위한 Hub 제품 코드 수정은
필요하지 않았습니다. 이는 신규 다중 Agent 화면까지 검증했다는 뜻은 아닙니다.
다음은 실제 소스를 확인한 후속 Hub 변경 지점이며, 이번 커밋에서 수정하지 않았습니다.

| 실제 호출 파일 | 신규 기능 연결 시 필요한 변경 |
|---|---|
| `hub/backend/runtime.py` | `submit`의 기존 `engine=`는 호환 Agent 경로를 유지한다. 새 Agent 선택은 `agent_id`/`agent_ids`/`recipients`로 전달한다. `submission_state`, `instruction_targets`와 삭제/복제 전 busy 판단의 singular `active_run_id`를 다중 `active_run_ids` 및 선택 Run으로 확장한다. Session 전체 interrupt와 개별 Run 취소를 구별한다. |
| `hub/backend/history.py` | singular busy 검사 및 `turn.latest.run_id` 중심의 재개/결과 선택을 형제 Run 집합과 명시적 Run 선택으로 확장한다. 전체 결과는 `RequestHandle.aruns/wait_all`을 사용한다. |
| `hub/backend/observability/snapshot.py` | User의 singular `message.run_id`로 요청 시간을 연결하는 부분은 각 Run의 `input_message_id`를 이용한다. 활성 상태와 마지막 Run 중심 투영을 다중 Run으로 확장하되 Assistant의 `run_id` 귀속을 유지한다. |
| `hub/components.py`, `hub/backend/project/record_management.py`, `hub/backend/tools/resources.py` | 기존 AgentData/Component 기반 편집·authoring Tool은 유지된다. engine-only Project Agent 생성/관리 화면은 `project.agents`의 도메인 schema와 CAS를 사용하고, AgentComponent 활성 여부와 정의 수명을 분리한다. 기존 Tool 승인 경로를 유지한다. |
| `hub/backend/execution/thinking.py` | 이미 검사하는 `visibility == "user"` 경계를 새 화면에서도 유지한다. 검토 중 internal 후보를 최종 Assistant 본문으로 승격하지 않는다. |

Session 전송 화면은 아직 없습니다. 후속 화면은 `session.exchange`의 공개
send/query/wait/cancel/resume 결과로 partial_failure를 표시해야 합니다.
새 UI·Skill·자동 모델 Tool은 이번 백엔드 범위에서 작성하지 않았습니다.

남은 위험·미검증은 다음과 같습니다.

- 실제 인증된 외부 모델 호출 및 공급자별 운영 동작은 미검증입니다.
- 커널 수준 short-write 주입은 미검증입니다. fsync/replace 예외·부분 JSON·digest
  검증을 이 항목의 성공으로 대신하지 않습니다.
- Hub idle polling 테스트의 timing 민감성은 기록대로 남습니다.
- Session 전송은 같은 Project의 신뢰 호스트 API이고 file storage를 요구합니다.
  영수증이 필요한 이력은 보수적으로 보존합니다.
- 검토 callback은 순수 결정 계약입니다. 임의 Python/finish 효과 및 사용자 알림의
  exactly-once 보장은 없고, 검토 Run의 원래 timeout은 승인 대기 시간도 셉니다.
- Cloud에서 실제 수행한 전체 게이트는 통과했습니다. 별도 CI 실행 및 운영
  환경의 인증 모델/장애 검증까지 완료한 것은 아닙니다.
