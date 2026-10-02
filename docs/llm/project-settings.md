# 프로젝트 설정과 기본 프로젝트

UI가 사용할 키·자료형·기본값·제약은 `LargeLanguageModel.project_schema()`에서 조회한다.
`await project.aconfiguration()`에는 `schema`, 같은 구조의 `values`, `component_versions`가
추가되어 있다. 선택 Component/Engine에 따라 동적으로 구성된다.
[폼 구성·저장 예제와 운영 API](operations-and-ui-settings.md)를 참고한다.

## 검토 결과

프로젝트에서 연결된 Component 설정을 한눈에 관리하는 방식은 현재 구조와 잘 맞는다.
설정의 소유권과 검증은 Component에 두고, Project 서비스가 조회/수정을 조율한다.
코어 Project 도메인 객체에 모델 클라이언트나 실행 동작을 넣을 필요가 없다.

현재 저장 형식인 JSON을 유지한다. 컴포넌트 설정도 `project.json`의
`config.component_configurations`에만 저장한다. Component는 기본값·검증·해석을 담당한다. Component 내부 디렉토리 구조는 ProjectManager가 알지 않는다.

읽을 때 JSON을 역직렬화하고 ProjectConfig 및 각 Component의 검증을 거쳐 dict로 사용한다.
열린 JSON 키를 유지하므로 모든 설정을 고정 dataclass로 바꾸지 않는다. UI가 기본 폼을
자동으로 만들려면 이후 Component별 설정 JSON Schema와 표시용 설명을 추가할 수 있다.
YAML을 도입한다면 영속 원본을 하나로 유지하면서 가져오기/내보내기에 사용하는 것이 좋다.
현재 YAML codec이나 자동 UI 폼 생성은 구현하지 않았다.

## 프로젝트 전체 설정 조회

```python
view = await project.aconfiguration()
# 동기 코드: view = project.configuration()
```

반환 형태:

```json
{
  "project": {
    "id": "프로젝트 ID",
    "title": "Default project",
    "conversation_storage": "file",
    "config": {
      "completion": {},
      "engines": {},
      "session_defaults": {},
      "data": {},
      "default_engine": "loop"
    }
  },
  "components": {
    "tools": {
      "directory": "tools",
      "configuration": {"enabled": []}
    },
    "rag": {
      "directory": "rag",
      "configuration": {}
    }
  }
}
```

선택된 모든 Component를 한 번의 잠금 범위에서 읽어 분리된 사본으로 반환한다.
위 예시는 일부 Component만 표시했다. 반환 dict를 수정하는 것만으로 저장되지 않는다.
UI는 components 항목으로 설정 패널을 만들고, 저장 버튼에서 기존 담당 API를 호출한다.

```python
# 프로젝트 설정 전체 교체
settings = view["project"]["config"]
settings["completion"]["temperature"] = 0.2
await project.asave(config=settings)

# 한 Component의 설정 전체 교체
tools = await project.components.aget("tools")
configuration = view["components"]["tools"]["configuration"]
configuration["ui"] = {"label": "도구"}
await tools.aconfigure(configuration)
```

실행 함수·DB 연결·MCP connector 같은 런타임 객체는 직렬화하지 않는다.
RAG 모델 인자는 component_configurations.rag의 embedding_params/extraction_params/rerank_params로
전달하며 필요하면 기본 LiteLLM 클라이언트를 구성한다. 생성자에는 실행 구현만 주입한다.
여러 Component 설정도 ProjectConfig를 한 번 저장하여 원자적으로 변경할 수 있다.
외부 파일 수정 감시는 제공하지 않으며 변경 후 조회 API로 다시 읽는다.

## 기본 프로젝트 생성 및 재사용

```python
from llm.llm import LargeLanguageModel, ProjectConfig

async with LargeLanguageModel("./workspace") as backend:
    project = await backend.projects.aget_default(
        config=ProjectConfig(completion={"model": model_name}),
    )
    view = await project.aconfiguration()
    session = await project.sessions.acreate("첫 대화")
    request = await session.run.submit(
        "안녕하세요", engine=view["project"]["config"]["default_engine"],
    )
    run = await request.wait()
```

동기 호출은 `backend.projects.get_default()`다. 생성자는 여전히 파일을 만들지 않는다.
앱 시작 때 기본 프로젝트가 필요하면 시작 코루틴에서 `aget_default()`를 한 번 호출한다.
이 메서드는 모델 실행이나 Session 생성을 자동으로 하지 않는다.

최초 생성 기준:

* 설정에 `default_engine: "loop"`를 저장한다. 실행 시에는 여전히 `engine=`을 명시한다.
* 등록된 모든 Component를 선택한다. 기본 백엔드는 tools/skills/mcp/rag/agents/workflows/memory다.
* `components=[...]`로 백엔드 등록을 교체했다면 그 목록 전체를 선택한다.
* 대화 저장은 명시적으로 `file`을 기록한다. 백엔드의 새 프로젝트 기본값이 memory여도 같다.
* Component 디렉토리/기본 설정을 초기화한다. Tool 함수 활성화, 모델 선택/인증, MCP 접속,
  RAG 색인 생성은 자동으로 하지 않는다.

재호출/재시작 시 같은 프로젝트를 반환하며 config/title 인자는 생성할 때만 적용한다.
Component 선택, 대화 저장 방식 등 사용자가 바꾼 값도 덮어쓰지 않는다. 일반 create/acreate의
기본 선택은 바꾸지 않았다. 파일/메모리 선택은 기존 Session 존재 검사 규칙을 그대로 따른다.
사용자 대화 저장소 팩토리를 주입한 경우 file 강제가 충돌하므로 새 기본 프로젝트 생성을 거부한다.
`engines=`를 직접 지정했다면 `loop`라는 이름도 등록해야 이 API를 호출할 수 있다.

기본 프로젝트 ID는 `workspace/projects/default-project.json`에 저장한다. 생성 전에 ID를
예약하고, 완료 후 pending을 해제하여 생성 중단/포인터 저장 실패에도 중복 생성을 피한다.
미완료 초기화는 같은 프로젝트의 멱등 초기화만 마무리하며 Run을 재실행하지 않는다.
잘못된 참조 JSON은 자동 덮어쓰지 않고 오류로 보고한다.

소프트 삭제된 기본 프로젝트는 자동으로 복원하지 않는다. `projects.alist(include_deleted=True)`로
찾아 명시적으로 복원한다. 영구 삭제했다면 다음 기본 프로젝트 요청은 새 ID로 생성한다.
프로젝트 복제는 기본 프로젝트 참조를 복사하지 않는다.
