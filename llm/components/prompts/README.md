# PromptComponent

`prompts`는 모델 지침·few-shot 메시지를 열린 JSON 레코드로 관리한다.
실행·템플릿 평가·RAG 검증을 소유하지 않는다. 새 기본 Project에 함께 선택된다.

```python
from llm.components.rag.prompts import default_prompt

prompts = project.components.prompts
prompts.create(default_prompt(), identifier="rag-triples")
record = prompts.load("rag-triples")
record["messages"].insert(0, {"role": "system", "content": "기술 문서의 명시적 관계만 추출하세요."})
prompts.save("rag-triples", record)

# configure는 전체 설정 교체이므로 기존 설정을 보존해서 전달한다.
rag = project.components.rag
settings = rag.configuration()
settings.setdefault("extraction", {}).update(prompt_id="rag-triples", repair_attempts=2)
rag.configure(settings)
```

비동기 UI에서는 `acreate/aload/asave/aconfigure`를 사용한다. 공통 ComponentData의
snapshot/expected_version으로 편집 충돌도 검사할 수 있다.
레코드 위치는 `<project>/prompts/records/<id>.json`, 선택과 재수정 설정은
`ProjectConfig.component_configurations.rag.extraction`이다.
prompt_id가 null이면 RAG 내장 기본 지침을 사용한다. 삭제/비활성화한 프롬프트를
참조하면 새 문서 준비는 모델 호출 전에 실패하며 설정 편집과 기존 문서 조회는 가능하다.
사용 중 프롬프트를 바꾸면 진행 중 준비 결과의 공개가 충돌로 거부된다.

필수 `messages`는 비어 있지 않은 배열이며 각 메시지는 `role`과 `content`를 가진다.
role은 system/user/assistant, content는 비어 있지 않은 문자열이다.
레코드 최상위에는 description·버전·라벨 등 JSON 메타데이터를 자유롭게 추가할 수 있다.
다른 Engine/Component도 선택한 프롬프트 정의를 가져와 사용할 수 있으며 자동 주입되지는 않는다.
