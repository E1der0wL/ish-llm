# Memory — 장기 기억과 대화 처리

대화 원본과 별도로 재사용할 사실·선호·작업 기억을 저장하고 검색합니다. Project/Session 범위, 출처, revision과 후보 검토를 관리합니다. 모델 입력을 구성하는 공통 processor 계약에 연결되며 핵심 도메인에 Memory 전용 로직을 추가하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | MemoryComponent, MemoryData 및 관련 공개 타입을 노출합니다. |
| [component.py](component.py) | 기억 레코드·revision·출처·상태·설정과 저장 구조를 관리합니다. |
| [summary_storage.py](summary_storage.py) | Session 요약의 필수 존재 목록과 원자 저장·명시 삭제를 소유합니다. |
| [data.py](data.py) | 잠금과 Project 수명 검사 아래의 MemoryData CRUD·검색·검토 API입니다. |
| [processing.py](processing.py) | MemoryProcessor/MemorySession: 회상, 문맥·Tool 결과 축약, 요약·후보 추출을 조합합니다. |
| [compaction.py](compaction.py) | 애플리케이션의 명시적 압축 요청, 원본 해시 검증과 범위별 파생 요약입니다. |
| [tool_results.py](tool_results.py) | 완료 Tool/MCP 결과의 원문 참조·분할 요약과 모델 입력 사본 적용입니다. |
| [work_state.py](work_state.py) | 선택적 구조화 요약의 형식·검증·출처 데이터를 정의합니다. |
| [tools.py](tools.py) | 모델이 호출할 Memory Tool 정의와 실행 출처 연결입니다. |
| [consolidation.py](consolidation.py) | 검토된 병합 journal·완료 영수증의 존재 목록, 명시 복구와 원자 저장·복제를 소유합니다. |

## 읽고 쓸 때

`await project.components.aget("memory")`로 핸들을 얻습니다. 공통 이름의 create/load/list 외에 search, summary, review, consolidate 등 전문 API가 있습니다. 비동기 UI는 해당 a 접두사 API를 사용합니다.

기억 수정·삭제·복원·영구 제거·통합에는 마지막으로 읽은 `expected_generation`과 `expected_revision`이 모두 필요합니다. generation은 생성 세대이고 revision은 그 안의 변경 번호입니다. 같은 ID로 다시 생성한 기록에 과거 편집을 적용하지 못하도록 함께 검증합니다. Memory의 delete는 상태를 바꾸는 전문 API이므로 일반 JSON Component의 단순 파일 삭제와 구분하세요. candidate/confirmed 등 기억 상태, 출처와 세션 범위를 보존해야 합니다.

## 설정과 실행

검색에는 `config.search_strategy="keyword"` 또는 호스트가 주입한 `search_fn`이 필요합니다.
미설정이면 검색 시 configuration error입니다. keyword는 단어 부분 일치가 있는 레코드를
일치율 내림차순으로 반환합니다. 주입 검색기의 반환 ID는 0점/음수 점수도 보존합니다.
`config.min_score`를 명시한 경우에만 추가 cutoff가 적용됩니다. 자동 recall도 같은 경로입니다.

추출의 의미 지침은 Project의 `config.processing.extract_prompt_id`로 선택한 Prompt 레코드에서
공급합니다. MemoryComponent 생성자는 모델·검색·토큰 계수기 구현만 주입받습니다.
MemoryProcessor도 모델·계수기에 None 또는 callable만 받으며 falsey callable의 identity를
보존합니다. completion_fn이 None일 때만 현재 Provider 기본 함수를 선택합니다. 잘못된 타입을
기본 함수로 바꾸지 않습니다. 처리 우선순위는 정확한 int이고 명시한 0/음수도 보존합니다.
미설정 시 중립 정렬값은 MemoryProcessor가 선언하며, 활성 기능의 명시 priority 요구는 유지합니다.
둘 다 없으면 추출을 실행하지 않고 오류를 반환합니다. 처리기 시작 시 지침/버전을 고정하고
candidate metadata에 출처를 남깁니다. JSON 형태, replaces의 id/generation/revision, 근거 없는 성공 금지는
backend 계약입니다. 무엇을 기억할지는 애플리케이션이 결정합니다.

