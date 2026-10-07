# Project 설정과 UI 연결

Project는 서비스가 집행하는 `policies`와 구현체에 전달하는 `parameters`를 저장한다.
전달 인자는 대상별 JSON 딕셔너리이며, 해당 구현체가 스키마와 의미를 소유한다.
Project는 SDK 옵션을 해석하거나 이름이 같은 인자를 여러 대상에 배포하지 않는다.

```python
config = ProjectConfig(
    policies={"run": {"timeout_seconds": 1800}},
    parameters={
        "engines": {"loop": {'policy': {'request_timeout': 300}, 'config': {'completion': {'model': 'openai/company-model', 'temperature': 0.2}}}},
        "components": {"vision": {'config': {'ocr': {'backend': 'tesseract'}}}},
    },
)
project = await backend.projects.acreate(
    "작업 공간", config=config, components=["vision"], conversation_storage="file",
)
session = await project.sessions.acreate("대화", config={
    "parameters": {"engines": {"loop": {'policy': {'request_timeout': None}}}},
})
request = await session.run.submit("안녕하세요", engine="loop")
```

숫자와 모델은 예제의 명시적 선택이다. Session의 null은 Project의 300초 제한을 해제한다.
Session에서 생략한 모델은 실행 시 Project 설정을 상속한다. 생성 시 복사하지 않는다.
Project의 최상위 completion/engines/component_configurations/session_defaults/default_engine은
지원하지 않으며 자동 변환도 하지 않는다. 기존 파일은 오류가 나도 그대로 보존한다.

## 두 쓰기 경로, 하나의 저장 원본

설정 원본은 `project.json`의 `config.parameters.components[name]`이다.
Component는 자신의 디렉토리 아래 자료를 관리하고 별도 설정 파일은 만들지 않는다.

```python
view = await project.adescribe_config()
config = ProjectConfig(view["project"]["config"])
config.parameters["engines"]["loop"]["config"]["completion"]["temperature"] = 0.1
await project.asave(config=config, expected_version=view["config_version"])

vision = await project.components.aget("vision")
await vision.aconfigure(
    {'config': {'ocr': {'backend': 'tesseract', 'backends': {'tesseract': {'language': 'eng'}}}}},
    expected_version=view["component_versions"]["vision"],
)
```

`asave`는 전체 ProjectConfig를, `aconfigure`는 해당 Component 설정 전체를 교체한다.
후자는 같은 Project 저장 경계의 잠금·검증·버전 비교를 사용한다. 부분 수정은 최신 사본을
읽고 수정하여 저장한다. 여러 Component를 함께 변경하려면 ProjectConfig를 한 번 저장한다.
실행 함수·클라이언트·DB 연결은 JSON에 넣지 않는다.

## 통합 조회

`backend.describe_project_config()`는 등록된 Engine/Component 스키마를 조합한다.
`project.adescribe_config()`은 잠금 아래 읽은 다음 정보를 함께 반환한다.

| 항목 | 내용 |
|---|---|
| project.config, values.config | 저장된 명시값의 독립 사본 |
| schema | 정책과 parameters.engines/components의 허용 키·형식 |
| effective_engines[name] | 구현체의 values/sources/overridden/editable |
| components[name].configuration | Component에 전달한 설정 |
| components[name].effective | 주입 client와 Project 설정을 해석한 결과 |
| config_version, component_versions | 저장 충돌 검사용 버전 |

조회가 없는 값을 채우거나 모델·준비 작업을 실행하지 않는다. JSON Schema default도 없다.
동적 host factory는 runtime/host_runtime으로 표시하며 실행 전 값을 추측하지 않는다.
Session override까지 보고 싶으면 `session.adescribe_config()`을 사용한다.
자세한 상속·강제값은 [설정 계약](../../llm/CONFIGURATION.md)에 있다.

## 애플리케이션의 생성·선택 정책

llm은 기본 Project/Session, 최근 선택, UI 기본 엔진, 초기 설정 템플릿을 관리하지 않는다.
일반 create/load/list/clone/delete와 명시적 `submit(engine=...)`만 사용한다.
Hub는 자기 `.hub/` 상태와 시작 잠금을 소유하고 공개 llm API로 첫 Project를 만든다.
다른 플러그인은 Hub에 의존하지 않고 자신의 정책을 구현할 수 있다. title/id/components/
conversation_storage는 그대로 Project의 수명 속성이며 parameters로 옮기지 않는다.
