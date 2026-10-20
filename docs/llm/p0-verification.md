# P0 검증 도구와 실행 기록

기준 main은 `94229cb865374d99f20ec48c5e48269488041a4e`, 작업 브랜치는
`refactor/llm-clean`이다. 이전 문서 단계의 결과는
[문서 검토 기록](documentation-review.md)에 보존한다. P0에서는 제품 Python과 제품 자산을
변경하지 않는다. P1 및 A–F의 구현 완료를 뜻하지 않는다.

## 저장소에서 재현하는 검사

Python은 `pyproject.toml`의 3.12.14를 사용한다. `ish.platform`은 외부 참조이며 커밋하지 않는다.
검증한 host SHA는 `d833790959a76ac457fdeea3f21a6bea1eecfe12`다.

```bash
export TERM=xterm-256color
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_MODE=PRODUCTION
# 시스템 PATH에 없으면 실제 설치 경로를 명시한다.
export HUB_TEST_XVFB=/usr/bin/Xvfb
export HUB_TEST_XCLIP=/usr/bin/xclip
python -m tests.llm.check_docs --output artifacts/docs.json
python -m tests.llm.check_architecture --output artifacts/architecture.json
# P1 종료: S1–S5 후보 해결·분류, A 종료: S6까지 해결·분류
python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5
python -m tests.llm.check_architecture --strict
python -m tests.llm.check_budget_mutation
python -m tests.llm.run_regression --suite all --output artifacts/regression.json
```

`check_docs`는 docs/ 및 llm/, hub/의 Markdown을 발견해 상대 링크·anchor·Python 코드 블록
문법을 검사한다. Cloud 절대 경로, 고정 문서 수, 과거 commit을 내장하지 않는다.
실모델 실행이나 문서의 의미적 정합성을 증명하는 도구는 아니다. 역사 문서 원문 보존과
제품 무변경 hash는 문서 편집 단계의 증거이며 일반 문서 검사에 상시 강제하지 않는다.

`check_architecture`는 S1–S6의 제한된 AST 패턴을 검사한다. 후보는 확정 위반과 다르다.
각 AST 발생의 파일·심볼·구조 hash를 기록하므로 기존 파일에 추가하거나 복제한 위반,
다른 owner로 옮긴 위반, 기본값 변경을 기존 목록으로 통과시킬 수 없다. 삭제한 후보는
목록에서도 제거해야 한다. 자동 baseline 갱신 옵션은 없다.

`fixtures/architecture_debt.json`은 main의 **미해결 검토 후보**이며 승인된 예외 목록이 아니다.
허용 가능한 Hub 생성 프리셋·기술 상수·UI 표시도 개별 owner와 계약에 따라 분류해야 한다.
기본 검사는 새 후보 및 오래 남은 목록을 거부하는 P0 gate이고, 엄격 검사는 남은 후보가
있으면 실패한다. P1 종료 시 타입·네이밍·호환·선언 후보를 해결해야 하며 Provider 경계는 A의
완료 조건이다. 동적 dispatch, 임의 alias, 전체 데이터 흐름의 의미를 이 AST 검사만으로
증명하지 않는다. 실제 Schema/Reader/Writer·호출 경로 검토와 안전성 시험은 계속 필요하다.
`test_policy_consumers`는 Tool/Retry의 실제 정책 Schema와 `ToolExecutionScope` Reader를
대조한다. 선언됐지만 읽히지 않는 필드, 선언되지 않은 필드를 거부하며 동적 lookup을 자동
검증 완료로 취급하지 않는다. 필드 전달 이후의 실제 효과는 R3 시험이 담당한다.

`run_regression`은 모듈 수동 목록 대신 LLM/Hub 전체 unittest discovery를 사용한다.
발견 ID와 실제 시작 ID, 실패·오류·Skip·expected failure를 저장한다. 누락·중복·Skip·0건 실행은
실패다. 하위 subtest의 실패/Skip도 부모를 검증 완료로 세지 않는다. CI의 두 matrix job은
각 트리를 전체 실행하며 어느 하나가 실패해도 다른 job의 증거를 남긴다.

## 실제 효과 및 한도 검증

`NestedGraphTests.test_resume_preserves_one_call_agent_budget_and_blocks_new_effect`는
부모 한도가 없는 상태에서 Agent `max_tool_calls=1`을 사용한다. 첫 Tool이 실제 파일을 쓰고
모델 호출이 실패한 뒤 백엔드를 닫고 다시 열어 Resume한다. 새로운 Run ID와 누적 사용량을
확인하고, 새 두 번째 Tool 효과가 없으며 `tool_budget_exceeded`로 실패하는지 확인한다.
원본 checkpoint도 보존한다. 이 시험은 현재 nested Agent 경로이며 B/C 이후 새 root API의
동일 안전성을 자동으로 보증하지 않는다.