추출은 검색 전략과 독립적입니다. search_strategy 또는 host search_fn이 있을 때만 관련
기억을 먼저 정렬합니다. 없으면 record ID 순서로 기존 기록을 입력 예산 안에 담습니다.
추출만 켠 경우 recall_limit은 필수가 아닙니다. 실제 search/recall에는 기존대로 명시적인
검색 전략이 필요합니다. Memory CRUD와 Run/Step 저장 경계는 변하지 않습니다.

설정은 `parameters.components.memory`에 저장합니다. 모델 호출을 수반하는 요약·추출, 자동 회상, 문맥 축약 등은 명시한 정책에 따라 적용됩니다. `completion_processors` capability로 Loop 입력·관찰 경계에 연결하고, CRUD Tool은 `tools` capability로 제공합니다.

보조 모델의 SDK 인자는 `config.processing.completion`, 외부 호출의 시도·기한은
`policy.processing.provider`에 둡니다. provider의 max_attempts는 최초 호출을 포함하고,
wall_timeout/delay_seconds/max_delay_seconds는 명시했을 때만 적용합니다. Loop의 provider를
상속하지 않습니다. Loop 입력에 기억을 결합할 때는 해당 실행의 CompletionPolicy를 사용하므로
현재 입력 예산과 공통 Run/usage 제한을 그대로 지킵니다.

ConversationStore는 대화 원본, Memory는 선택·파생된 장기 기억, Run/Step은 실행 기록을 소유합니다. 하나를 다른 것의 대체 저장소로 사용하지 않습니다.

요약의 공통 알고리즘은 [../summarization.py](../summarization.py)에 있습니다. 과거 대화 요약,
활성 Tool 교환 압축이 `summarize()`를 사용하고, 명시적 압축과 큰 턴/Tool 결과의 조각 처리는
`summarize_fragments()`를 사용합니다. Memory는 계속 자료 선택·프롬프트·work_state 검증·원본 지문·
요약 저장·Step 기록·문맥 적용을 소유합니다. 현재 사용자 지시와 미완료 Tool 교환을 삭제하지 않습니다.
기존 자동 요약의 fingerprint·부분 cursor 계약은 유지합니다. 명시적 요청의 조각 커서는
별도로 원문·옵션·누적 요약의 무결성을 검증합니다.

공통 함수는 모델·정책을 생성하지 않으며 호출자가 명시한 최대 결과 문자 수와 모델 호출 함수를
받습니다. 분할 호출에는 입력 크기 계수기, payload 생성 함수, 최대 입력 크기, 최대 호출 횟수,
시작 offset과 이전 요약도 명시합니다. `SummaryResult`는 검증된 요약·처리 offset·호출 수·완료 여부와
다음 조각이 들어가지 않는 `blocked` 상태를 돌려주는 메모리상의 결과이며 영속 영수증이 아닙니다.
원본/설정 검증 뒤 어떤 결과를 저장·적용할지는 호출자 책임입니다.

Memory의 기존 `model_input_chars`는 이전 요약과 JSON payload를 포함한 문자 수입니다.
SDK 전체 요청의 토큰 한도로 재해석하지 않습니다. 다른 소비자는 전체 요청을 반영하는 계수기를
주입할 수 있습니다. 공통 함수의 계수기는 prefix 증가에 따라 단조 증가해야 하며, 최종 선택한
payload도 전송 전에 재검증합니다. 호출 예산이 소진되면 부분 결과를 반환하고 자동 반복하지 않습니다.
실패·취소는 전파하고 모델 iterator를 닫습니다. 선택적 progress callback은 성공한 조각의 독립 사본을
받으며 저장 경계를 소유할 수 있습니다. callback 실패도 재시도하지 않습니다. SDK 재시도·사용량
계측은 기존 모델 어댑터가 담당하므로 별도의 재시도 배수를 추가하지 않습니다.

Loop의 추가 지시는 원래 사용자 요청과 같은 턴으로 요약·보존합니다. 활성 Tool 이력을 압축할 때 추가 지시를 가로질러 삭제하지 않으며, 이 경우 압축을 건너뛰어 지시 원문을 유지합니다. 기존 토큰 한도는 그대로 적용됩니다.

