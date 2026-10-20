# Prompts — 시스템 지침과 few-shot 정의

모델에 전달할 메시지 묶음을 `messages`, `description`, `metadata`의 닫힌 JSON 계약으로 저장합니다. 실행·템플릿 평가·RAG 검증은 소비하는 Engine/Component가 담당합니다. 저장만으로 모든 모델 호출에 자동 적용되지는 않습니다. Application 확장은 `metadata`에 둡니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | PromptComponent 공개 import입니다. |
| [component.py](component.py) | messages의 형식과 역할을 검증하고 공통 JSON CRUD로 저장합니다. |

## 사용 예

다음 코드는 `prompts`, `rag`가 선택되고 RAG 모델·기본 처리 설정이 구성된 열린 Project에서 사용합니다.

```python
from llm.components.rag.prompts import default_prompt

prompts = await project.components.aget("prompts")
await prompts.acreate(default_prompt(), identifier="rag-triples")
record = await prompts.aload("rag-triples")
record["messages"].insert(0, {
    "role": "system",
    "content": "기술 문서의 명시적 관계만 추출하세요.",
})
await prompts.asave("rag-triples", record)

# configure는 전체 교체이므로 현재 RAG 설정을 읽어 변경한다.
rag = await project.components.aget("rag")
configuration = await rag.aget_config()
configuration.setdefault("config", {}).setdefault("extraction", {})["prompt_id"] = "rag-triples"
configuration.setdefault("policy", {}).setdefault("extraction", {})["repair_attempts"] = 2
await rag.aconfigure(configuration)
```

레코드의 `messages`는 비어 있지 않은 배열입니다. 각 항목은 system/user/assistant 중 하나의 role과 비어 있지 않은 문자열 content를 가집니다. description은 선언된 최상위 필드이며, 라벨 등 Application 확장은 metadata 안에 둡니다.

## 저장과 참조

정의는 `<project>/prompts/records/<id>.json`에 저장합니다. RAG가 사용할 ID는 `ProjectConfig.parameters.components.rag.config.extraction.prompt_id`,
수정 횟수는 같은 RAG 설정의 `policy.extraction.repair_attempts`에 둡니다.
prompt_id의 누락/null은 추가 Prompt 레코드를 사용하지 않는다는 뜻입니다. 추출 결과의 형식·근거를
규정하는 `EXTRACTION_CONTRACT`는 항상 적용되며, 의미 지침 샘플인 `default_prompt()`를 자동 선택하지 않습니다.
위 예제처럼 저장하고 ID를 지정했을 때만 그 샘플/사용자 지침을 추가합니다.

삭제·비활성화한 프롬프트를 참조하면 새 문서 준비가 모델 호출 전에 실패합니다. 설정 편집과 기존 문서 조회는 가능합니다. 준비 중 참조 프롬프트가 변경되면 결과 공개가 충돌로 거부됩니다.

일반 편집은 snapshot/expected_version으로 충돌을 확인할 수 있습니다. 동기 API도 제공하지만 UI에서는 위의 비동기 API를 사용하세요. [Component 공통 계약](../README.md), [RAG 설정](../rag/README.md)을 참고하세요.