`check_budget_mutation`은 테스트 프로세스에서만 `ToolExecutionScope.child`의 max_calls 전달을
제거한다. 같은 시험이 실제 두 번째 파일 쓰기 assertion에서 실패해야 검출 성공이다.
다른 오류·Skip·시험 미실행은 검출 성공으로 처리하지 않는다. 정상 시험의 실패는 허용하지 않는다.
기존 승인·Host deny·영수증·checkpoint·retry 시험도 전체 회귀로 계속 실행한다.

## 환경과 CI

CI는 Ubuntu 24.04를 명시하고 Python 선언 및 고정 host의 의존성을 직접 읽는다.
Bubblewrap·Xvfb·xclip을 준비하며, host 경로/정확한 SHA/Python/격리 조건을 별도 검사한다.
제품 sandbox flag, network deny, 파일 경계, capability 정책을 완화하지 않는다.

Cloud의 명령 샌드박스 안에서는 bwrap UID map 생성이 read-only로 거부됐다. 승인된 실행
환경에서는 bwrap 0.12.0의 기존 `--unshare-all --ro-bind / /` smoke가 통과했고 실제 제품
sandbox의 파일·네트워크 거부 시험도 실행했다. 이를 GitHub runner 통과로 대체하지 않는다.
기존 main job 로그는 Azure Blob redirect에서 Forbidden으로 원시 로그를 확보하지 못했다.

## 2026-10-20 로컬 실행 결과

| 검증 | 실행 결과 |
|---|---|
| 변경 전 실제 host loader 단일 시험 | 1 failure, exit 1; `_library_available` 부재 재현 |
| 최소 메서드명 수정 후 host 3개 모듈 | 44 pass, 0 failure/error/Skip, 17.923초 |
| 전체 LLM+Hub | 2,184 pass, 0 failure/error/Skip, 796.260초 |
| 전체 발견/실행 ID 대조 | 누락 0, 중복 0, 예상 밖 실행 0 |
| Xvfb/xclip | 위 전체 Hub에 포함된 9개 실제 pass |
| 정상 R3 실제 효과 시험 | 1 pass; Resume 이후 새 효과 없음 |
| max_calls 전달 제거 변형 | 의도한 실제 효과 assertion에서 1 failure; 변형 검출 명령 exit 0 |
| 마지막 checker/정책 Reader 집중 시험 | 19 pass; 아래 전체 실행 이후 추가 보완분 포함 |
| 제품 AST 후보 | 377 파일, 755개 미해결 후보. 새 후보 0; 엄격 gate는 실패 |

전체 2,184는 LLM 1,650 + Hub 534다. 당시 검사 도구 14개 및 R3 1개가 기존 고유
2,169개에 추가됐다. 이후 제품 변경 없이 checker 시험 2개와 정책 Reader 시험 3개를
추가·보완하여 19개 집중 시험을 다시 실행했다. 새 전체 discovery는 2,189개이며, 이 수를
위 로컬 전체 실행 수로 바꿔 쓰지 않는다. 원격 CI에서 최종 집합을 별도 확인한다.

위 결과의 호스트 명령은 `python -m unittest -v tests.llm.test_plugin
tests.llm.test_tool_packages tests.hub.test_ish_integration`, 전체 명령은 위의
`python -m tests.llm.run_regression --suite all --output artifacts/regression.json`이다.
집중 명령은 다음과 같다.

```bash
python -m unittest -v tests.llm.test_documentation_checks \
  tests.llm.test_architecture_checks tests.llm.test_regression_runner \
  tests.llm.test_policy_consumers
```

후보 분포는 S1 36 / S2 109 / S3 561 / S4 29 / S5 2 / S6 18이다. 이 목록에는 유효한
선언·UI 표시·SDK 계약도 포함될 수 있으며 전부 결함으로 단정하지 않는다. P1에서 실제
owner·Schema·Reader/Writer·소비자·시험 근거로 개별 해결/분류해야 한다. 기존 항목을
자동 승인하거나 전역/디렉터리 예외로 바꾸지 않는다.

host 수정은 `919fc78`로 별도 커밋·Push했다. 제품 Python, `ish.platform`, main과 기존
backend 브랜치/PR #1을 수정하지 않았다. **원격 CI 검증 전이므로 P0 전체 완료 및 P1/A–F
진입을 아직 선언하지 않는다.** 실제 인증 모델 호출과 커널 short-write 장애 주입도 미검증이다.