필수 필드와 호출 예는 [Memory API](../../../docs/llm/memory.md), 처리 정책은 [Memory 처리](../../../docs/llm/memory-processing.md), processor 확장은 [공통 처리기](../../../docs/llm/completion-processing.md)에 있습니다.

[상위 안내](../README.md)

## 명시적 문맥 압축

`aget_context(session_id, scope=None)`로 마지막 Loop 입력 경계의 토큰·예산·과거 턴 참조와
요청 상태를 조회합니다. 계수기가 없으면 토큰 수는 `None`입니다. `describe_compaction()`은
요청 schema만 제공합니다. `arequest_compaction(session_id, options, expected_revision=..., scope=None)`은
요청을 저장하고 다음 Loop 입력 준비 경계에서 처리합니다. API 호출 자체는 모델을 실행하지 않습니다.
모델·프롬프트·최근 보존 턴·요약 크기·model_input_chars·max_summary_calls는 애플리케이션이 명시합니다.

Compactions는 compactions.manifest.json으로 문맥·Tool 요약의 존재·scope·revision을
검증합니다. 아직 생성하지 않은 상태만 비어 있으며, 소실된 요청·cursor·원본 참조를 빈 값으로
복원하지 않습니다. 기록과 목록, Tool 원본과 참조 생성은 같은 transaction입니다.
조각별 최신 누적 체크포인트를 저장하고 완료·검증 후에만 원자적으로 적용합니다.
부분 결과는 적용하지 않습니다. `aresume_compaction`은 같은 요청을 다음 입력 경계에 다시
예약하고 `acancel_compaction`은 미사용 예약 또는 다음 저장 경계 이후의 결과를 취소합니다.
진행 중 SDK 호출의 강제 종료는 보장하지 않습니다. Run 중단은 기존 interrupt를 사용합니다.
원본 삭제 보호를 해제하려면 요청을 완료/취소한 뒤 `adelete_compaction`으로 파생 상태를 삭제합니다.

원본 대화와 Run/Step/Tool 결과는 바꾸지 않습니다. 실패·취소·원본 변경 시 새 요약을 적용하지
않으며 자동 재시도하지 않습니다. 기존에 명시한 자동 Memory 처리와는 별개의 요청 경로입니다.
두 경로를 함께 사용하면 먼저 변경된 입력 때문에 source hash 검사가 압축을 거부할 수 있으므로,
애플리케이션에서 한 경로를 선택하는 것이 명확합니다.
[사용 예와 범위 계약](../../../docs/llm/long-running-capabilities.md)을 참고하세요.

## 큰 Tool/MCP 결과

`compress_tools` 미리보기의 원문 조회는 `memory_tool_result`에 `run_id`와 `step_id`를
명시합니다. 모델 Tool call ID는 중첩 Agent 간에 중복될 수 있어 조회 identity로 사용하지
않습니다. 완료 Step과 Session 소유권을 검사하며 원문을 읽기 위해 Tool을 재실행하지 않습니다.

`config.processing.tool_result_mode`를 reference/summary로 명시하면 완료 결과를 선택한
`tool_data={component, identifier}` 파일 패키지에 보존합니다. 결과 임계값·모델·지침·처리
예산은 명시적입니다. 기본 활성화, 기본 Data 패키지 생성, 자동 재시도는 없습니다.
기존 compress_tools 미리보기와 중복 선택할 수 없습니다.

원문 저장과 참조 생성은 한 트랜잭션이고 모델 호출은 잠금 밖입니다. 요청별 조각 저장 후
완성된 요약만 입력 사본에 적용합니다. 원본 Tool 결과와 Run 재개 영수증은 변경하지 않습니다.
`alist_tool_summaries`, `aresume_tool_summary`, `acancel_tool_summary`로 앱이 제어합니다.
모델의 `memory_read_source` 또는 앱의 `aread_source`는 Session에 귀속된 참조와 revision으로
문자 범위를 조회합니다. 참조가 있으면 Data 삭제를 막고, 변경된 파일을 과거 원문으로 사용하지
않습니다. `adelete_tool_summary`는 완료/취소한 참조만 해제하며 원문 삭제는 Data API 책임입니다.
