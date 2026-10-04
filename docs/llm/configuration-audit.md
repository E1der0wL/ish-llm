# 설정 정적 감사 기록

범위는 `llm/` production Python 전체다. tests/examples는 명시적 호출자의 fixture로 따로 검사했다. 핵심 계약·강제값 전체 목록은 [CONFIGURATION.md](../../llm/CONFIGURATION.md)에 있다.

## 2026-10-05 대상별 전달과 애플리케이션 선택 정책 분리

- `ProjectConfig`에는 `policies`, 열린 `parameters`, `data`를 둔다. Loop SDK 옵션은
  `parameters.engines[name].completion`, Component 설정은 `parameters.components[name]`이다.
  Session 생성 시 기본 설정을 복사하지 않고, Run 시작 시 명시적 override를 병합한다.
- llm production 소스의 get_default/aget_default/default-project 포인터 접근을 제거했다.
  session_defaults/default_engine은 거부 목록과 스키마의 금지 조건에만 남는다.
  런타임 선택 분기나 옛 설정 변환은 없다. 기존 데이터 파일도 수정하지 않는다.
- 같은 이름의 model/timeout 인자를 다른 Engine이나 Component로 전파하지 않는다.
  보관 계산기는 retention.counter_params를 사용하며 임의 엔진의 모델을 선택하지 않는다.
- ComponentData.configure와 Project 저장은 같은 ProjectConfig 원본·잠금·CAS를 사용한다.
  통합 조회의 values.config는 저장된 명시값이고 effective 값·출처는 별도로 제공한다.
- Hub의 시작 선택·생성 템플릿은 hub/backend/bootstrap.py에 있다. 등록된 구현 목록,
  일반 create의 ID/제목/저장소 초기화는 기본 프로젝트 선택 정책과 구분한다.
- 변경 전 소스 사본과 AST/diff를 대조하여 Run/Step/Conversation/ToolExecutor/provider/
  체크포인트 저장 owner가 바뀌지 않았는지 확인한다. Graph 설정 바인딩은 policies와
  parameters를 계속 보호하며 Pipeline의 단계별 설정도 같은 새 경로를 사용한다.

설정·생성 회귀는 tests.llm.test_project_creation, Hub 시작 경계는 tests.hub.test_bootstrap에
있다. 감사 스크립트·차이·검증 기록은 tests/llm/reports/ 아래에 두고 배포에 포함하지 않는다.

## 명시적 설정 불변식 감사

검색은 `_defaults|default_configuration|provider_defaults|search_defaults|extraction_defaults`, `setdefault(`, 숫자 `.get` fallback, `or 숫자`, timeout/max 할당, schema `default`, 숫자 field 생성이며 추가로 AST의 함수 기본 인자·dataclass 초기값·SDK 요청 조립을 검토했다. 재현 스크립트는 `tests/llm/audit_configuration.py`다. grep 결과가 존재한다는 이유로 결함이라고 하거나, 테스트 통과를 감사의 대체로 삼지 않는다.

