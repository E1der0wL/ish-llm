# 설정 정적 감사 기록

범위는 `llm/` production Python 전체다. tests/examples는 명시적 호출자의 fixture로 따로 검사했다. 핵심 계약·강제값 전체 목록은 [CONFIGURATION.md](CONFIGURATION.md)에 있다.

검색은 `_defaults|default_configuration|provider_defaults|search_defaults|extraction_defaults`, `setdefault(`, 숫자 `.get` fallback, `or 숫자`, timeout/max 할당, schema `default`, 숫자 field 생성이며 추가로 AST의 함수 기본 인자·dataclass 초기값·SDK 요청 조립을 검토했다. 재현 스크립트는 `tests/audit_configuration.py`다. grep 결과가 존재한다는 이유로 결함이라고 하거나, 테스트 통과를 감사의 대체로 삼지 않는다.

| 남은 패턴/위치 | 분류와 판단 |
|---|---|
| providers/retry.py의 sdk_defaults | SDK에 호스트가 명시한 retry를 감지하는 플래그. default 설정 생성 함수가 아님 |
| ProjectConfig.session_defaults / merge(defaults, overrides) | 사용자가 제공한 상속값, 또는 인자 이름. 내장 leaf 생성 없음 |
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
| domain operational log / SDK 콘솔 차단 | 호출 사실 관찰 및 ish 터미널 소유권. 기본 보관 삭제/회전 없음 |

제거한 항목: user default helper/Schema default, Loop/Graph/Tool/Run 제한 생성, Provider admission/wall/retry/stream fallback, RAG algorithm preset, Memory 자동 처리 preset, 파일·조회 truncation, workflow validation의 숨은 nesting32 cap, extraction temperature0 강제, LiteLLM 비용표/모드 환경 강제, Graph Agent의 purpose→system_prompt 자동 변환. 문자열/boolean fallback과 AST 기본 인자까지 확인하며 숫자 검색에만 의존하지 않는다.

Python API의 None은 null을 허용하는 설정 경계에서 명시적 해제다. Loop/Graph/Preparation/host Tool retry는 UNSET으로 누락과 구별한다. null을 지원하지 않는 RAG 알고리즘 설정은 오류이며 null을 임의의 숫자로 변환하지 않는다. SDK nullable 옵션은 실제 request에서 None을 보존한다.

외부 확장 컴포넌트의 임의 Python 실행을 감시하는 sandbox는 아니다. 공개 configuration schema에 default annotation을 넣으면 UI 계약 경계에서 거부한다. 사용자 JSON field 이름 `default`와 enum/const 데이터는 허용한다. 확장 코드는 같은 explicit-only 계약을 지켜야 한다.
