# 설정의 저장 위치와 적용 순서

Project → Session → Run → Step 책임은 유지한다. 설정 해석은
`core/configuration.py`의 순수 병합 함수와 각 Engine/Component의 공개 설정 계약이 담당한다.
Engine은 여전히 이벤트만 생성하고, 설정 조회는 모델·준비 함수·Tool을 실행하지 않는다.

## 컴포넌트 설정의 단일 경로

컴포넌트 설정은 `ProjectConfig.component_configurations[등록 이름]`에만 저장한다.
`component.json` 읽기/쓰기, Component/Registry의 직접 configure와 ToolComponent의
직접 선택 변경 메서드는 제거했다. ComponentData의 configure 및 Tool 편의 API는
ProjectConfig를 검증하고 project.json을 원자적으로 저장하는 서비스 진입점이다.

```python
project = await backend.projects.acreate("작업", components=["rag", "memory"],
    config=ProjectConfig(component_configurations={
        "rag": {"chunk_size": 1200, "embedding_params": {"model": "사용할 모델"},
                "search": {"method": "hybrid", "limit": 8}},
        "memory": {"search_limit": 5},
    }))
view = await project.aconfiguration()
config = ProjectConfig(view["project"]["config"])
config.component_configurations["rag"]["search"]["limit"] = 10
await project.asave(config=config, expected_version=view["config_version"])
```

- 기본값 → Project 설정 순으로 병합한다. 생략한 항목은 기본값을 쓰며 배열은 교체한다.
- RAG 생성자는 모델 구현만 주입한다. chunk_size, document_kwargs, query_kwargs,
  각 batch_size, search_cache_chars는 ProjectConfig에서 전달한다.
- 주입 ModelClient의 인자는 클라이언트 기본값이다. Project의 모델 인자가 우선하며
  원본 클라이언트는 변경하지 않는다. JSON이 아닌 실행 객체는 계속 호스트에 주입한다.
- 미등록 이름과 잘못된 값은 저장 전에 거부한다. 등록만 하고 선택하지 않은 컴포넌트의
  설정도 보관할 수 있지만 디렉토리 생성이나 capability 실행은 하지 않는다.
- configure()는 해당 Project 설정 객체를 교체한다. 생략한 값은 기본값으로 돌아간다.
  부분 수정은 configuration()을 읽고 수정하거나 ProjectConfig를 편집한다.
- 설정 항목을 지우면 기본값으로 돌아간다. 비활성화는 설정을 보존하고 영구 제거는 삭제한다.
  복제에서 설정은 Project와 함께 복사하고 각 Component는 자기 레코드/자료만 복제한다.
- UI 입력 위치도 `schema/values.config.component_configurations` 한 곳이다.
  components[name].configuration/effective와 component_versions는 조회/충돌 검사에 사용한다.
- Session에서 컴포넌트 설정을 덮어쓰지는 못한다. 사용자 정의 Component도 같은 계약을 따른다.
- 기존 component.json이 있으면 설정을 조용히 무시하지 않고 오류를 낸다. 파일 내용은 보존한다.
  기존 설정은 ProjectConfig.component_configurations로 옮긴 후 해당 파일을 별도로 정리해야 한다.
  자동 이전, 호환 리더, 사용자 파일 자동 삭제는 제공하지 않는다.

policies.output의 batch_size/max_delay/max_chars는 Run 시작 정책 사본으로 적용한다.
null은 ServiceConfig.output_policy를 상속한다. 진행 중인 Run의 저장 정책은 바뀌지 않는다.

## Project 설정과 호스트 구성의 경계

모든 Python 생성자 인자를 ProjectConfig로 옮기지는 않는다. 현재 프로젝트별 사용자 설정은
completion, engines, policies, session_defaults, component_configurations로 전달한다.
임의 JSON 키가 저장된다는 사실만으로 런타임이 해당 키를 해석하는 것은 아니다.

다음은 의도적으로 별도 API에 남는다.

| 범위 | 구성 위치 | 이유 |
| --- | --- | --- |
| 컴포넌트 선택·대화 저장 방식·제목 | Project 생성/수명 주기 API | 초기화·저장 방식 변경 조건 검사가 필요 |
| Agent, Workflow 그래프, Skill, MCP 연결 정의 | 컴포넌트 레코드 CRUD | 설정값과 사용자 자료의 수명 분리 |
| Workflow 선택, 핸들러, 코드 revision | Engine/Agent 실행 정의 | 실행 대상을 정의하는 개발자 계약 |
| Tool 권한·실행기·격리·조건부 재시도 안전성 | ServiceConfig.tool_policy와 ToolContract | Project JSON으로 호스트 권한을 확대하지 않음 |
| 공급자 전체 동시 호출 제한·공유 캐시·색인 간격 | ServiceConfig | 여러 Project가 공유하는 백엔드 자원 |
| 저장소·로거·이벤트 처리기·모델 함수·MCP connector·토큰 계산기 | 호스트 객체 주입 | 실행 객체는 JSON으로 저장하지 않음 |

