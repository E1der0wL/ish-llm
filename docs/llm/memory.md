# 프로젝트 장기 기억

`MemoryComponent`는 프로젝트에서 반복해서 참고할 선호, 사실, 업무 지침 등을 저장한다.
일반 컴포넌트와 같이 선택한 프로젝트에만 `memory/`를 만들고, 데이터는 열린 JSON 사전으로
관리한다. API 호출만으로 Run을 만들거나 LLM을 호출하지 않는다.

`conversation_storage="memory"`는 프로세스 종료 시 사라지는 **대화 저장 옵션**이다.
여기서 설명하는 장기 기억은 그 옵션과 관계없이 파일에 저장된다.

## 코드와 UI에서 사용

```python
from llm.llm import LargeLanguageModel

async with LargeLanguageModel(workspace) as backend:
    project = await backend.projects.acreate("업무", components=["memory"])
    memory = await project.components.aget("memory")
    # 동기 코드에서는 project.components.memory와 create/load/update 등을 사용한다.
    identifier = await memory.acreate({
        "content": "설명과 주석은 한국어로 작성한다.",
        "kind": "preference",
        "tags": ["언어", "코드"],
        "status": "confirmed",
        "metadata": {"owner": "user"},
        "custom_field": {"enabled": True},
    })
    record = await memory.aload(identifier)
    hits = await memory.asearch("한국어", limit=5)  # [{"score": ..., "memory": {...}}]
    record = await memory.aupdate(identifier, {"tags": ["응답"]},
                                  expected_revision=record["revision"])
    history = await memory.ahistory(identifier)
    records = await memory.alist(status="candidate")  # {ID: 레코드}
    deleted = await memory.adelete(identifier, expected_revision=record["revision"])
    restored = await memory.arestore(identifier, expected_revision=deleted["revision"])
```

API의 생성 기본값은 `status="confirmed"`, `kind="note"`, `scope="project"`, `tags=[]`다.
`kind`는 임의 문자열이며 사용자 필드도 추가할 수 있다. Session 전용 기억은
`scope="session", session_id=session.id`로 생성한다. 조회·수정·삭제에도 같은 `session_id`를 전달해야 하며,
기본 목록/검색은 프로젝트 공통 기억만 반환하고 `session_id` 지정 시 해당 Session의 기억도 포함한다.
모델 Tool의 session_id는 실행 문맥에서 결정한다. 전역 범위는 지원하지 않는다.
관리 필드 `id`, `revision`, `created_at`, `updated_at`, `deleted`, `source`는 컴포넌트가
작성한다. API의 `source={...}` 키워드로 외부 문서 등의 출처를 별도 전달할 수 있다.

변경에는 읽은 시점의 `expected_revision`이 필수다. 충돌 시 `MemoryConflictError`가 발생하고
쓰기는 수행하지 않는다. 최신 데이터를 다시 읽고 변경을 검토해야 하며 자동 덮어쓰기는 하지
않는다. `update`는 최상위 키를 병합한다. `save`는 관리 필드를 제외한 사용자 필드 전체를
교체한다(`content/kind/scope/status/tags`를 포함). 삭제·복원도 revision을 증가시킨다.

삭제한 기억은 기본 `load/list/search`에서 제외한다. 휴지통 UI는
`alist(include_deleted=True)` / `aload(id, include_deleted=True)`를 사용한다.
영구 제거는 삭제한 항목에만 `apurge(id, expected_revision=...)`로 수행하며 이력도 지워진다.

## 모델 Tool 연결

Memory를 선택하면 `tools` capability를 사용하는 Loop와 Graph에 아래 Tool이 자동 연결된다.
별도의 ToolComponent나 등록/활성화 메서드가 필요 없다. Engine 이름은 평소처럼 명시한다.

```python
session = await project.sessions.acreate()
request = await session.run.submit("내 응답 언어 선호를 기억에서 찾아줘.", engine="loop")
run = await request.wait()
```

| Tool | 역할 |
| --- | --- |
| `memory_search(query, limit?, status?)` | 기본적으로 확인된 기억을 키워드 검색 |
| `memory_get(identifier)` | 현재 값과 revision 조회 |
| `memory_create(content, kind?, tags?, metadata?)` | 새 기억 후보 생성 |
| `memory_update(identifier, expected_revision, changes)` | 기억 수정; 기본 정책에서는 후보로 전환 |
| `memory_delete(identifier, expected_revision)` | 복구 가능한 삭제 |

복원·영구 제거·승인은 API/UI로 제공한다. Tool 인자는 파일 경로, 다른 프로젝트 ID, 실행 출처,
승인 상태를 받지 않는다. 도메인 레코드는 열린 JSON이지만 모델에는 자주 쓰는 필드와 열린
`metadata` 사전만 노출한다. Agent가 이 Tool을 쓰려면 Agent의 `tools` 허용 목록에도 지정한다.