다음 P1-a의 변경 대상은 `llm/core/configuration.py`,
`llm/services/runtime/{tools,checkpoints,operations}.py`와 각 저장 Reader owner 및 관련 테스트다.
False/0/[]를 정책 없음으로 바꾸는 경로, bool/float 버전 수락, Reader 거부 시 원본 보존을 먼저
검증한다. 이어 호환 소비자 직접 이관·owner별 선언 정리를 마친 후 A의 Provider 경계로 진행한다.

## 첫 원격 CI 실패와 진단 보완

[run 39370650287](https://github.com/E1der0wL/ish-llm/actions/runs/39370650287)은
`847612f`에서 LLM/Hub 두 job 모두 실패했다. 두 job의 전체 회귀 step도 실행됐지만 실패했으며,
로그/zip 보관소로 향하는 네트워크 프록시 터널이 `403 Forbidden`이어서 원격 테스트별
통과·실패 수를 확인하지 못했다. 로컬 통과 수로 원격 결과를 대체하지 않는다.

Checks API에서 확보한 실제 환경은 Python 3.12.14, 지정 host SHA, Ubuntu 24.04 runner의
kernel `6.17.0-1022-azure`, bwrap 0.9.0이다. 기존 격리 smoke가
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`로 exit 1,
별도 user/network namespace 진단도 `/proc/self/uid_map: Operation not permitted`로
exit 1이었다. 이 시점에는 AppArmor/seccomp 중 원인을 확정하지 않았다.

환경 annotation은 GitHub에서 4,096자에 잘려 커널 거절 부분을 읽지 못했다. 이후 진단은
UTF-8 크기를 제한한 유효 JSON으로 분리하고, kernel log의 실제 DENIED 항목을 별도로 남긴다.
테스트의 실제 집계와 실패 일부도 Checks API에 남기며 원본 전체는 artifact에 보존한다.
두 job 모두 namespace 검사를 수행한다. 환경 검사 실패는 job 실패이고, 그 실행의
회귀 미실행을 명시한다. 환경이 통과한 뒤에만 전체 회귀를 시작하며, 실패 환경을 우회하거나
시험 Skip을 통과로 처리하지 않는다. CI job 실행 기한은 workflow 소유의 30분으로 명시한다.

진단 전송 보완 시험은 5개 통과했다. 이 보완은 제품 동작이나 테스트 assertion을 변경하지 않는다.
새로 추가한 annotation 시험 2개를 포함한 다음 전체 discovery는 2,191개이며 아직 원격
통과로 보고하지 않는다.

## AppArmor 원인과 CI 한정 복구

[진단 run 39372311849](https://github.com/E1der0wL/ish-llm/actions/runs/39372311849)의
Checks 기록에서 원인을 확인했다. AppArmor `unprivileged_userns`가 bwrap의
`setpcap`·`net_admin`, 대조군 unshare의 `sys_admin`을 거부했다. 전역 userns 생성은
활성화되어 있었고 `kernel.apparmor_restrict_unprivileged_userns=1`, Seccomp=0이었다.
두 환경 gate가 실패했으므로 이 실행에서는 전체 회귀를 시작하지 않았다.

복구 설정은 [CI 전용 AppArmor profile](../../tests/llm/ci/bwrap.apparmor)과 workflow가
소유한다. 일반 사용자로 실행하는 root 소유·non-setuid `/usr/bin/bwrap`만 userns를
사용할 수 있도록 한다. 전역 AppArmor 제한을 1로 유지하고, 별도 `unshare` 명령은
계속 거부되어야 setup이 통과한다. 이 설정은 일회용 GitHub runner에만 설치하며 제품
배포 설정이나 사용자 호스트 정책으로 주입하지 않는다.

profile의 `unconfined`는 원래 unconfined였던 launcher에 userns 권한을 명시하는 AppArmor
방식이다. 제품의 `--unshare-all`, `--cap-drop ALL`, mount/network 경계는 변경하지 않는다.
실제 Tool은 sudo로 실행하지 않는다. launcher 권한 추가와 제품 격리를 혼동하지 않으며,
복구 성공은 동일 runner에서 실제 파일·네트워크 거부 및 전체 회귀가 통과한 뒤에만 판정한다.

## 복구 후 실제 원격 회귀와 P0 차단 상태

[run 39372835749](https://github.com/E1der0wL/ish-llm/actions/runs/39372835749),
commit `ef35e5111bc4736e43cef823b0828a9d000fdce1`에서 두 job의 환경 gate,
문서 검사, AST 신규 후보 검사와 LLM R3 변형 검출이 통과했다. 전역 AppArmor 제한은 1,
일반 unshare는 거부, Tool 시험은 일반 사용자 실행이었다. 전체 회귀는 다음과 같이 **실패**했다.

| 범위 | 발견/실행 | 통과 | 실패 | 오류 | Skip | 시간 |
|---|---:|---:|---:|---:|---:|---:|
| LLM | 1,657 / 1,657 | 1,655 | 1 | 0 | 1 | 679.782초 |
| Hub | 534 / 534 | 533 | 1 | 0 | 0 | 828.434초 |

누락·중복은 두 job 모두 0이다. Hub X11 9개 및 실제 sandbox 시험은 이 집합에 포함되어
실행됐다. 실패 ID는 `tests.llm.test_loop.LoopTests.test_real_litellm_sdk_with_mock_http_sse_and_tool_loop`,
`tests.hub.test_http_backend.HTTPBackendTests.test_production_backend_streams_from_configured_endpoint`다.
이 실행의 annotation에는 Skip의 ID/이유가 없어 정확한 항목은 아직 미확인이다.
설치 목록에 Tesseract가 빠진 것은 확인되어 CLI와 eng 자료를 추가하고 환경 gate에서 요구한다.
후속 실행부터 Skip ID/이유도 Checks에 기록한다. 기존 Skip 조건과 assertion은 유지한다.

Cloud의 기존 전체 통과 환경은 LiteLLM 1.103.1 / OpenAI 2.54.0 / MCP 1.30.0이다.
현재 허용 범위를 새로 설치한 **별도 외부 디렉터리**에서 LiteLLM 1.106.0 / OpenAI 2.54.0 /
MCP 1.30.0으로 위 두 기존 시험을 실행하자 2 failure / 0 error / 0 Skip으로 재현됐다.
기존 venv나 제품 파일은 바꾸지 않았다.
같은 진단 명령을 기존 1.103.1 조합에서 다시 실행한 결과는 2 pass / 0 failure/error/Skip이다.

```bash
python -m tests.llm.check_provider_environment --output artifacts/provider-environment.json
```

이 명령은 기존 두 시험의 assertion을 그대로 실행하고 SDK 버전 및 본문·인증정보가 없는
예외 identity/source 위치를 보존한다. 메모리 HTTP transport/loopback 서버만 사용하므로
실제 인증 모델 호출 검증은 아니다.

확인한 호출은 LiteLLM `main.completion` → `responses.mcp.chat_completions_handler` →
`responses.mcp.litellm_proxy_mcp_handler` → `proxy._experimental.mcp_server.catalog`다.
`tools`가 있으면 일반 Tool도 이 import를 지나며, 마지막 모듈의 `from mcp.types import
CacheableResult`에서 ImportError가 발생한다. LiteLLM 1.106.0의 MCP extra는 `mcp>=2.2.0,<3`,
프로젝트의 MCP 계약은 `mcp>=1.30,<2`다. 일반 LiteLLM 설치에는 이 extra 제약이 강제되지 않아
resolver 성공만으로 런타임 호환성이 증명되지 않는다. 원격의 동일 원인 여부는 새 진단으로
별도 확인한다.

P0는 **미완료·차단**이다. CI만 예전 SDK로 고정하여 제품의 넓은 허용 범위를 검증 완료로
표시하지 않는다. 권장 후속 조치는 실제 검증된 SDK/MCP 조합을 프로젝트의 설치 계약으로
명시하고, 그 조합에서 전체 회귀를 다시 통과시키는 것이다. MCP 2로 전환하려면 실제
stdio/HTTP/취소·Tool 승인 경계를 먼저 검증해야 하며 단순 버전 상향으로 처리할 수 없다.
이 핵심 통합 실패가 남아 있는 동안 P1과 A–F 제품 구현에 진입하지 않는다.

## 감사 범위 보완

S3/S4에 `format_version`을 포함하면서 main에 이미 존재한 후보 12개를 추가했다.
Vision Reader/Writer 3개, DirectoryBackups 5개, Transaction Reader/Writer 4개다.
세 제품 파일이 기준 main과 byte 단위로 같음을 확인하고 기존 755개 항목도 보존했다.
정확한 타입을 검사하는 Backup/Transaction Reader도 후보에 포함되므로 미검토 후보 수를
실제 결함 수로 바꾸지 않는다. 신규 위반을 허용한 것이 아니라 누락된 감사 범위를 확장한 것이다.

현재 후보는 **767개**: S1 36 / S2 109 / S3 568 / S4 34 / S5 2 / S6 18.
새 후보·오래 남은 목록은 0이며 엄격 gate는 여전히 실패한다. 이 보완 뒤 checker/정책 Reader/
annotation 집중 시험 21개가 1.489초에 통과했다. 앞서 실행한 전체 2,184 및 원격 2,191의 결과와
구별한다.