이 경계 안에서 설정 연결은 일관되지만, 사용자 제작 Engine/Component의 임의 옵션까지
자동으로 적용된다고 보장하지 않는다. 확장 구현은 자신의 schema와 실제 해석 계약을 제공해야 한다.

## 우선순위

저장 전에 등록 Engine의 동기 `configuration()` 계약을 검사한다. Project 생성/저장과
Session 기본 설정/생성/저장에 같은 검증기를 사용한다. 모델·준비 함수·Engine 실행은 호출하지 않는다.
스키마를 선언한 Engine은 먼저 Project/Session 입력 자체를 같은 UI 스키마로 검사한다.
예를 들어 호스트가 max_iterations=4로 고정해도 Project에 0을 저장할 수 없다.
호스트 우선순위는 유효한 입력에 대해 유지된다. 확장 Engine의 입력 스키마에서 required는
해당 설정 객체를 명시할 때 반드시 제공해야 하는 값이며, 상속 옵션은 선택 필드로 선언한다.
`configuration()`이 없는 확장 Engine은 `configuration_schema()`로 명시된 값을 검증한다.
호스트에 아직 등록하지 않은 Engine 및 검증 계약이 없는 확장의 실행 의미는 추론하지 않는다.
독립 ProjectManager 사용자는 `configuration_validator=registry.validate_configuration`을 연결한다.

UI의 `values.config.component_configurations`는 기본값을 병합한 폼 값이며
`project.config`는 원본 설정이다. JSON Schema 자체가 기본값을 삽입하는 것은 아니다.
중첩 객체를 일부만 편집했다면 아래 미리보기로 병합·의미 검증 후 반환한 `values`에
스키마를 적용한다. required, 배열 항목, 로컬 `$ref` 조건을 약화시키지 않는다.
여러 Engine이 설정 키를 공유하면 UI 스키마는 `allOf`로 모든 조건을 요구한다.

```python
current = await project.aconfiguration()
candidate = current["project"]["config"]  # 전체 ProjectConfig 사본; 부분 패치 API가 아님
candidate["component_configurations"]["rag"] = {"search": {"limit": 5}}
preview = await project.avalidate_configuration(candidate, expected_version=current["config_version"])
# 검증/병합만 수행하며 저장하지 않는다. 편집 시작 이후 변경도 버전으로 검사한다.
# preview["values"]를 preview["schema"]로 검증하거나 폼에 표시
await project.asave(config=preview["project"]["config"], expected_version=preview["config_version"])
```

미리보기 후 동시 수정이 있으면 저장은 충돌로 거부한다. 미리보기 원본을 저장하면
기본값 상속을 유지하며, 폼의 병합된 전체 값을 저장하면 해당 기본값도 명시적인 설정이 된다.

Engine의 값은 **기본값 → Project → Session → Agent → 호스트 명시값** 순으로 적용한다.
뒤의 값이 우선하며 JSON 객체는 재귀 병합하고 배열·스칼라는 교체한다.
`ProjectConfig.session_defaults`는 Session 생성 때 복사하는 템플릿이다. 이미 존재하는 Session에
나중에 변경된 템플릿을 다시 적용하지 않는다.

- Project: `config.engines[설정키]`, 공통 공급자 인자는 `config.completion`.
- Session: `session.config.engines[설정키]`, `session.config.completion`.
- Agent: `engine_options`, `completion`, `system_prompt`/`purpose`.
- 호스트: Engine 생성자에 **명시적으로** 전달한 값. Project/Agent로 해제하지 못한다.
- `settings_name`을 지정하면 등록 이름 대신 해당 키를 조회한다. 모든 Run submit에는 여전히 Engine 이름이 필요하다.
- Tool 권한·격리·호스트 자원 상한은 기존 실행 계약이 계속 강제한다. 위 설정 병합은 승인 권한을 부여하지 않는다.

Loop Agent의 별도 8회/60초 기본값은 제거했다. 일반 Loop와 같은 기본값
(999회, 요청 7200초, Tool 3600초)을 사용하며 Agent별 제한은 `engine_options`로 명시한다.
Agent 값이 호스트의 명시값을 덮어쓰던 동작도 제거했다. 모델 인자와 프롬프트도 같은 우선순위다.
호스트에서 프롬프트를 고정하면 Agent 프롬프트/Skill을 조합한 값보다 우선하므로,
Agent 지침을 사용할 경우 호스트 `system_prompt`를 고정하지 않는다.

Graph의 기본값은 기존 1000노드/병렬 8개/300초를 유지한다. `timeout_seconds: null`은
Graph 자체 제한을 해제하며, Project Run 제한이나 상위 Graph 제한은 계속 적용된다.
하위 Graph Agent에도 동일하게 적용하고 재개 바인딩에 최종 설정을 포함한다.
Workflow ID·핸들러·코드 revision·config_keys는 코드/Agent 정의의 실행 계약으로 남긴다.

```python
project = await backend.projects.acreate(config={
    "completion": {"model": "사용할 모델"},
    "engines": {
        "loop": {"max_iterations": 20, "request_timeout": 120},
        "graph": {"max_steps": 500, "max_parallelism": 4, "timeout_seconds": None},
    },
})
```

## Pipeline

