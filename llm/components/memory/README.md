# Memory — 장기 기억과 대화 처리

대화 원본과 별도로 재사용할 사실·선호·작업 기억을 저장하고 검색합니다. Project/Session 범위, 출처, revision과 후보 검토를 관리합니다. 모델 입력을 구성하는 공통 processor 계약에 연결되며 핵심 도메인에 Memory 전용 로직을 추가하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | MemoryComponent, MemoryData 및 관련 공개 타입을 노출합니다. |
| [component.py](component.py) | 기억 레코드·revision·출처·상태·설정과 저장 구조를 관리합니다. |
| [data.py](data.py) | 잠금과 Project 수명 검사 아래의 MemoryData CRUD·검색·검토 API입니다. |
| [processing.py](processing.py) | MemoryProcessor/MemorySession: 회상, 문맥·Tool 결과 축약, 요약·후보 추출을 조합합니다. |
| [work_state.py](work_state.py) | 선택적 구조화 요약의 형식·검증·출처 데이터를 정의합니다. |
| [tools.py](tools.py) | 모델이 호출할 Memory Tool 정의와 실행 출처 연결입니다. |
| [consolidation.py](consolidation.py) | 기억 통합 계획·승인·journal·중단 후 복구를 담당합니다. |

## 읽고 쓸 때

`await project.components.aget("memory")`로 핸들을 얻습니다. 공통 이름의 create/load/list 외에 search, summary, review, consolidate 등 전문 API가 있습니다. 비동기 UI는 해당 a 접두사 API를 사용합니다.

기억 수정·삭제·복원에는 마지막으로 읽은 `expected_revision`이 필요합니다. Memory의 delete는 상태를 바꾸는 전문 API이므로 일반 JSON Component의 단순 파일 삭제와 구분하세요. candidate/confirmed 등 기억 상태, 출처와 세션 범위를 보존해야 합니다.

## 설정과 실행

검색에는 `config.search_strategy="keyword"` 또는 호스트가 주입한 `search_fn`이 필요합니다.
미설정이면 검색 시 configuration error입니다. keyword는 단어 부분 일치가 있는 레코드를
일치율 내림차순으로 반환합니다. 주입 검색기의 반환 ID는 0점/음수 점수도 보존합니다.
`config.min_score`를 명시한 경우에만 추가 cutoff가 적용됩니다. 자동 recall도 같은 경로입니다.

추출의 의미 지침은 `config.processing.extract_prompt_id`로 선택한 Prompt 레코드 또는
`MemoryComponent(extract_prompt="...")`의 명시적 host 지침으로 공급합니다(host 우선).
둘 다 없으면 추출을 실행하지 않고 오류를 반환합니다. 처리기 시작 시 지침/버전을 고정하고
candidate metadata에 출처를 남깁니다. JSON 형태, replaces revision, 근거 없는 성공 금지는
backend 계약입니다. 무엇을 기억할지는 애플리케이션이 결정합니다.

설정은 `parameters.components.memory`에 저장합니다. 모델 호출을 수반하는 요약·추출, 자동 회상, 문맥 축약 등은 명시한 정책에 따라 적용됩니다. `completion_processors` capability로 Loop 입력·관찰 경계에 연결하고, CRUD Tool은 `tools` capability로 제공합니다.

보조 모델의 SDK 인자는 `config.processing.completion`, 외부 호출의 시도·기한은
`policy.processing.provider`에 둡니다. provider의 max_attempts는 최초 호출을 포함하고,
wall_timeout/delay_seconds/max_delay_seconds는 명시했을 때만 적용합니다. Loop의 provider를
상속하지 않습니다. Loop 입력에 기억을 결합할 때는 해당 실행의 CompletionPolicy를 사용하므로
현재 입력 예산과 공통 Run/usage 제한을 그대로 지킵니다.

ConversationStore는 대화 원본, Memory는 선택·파생된 장기 기억, Run/Step은 실행 기록을 소유합니다. 하나를 다른 것의 대체 저장소로 사용하지 않습니다.

Loop의 추가 지시는 원래 사용자 요청과 같은 턴으로 요약·보존합니다. 활성 Tool 이력을 압축할 때 추가 지시를 가로질러 삭제하지 않으며, 이 경우 압축을 건너뛰어 지시 원문을 유지합니다. 기존 토큰 한도는 그대로 적용됩니다.

필수 필드와 호출 예는 [Memory API](../../../docs/llm/memory.md), 처리 정책은 [Memory 처리](../../../docs/llm/memory-processing.md), processor 확장은 [공통 처리기](../../../docs/llm/completion-processing.md)에 있습니다.

[상위 안내](../README.md)