| 남은 패턴/위치 | 분류와 판단 |
|---|---|
| Vision 기본 Tesseract registry | 구현 등록만 수행. 호출/Project backend 미설정은 오류; auto/fallback 없음 |
| Vision stream=False/n=1, asset format_version=1, PNG 가공본 | 단일 분석 응답·출처 무결성·손실 없는 가공 결과 표현. enforced 메타데이터와 사용자 values 분리 |
| Vision backend options/limits/provider 빈 dict, timeout=None | 추가 옵션·제한 없음. SDK/native 옵션을 대신 채우지 않음 |
| Tesseract executable='tesseract', revision='1' | 호스트 실행 파일 탐색 및 구현 지문. backend 선택·품질·실행 기한 default가 아님 |
| providers/retry.py의 sdk_defaults | SDK에 호스트가 명시한 retry를 감지하는 플래그. default 설정 생성 함수가 아님 |
| ProjectConfig.parameters / merge(defaults, overrides) | 사용자가 제공한 상속값, 또는 인자 이름. 내장 leaf 생성 없음 |
| invoke max_attempts fallback1 / BaseEngine max_retries fallback0 | 정책이 없는 최초 호출을 표현. values/kwargs에 넣지 않음 |
| retry delay fallback0 | 대기 정책 부재, 추가 대기 없음. 명시 cap만 적용 |
| retention keep_runs fallback0 / reserve_tokens fallback0 | 추가 보호·예약 정책 부재. 삭제는 별도 명시 상한 및 apply 승인에 따름 |
| repair_attempts fallback0, rerank False | 기능 미활성. 설정값으로 materialize하지 않음 |
| Memory cache0/priority0 | 미설정 cache 비활성; 비활성 processor의 중립 순서. 실제 처리 기능 활성화 시 priority 명시 필요 |
| Graph buffer32/Loop·provider buffer8/Event buffer64 | 유실 없는 backpressure queue. timeout·drop·호출 거절 없음 |
| Graph cleanup5 / Memory close5 / process kill5 | 취소 회수 계약. 실행 deadline과 분리. Graph는 유한 명시 조정 가능 |
| OutputPolicy batch1/delay.025/chars65536 | flush 시점만 조정, 출력 자르지 않음 |
| index_stride128 / catalog4096 / conversation cache32 / log handle16 | 파생 인덱스·projection·핸들 eviction. 영속 원본은 유지 |
| log maxBytes0/backupCount0 | 회전 설정 없을 때 Python logging의 비회전 모드. 자동 삭제 없음 |
| stream/n fallbackTrue/1 | 다른 값을 거부하는 프로토콜 검증. request timeout 등과 무관 |
| setdefault: grouping, provenance, completions, checkpoints, transaction callbacks | 누적 자료구조 생성. 실행 정책 생성 없음 |
| graph/agent_usage, RAG positions[text] / evidence support | 원장·chunk ordinal·관계 근거 집계. 설정 아님 |
| lifecycle 상태/ID/시각/revision, output sequence0/append | 생성 레코드와 이벤트 프로토콜. 사용자 실행 설정과 구분 |
| Memory CRUD note/project/confirmed, tags[], source.kind=api | 새 데이터 레코드의 구조·출처 계약. 자동 Memory 처리는 별도 opt-in. 모델 쓰기 상태는 필수 설정 |
| ToolContract unknown/none/False, Tool error uncertain/False | 정책 부재와 효과 불확실성. 추가 권한·retry·approval 자동 생성 없음 |
| include_deleted=False, apply=False, retry=False | 삭제 레코드 격리 및 명시적 효과/재개 API 계약. 자동 삭제/재실행 없음 |
| path='.', offset0/start_line1 | 지정 루트/전체 결과의 시작. 숨은 부분 결과 제한 없음 |
| schema minimum/maximum/enum | 명시값의 허용 범위. runtime default를 생성하지 않음 |
| Agent output_format 미설정 / emit_text=False | JSON 변환 정책 비활성, 중간 노드 출력을 최종 대화로 자동 승격하지 않는 이벤트 계약 |
| Workflow join wait=all | 현재 유일하게 지원하는 join 프로토콜 검증. 다른 값은 거부하며 병렬 분기를 모두 합류 |
| 생성 title/workspace 경로, record revision, Step kind | 공개 생성 API의 이름·저장 위치·레코드 형식 계약. 모델/실행/검색/보관 정책을 생성하지 않음 |
| SDK/DB constructor | 설정되지 않은 옵션 생략. Chroma embedding_function=None만 이미 소유한 vector 계약 |
| domain operational log / SDK 콘솔 차단 | 호출 사실 관찰 및 ish 터미널 소유권. dotenv 및 하위 logger 포함. 기본 보관 삭제/회전 없음 |
| LITELLM_LOCAL_MODEL_COST_MAP=True | LiteLLM import 전에 외부 cost-map network fetch를 차단하고 bundled map을 선택하는 runtime isolation invariant. 재진입 시 복구. 사용자 inference option이나 ProjectConfig default/values가 아니며 override 불가 |
| DEFAULT_MAX_RETRIES=0 | LiteLLM compatibility invariant. import 전 env 및 매 진입 SDK global에 강제하며 사용자가 명시한 retry kwargs는 보존 |
| Project worker capture 1 MiB / protocol 8 MiB | IPC 메모리 경계. 출력 한도 초과는 tool_worker_output_limit으로 실패. Tool 실행 deadline을 추가하지 않음 |
| observability recent 256 / code 집계 128종 + other | 비영속 관찰 메모리 한도. 사용자 실행·저장 의미를 바꾸지 않음 |

제거한 항목: user default helper/Schema default, Loop/Graph/Tool/Run 제한 생성, Provider admission/wall/retry/stream fallback, RAG algorithm preset, Memory 자동 처리 preset, 파일·조회 truncation, workflow validation의 숨은 nesting32 cap, extraction temperature0 강제, LiteLLM 모드 환경 강제, Graph Agent의 purpose→system_prompt 자동 변환. 문자열/boolean fallback과 AST 기본 인자까지 확인하며 숫자 검색에만 의존하지 않는다.

UI metadata 감사: Loop의 system_prompt=null은 host의 명시적 해제로 x-host-override=true, source=host, editable=false, value=null이 일치한다. 미지정은 Project 값을 상속한다. Graph/Preparation의 nullable 옵션은 키 존재 여부로 판단하며 Pipeline은 단계별 schema/effective 결과를 보존한다. 동적 host prompt는 조회 중 실행하지 않고 host_runtime/unresolved로 표시한다.

Python API의 None은 null을 허용하는 설정 경계에서 명시적 해제다. Loop/Graph/Preparation/host Tool retry는 UNSET으로 누락과 구별한다. null을 지원하지 않는 RAG 알고리즘 설정은 오류이며 null을 임의의 숫자로 변환하지 않는다. SDK nullable 옵션은 실제 request에서 None을 보존한다.

외부 확장 컴포넌트의 임의 Python 실행을 감시하는 sandbox는 아니다. 공개 configuration schema에 default annotation을 넣으면 UI 계약 경계에서 거부한다. 사용자 JSON field 이름 `default`와 enum/const 데이터는 허용한다. 확장 코드는 같은 explicit-only 계약을 지켜야 한다.