기존 단계 목록은 `"0"`, `"1"` 등의 키를 사용한다. 이름으로 관리하려면 Mapping을 전달한다.
단계별 설정은 `engines[Pipeline 설정키].stages[단계 이름]`에 둔다.
단계에 `settings_name`이 있으면 해당 설정을, 없으면 Pipeline의 공통 Engine 설정을 먼저 사용한다.
그 위에 이름별 단계 설정을 병합하고, 호스트 고정값이 최종 우선한다.
단계가 공개 `configuration_schema/configuration` 계약을 제공하지 않으면 UI에 `runtime_only`로 표시한다.

```python
pipeline = PipelineEngine({
    "prepare": PreparationStep("문서 준비", prepare),
    "answer": LoopEngine(),
})
# backend에 engines={"work": pipeline}로 등록
config = ProjectConfig(engines={
    "work": {"stages": {
        "prepare": {"timeout_seconds": 180},
        "answer": {"max_iterations": 12},
    }},
})
```

## RAG

`ProjectConfig.component_configurations["rag"]`에서 분할 크기·임베딩/추출 배치·색인 배치·검색 캐시·
검색 기본값·Kuzu 자원을 관리한다. 모델 클라이언트는 런타임 객체이며, 그 호출 인자는
`embedding_params/extraction_params/rerank_params`로 저장할 수 있다.
클라이언트를 주입하지 않아도 해당 모델 인자를 설정하면 기본 LiteLLM 클라이언트를 구성한다.
사용자 클라이언트는 `configured(params)`를 제공해야 프로젝트 모델 인자를 받을 수 있다.
빈 모델 인자만 사용하는 기존 사용자 클라이언트의 호출 계약은 유지한다.

```python
rag = await project.components.aget("rag")
await rag.aconfigure({
    "chunk_size": 1200,
    "embedding_batch_size": 64,
    "extraction_batch_size": 16,
    "index_batch_size": 128,
    "search_cache_chars": 2_000_000,
    "embedding_params": {"model": "사용할 임베딩 모델"},
    "extraction_params": {"model": "사용할 관계 추출 모델"},
    "search": {
        "method": "hybrid", "expand": "section", "limit": 5,
        "rerank": False, "candidate_count": 40, "rrf_constant": 60,
        "max_hops": 2, "relation_limit": 30,
    },
    "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2},
})
result = await rag.asearch("질문")  # 저장된 검색 기본값 적용
result = await rag.asearch("질문", limit=3)  # 이번 검색에서만 변경
```

`configure`는 기존처럼 설정 객체를 교체한다. 부분 수정 시에는 `aconfiguration()`으로 읽은
객체를 수정해 저장하거나, `snapshot()`의 version으로 충돌을 검사한다.
RAG 생성자의 분할/배치 설정 인자는 제거했다. 모델 클라이언트의 기본 인자보다
ProjectConfig로 전달한 모델 인자가 우선한다.
`document_kwargs/query_kwargs`는 임베딩 호출별 인자이며 기본 모델 인자에 추가 적용한다.
검색 모델명은 색인 모델과 일치해야 한다.

설정은 작업별 사본에 적용한다. 준비/검색 도중 유효 설정이 바뀌면 충돌로 거부한다.
영속 색인 작업의 완성된 준비 결과도 설정 해시를 확인한다. 변경된 설정으로 재시도하려면
새 작업을 등록한다. 기존 문서를 자동 재임베딩하지 않으며, 다른 모델/차원의 벡터를
동일 코퍼스에 섞는 기존 금지 규칙도 유지한다.
직접 관계 검색도 `search.max_hops/relation_limit` 기본값을 사용한다.

## UI 조회

```python
view = await project.aconfiguration()
loop = view["effective_engines"]["loop"]
print(loop["values"], loop["sources"], loop["editable"], loop["overridden"])
rag_view = view["components"]["rag"]["effective"]
session_view = await session.aconfiguration()  # Session 변경까지 반영
```

`sources/editable/overridden`의 키는 `/max_iterations`, `/search/limit` 같은 JSON Pointer다.
`values`는 적용값, `sources`는 출처, `overridden`은 앞선 계층의 가려진 값이다.
Loop의 공급자 인자는 별도의 `completion` 조회에 같은 형식으로 담는다.
동적 공급자 인자 함수는 `runtime`과 `completion.resolved=False`로 표시한다.
동적 시스템 프롬프트는 값을 추측하지 않고 `host_runtime` 출처와 편집 불가를 표시한다.
Agent로 바인딩한 엔진의 `configuration(config, name, session_config=...)`도 같은 계약을 제공하며
실제 Agent Step의 binding에는 해당 해석 결과가 포함된다.

`ServiceConfig`의 저장소/로그 출력 객체·SDK 함수·프로세스 실행기와 호스트 공유 자원은
런타임 주입을 유지한다. 이들을 JSON 설정인 것처럼 표시하거나 직렬화하지 않는다.
등록 후 Engine/RAG 객체의 내부 필드를 직접 바꾸는 대신 Project/Component API로 설정한다.
호스트 고정값 자체를 변경하려면 실행을 정리한 뒤 새 설정으로 Engine/백엔드를 구성한다.
