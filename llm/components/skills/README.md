# Skills — 재사용할 지침과 참고 자료

특정 작업에 사용할 instructions와 선택적인 리소스 설명을 열린 JSON 레코드로 저장합니다. Skill 자체가 별도 실행기나 Python 함수 패키지는 아닙니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | SkillComponent 공개 import입니다. |
| [component.py](component.py) | 정의 schema와 skills/tools capability를 선언합니다. |
| [tools.py](tools.py) | Run 스냅샷의 요약 목록·본문을 조회하는 skill_list/skill_read를 제공합니다. |

## 사용 예

```python
skills = await project.components.aget("skills")
await skills.acreate({
    "instructions": "변경 전후 동작을 비교하고 근거를 함께 제시하세요.",
    "description": "코드 검토 지침",
    "title": "코드 검토", "tags": ["code", "review", "검토"],
}, identifier="review-guide")
```

저장 위치는 `<project>/skills/records/<id>.json`입니다. Agent의 `resources.skills`에 ID를 나열해 연결합니다. 소비하는 Engine/Agent가 지침을 적용하며, 저장만으로 모든 모델 호출에 자동 주입되지 않습니다.

## LLM이 지침을 선택하는 경로

Project에서 `skills`를 선택하면 tools capability에 읽기 전용 Tool 두 개가 연결됩니다.
별도의 ToolComponent나 enable_search 같은 메서드는 필요하지 않습니다.

- `skill_list({query?, after?, limit?})`: ID 순서의 제목·설명·태그·revision을 반환합니다.
  인자 없이 호출하면 전체 요약입니다. 본문은 포함하지 않습니다. query의 공백으로 나뉜
  모든 단어가 ID/제목/설명/태그에 있어야 일치하며, 대소문자는 구분하지 않습니다.
  다음 페이지가 있으면 next_after가 나오고 같은 query와 함께 after로 전달합니다.
- `skill_read({identifier, expected_revision?})`: `{id, revision, definition}`을 반환합니다.
  definition에는 instructions와 사용자가 저장한 추가 필드가 들어갑니다.

일반 Loop는 이 Tool들을 바로 사용할 수 있습니다. Graph Agent에서는 Agent의 `tools` 허용
목록에 이름을 추가해야 하며, 추가하면 해당 Project의 전체 Skill 목록·본문 조회를 허용합니다.
특정 Skill만 정적으로 적용하려면 기존 `resources.skills`를 사용합니다. 두 경로 모두 기존
ToolPolicy·승인과 Run/Step 경계를 유지하며 Skill은 Tool 권한을 추가하지 않습니다.

Run 시작 시 정의를 복사합니다. 실행 중 편집은 다음 Run에 반영되며 같은 Run의 목록/본문에
다른 버전이 섞이지 않습니다. 전체 지침 지문은 ToolContract revision에 포함되어 변경 후
옛 체크포인트 재개를 거부합니다. Tool 결과는 기존 Step에 기록하고 별도 Skill 실행 이력은
만들지 않습니다. expected_revision이 다르면 현재 Run의 목록을 다시 조회해야 합니다.

시스템 프롬프트에 "먼저 skill_list로 적합한 지침을 찾고 skill_read로 읽은 뒤 실제 결과를
검증하라"는 사용자의 지침을 명시할 수 있습니다. 라이브러리가 숨은 프롬프트를 주입하거나
Skill 준수를 보장하지 않습니다. 강제 검증·승인은 Workflow와 실행 정책으로 구성합니다.
[개발·진단 예제](../../../examples/llm/developer_assistant.md)는 설치를 명시한 두 지침을 제공합니다.

`resources`의 추가 JSON을 실제 파일/네트워크 작업으로 해석하려면 소비자 구현이 필요합니다. 공통 CRUD·설정은 [Component 계약](../README.md)을 따릅니다.

[상위 안내](../README.md)