ToolExecutor는 기존 ToolPolicy의 허용 목록·승인·예산·시간 제한을 적용하고, 승인 Step 이벤트를
저장한 뒤 핸들러를 실행한다. Tool 결과는 LiteLLM의 tool 메시지로 전달되고 Step에도 남는다.
출처의 Project/Session/Run/입력 Message/Tool Step ID는 `current_tool_call()` 실행 문맥에서 가져온다.
같은 Run 안의 Agent/Graph도 같은 계약을 사용하며 Memory 전용 Run은 만들지 않는다.

`current_tool_call()`은 일반 Tool에서도 사용할 수 있는 실행 문맥 API다. 기본 핸들러와 같은
문맥에서 실행하는 주입 runner에 적용된다. 별도 프로세스/원격 runner는 전달받은 `ToolCall`을
자체 통신 계약으로 운반해야 한다. Memory 핸들러는 프로젝트 서비스에 연결된 로컬 핸들러이므로
ProcessToolRunner에 자동 직렬화되지는 않는다. 주입 runner에서 적절한 실행 경로를 구성해야 한다.

## 승인과 검색 설정

```python
await memory.aconfigure({
    "tool_write_status": "candidate",  # 또는 confirmed: 모델 작성도 바로 검색에 사용
    "search_status": "confirmed",     # candidate / confirmed / all
    "search_limit": 10,
    "max_search_results": 100,
    "ui": {"label": "장기 기억"},
})
# UI에서 기억 후보를 확인한 뒤 승인한다.
record = await memory.aload(identifier)
await memory.aupdate(identifier, {"status": "confirmed"}, expected_revision=record["revision"])
```

`configure`는 설정 전체 교체이며 생략한 동작 설정은 기본값을 사용한다. 기본 정책에서는
모델이 확인된 기억을 수정해도 `candidate`로 전환되므로 다시 확인하기 전에는 기본 검색에서
제외된다. `search(status="all")`이나 ID 조회로 후보를 명시적으로 볼 수 있다. `confirmed`는
검색 상태 표시일 뿐 사실 검증이나 보안 경계가 아니다.

검색은 Unicode 대소문자를 정규화한 단어 부분 일치다. 내용·kind·tags를 검색하고 점수와 ID로
정렬한다. 임베딩/RAG 의존성은 없으며 대량 기억의 색인 검색은 아직 없다. `score` 또는 `search`
를 재정의한 MemoryComponent를 등록하여 검색 전략을 바꿀 수 있다. `memory` capability는
수명 검사 핸들을 반환하므로 커스텀 엔진도 같은 API를 활용할 수 있다.

BaseEngine에 Memory 전용 로직을 추가하지 않는다. Loop의 범용 completion_processors 계약을
통해 Memory 자체가 자동 검색·Tool 결과 압축을 제공하며, 설정 시 증분 요약과 후보 추출도
수행한다. [장기 문맥 처리 설정](memory-processing.md)을 참고한다. 검색 결과는 참고 데이터다.
`expires_at`을 시간대가 있는 ISO 날짜로 지정하면 만료 후 검색에서 제외한다. 데이터는 남는다.

## 저장과 책임

```text
<project>/memory/
  identity.json
  records/
    <id>.json   # {record: 현재 값, history: 변경별 스냅샷 목록}
  contexts/
    <session-id>.json  # 원문 범위를 확인할 수 있는 최신 파생 요약 캐시
```

현재 값과 이력은 한 파일의 원자적 교체로 함께 공개한다. ComponentData의 기존 workspace
잠금과 수명 검사를 공유하고 비동기 API는 파일 I/O를 StorageIO로 넘긴다. Project clone과
백업은 삭제된 기록을 포함해 이력을 보존하며, clone의 원본 출처 ID는 그대로 유지한다.
기억 수정은 해당 파일의 전체 이력을 읽고 다시 쓰며 검색은 모든 기록을 순회한다. 큰 규모의
저장소에서는 별도 색인/이력 저장 전략이 필요하다.

Memory 데이터 저장과 Run/Step 기록은 별개 트랜잭션이다. Tool 효과 후 Run 기록 실패 시
기억은 이미 반영됐을 수 있으므로 출처와 이력을 확인한다. 실패한 Tool 효과를 자동 재시도하지
않는 기존 정책을 유지한다.

컴포넌트 설정은 `project.json`의 `config.component_configurations`에만 저장한다.
ComponentData.configure 편의 API도 이 설정을 갱신한다.
