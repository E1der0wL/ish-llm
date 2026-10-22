# Skills — 재사용할 지침과 참고 자료

표준 Agent Skills 디렉터리를 직접 저장합니다. SKILL.md의 YAML frontmatter와 Markdown 본문이
개별 Skill의 유일한 정의 원본입니다. Skill 자체는 실행기가 아닙니다.
표준 metadata는 문자열 키·값이며, Project별 출처·관계는 별도 운영 기록입니다.
[표준 전환 계약](../../../docs/llm/agent-skills.md)을 따릅니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | SkillComponent 공개 import입니다. |
| [component.py](component.py) | Skills 저장 Owner와 skills/tools capability를 연결합니다. |
| [records.py](records.py) | 운영 관계와 메모리 Projection schema를 소유합니다. |
| [storage.py](storage.py) | 표준 디렉터리와 필수 운영 증거를 원자적으로 저장합니다. |
| [SkillDocument](../../core/skills.py) | 표준 frontmatter·이름·본문 검증과 원문 보존입니다. |
| [data.py](data.py) | 파일 API, 버전 참조 검증·조회, fork와 삭제 보호입니다. |
| [tools.py](tools.py) | Run 스냅샷의 목록·본문·참고 자료를 조회하는 읽기 전용 Tool입니다. |

파일 스트림·경로 검증은 [files.py](../files.py), 원자성은 기존 workspace transaction을 재사용합니다.

## 사용 예

```python
skills = await project.components.aget("skills")
await skills.acreate({
    "markdown": "---\nname: review-guide\ndescription: 코드 검토 지침\n---\n변경 전후 동작을 비교하고 근거를 함께 제시하세요.",
}, identifier="review-guide")
snapshot = await skills.asnapshot("review-guide")
await skills.awrite_markdown("review-guide", snapshot["data"]["markdown"],
                            expected_revision=snapshot["version"])
```

저장 위치는 `<project>/skills/<name>/SKILL.md`이며 참고 파일은 같은 디렉터리에 있습니다.
`_state/records/<name>.json`에는 정의 digest·자료 목록·명시적 참조·계보·출처를 보관합니다.
정의 본문이나 frontmatter를 JSON 원본으로 중복 저장하지 않습니다. 필수 목록·기록·사용량
영수증이 소실되면 빈 값으로 복구하지 않고 거부합니다. 사용량은 `_state/usage/`에 있습니다.
Agent의 `resources.skills`에 ID를 나열해 연결합니다. 선택한 본문만 적용하며 자동 권한 부여는 없습니다.

`import_directory/aimport_directory`는 표준 디렉터리의 원문·파일·권한·빈 디렉터리를 보존합니다.
`export_directory/aexport_directory`는 현재 snapshot의 `expected_revision`과 존재하지 않는
같은 이름의 대상 경로를 받습니다. 운영 기록은 내보내는 표준 패키지에 섞지 않습니다.
Project backup은 운영 기록도 보존합니다. `read_markdown/aread_markdown`과
`write_markdown/awrite_markdown`으로 원문을 편집합니다. 쓰기는 `expected_revision`이 필수입니다.
원본 변경·이름 불일치·링크·특수 파일·구 skill.json 구조를 거부하며 자동 변환하지 않습니다.

파일 API는 `write_file/awrite_file`, `import_file/aimport_file`, `read_file/aread_file`,
`list_files/alist_files`, `delete_file/adelete_file`입니다. 변경에는 snapshot의 version을
`expected_revision`으로 전달합니다. 파일 목록·해시·크기·권한은 운영 기록의 `files` 필드에 기록되고
정의와 함께 하나의 revision을 구성합니다. manifest는 직접 편집하지 않습니다.
SKILL.md는 generic file API로 변경하거나 삭제할 수 없습니다.
`resources: [{"path": "references/manual.md"}]`는 등록된 로컬 파일을 참조합니다.
`skill_read_resource(identifier, index)`는 선택한 자료를 필요한 시점에 읽으며 Run의 revision이
바뀌면 거부합니다. 외부 URI는 자동 열기/다운로드하지 않습니다. scripts 파일도 자동 실행하지 않습니다.
표준 allowed-tools를 보존하지만 실행 allowlist·승인을 대체하지 않습니다.
복제·백업에는 파일이 포함되며 Skill 삭제는 자체 파일만 삭제합니다. 참조 중인 로컬 파일을
삭제하려면 먼저 resources에서 참조를 제거해야 합니다. 옛 records 디렉터리는 자동 변환하지 않습니다.
[Revision 계약](../../../docs/llm/revisions.md)을 참고하세요.

## Tool·Data 참조

`resources`는 다음 네 형태만 허용합니다. 각 항목에 description과 metadata를 추가할 수 있습니다.

| 형태 | 의미 |
| --- | --- |
| `{"uri": "https://example.org/manual"}` | 참고 URI. 자동 접근하지 않음 |
| `{"path": "references/manual.md"}` | 해당 Skill에 등록한 파일 |
| `{"component": "data", "identifier": "manual", "revision": "<snapshot version>", "path": "reference.md"}` | Data 패키지의 지정 버전 파일 |
| `{"component": "tools", "identifier": "helper", "revision": "<snapshot version>"}` | Project Python Tool 패키지의 지정 버전 |

Tool 참조 조회는 참조 정보만 반환합니다. prepare/import/enable/execute를 호출하지 않으며,
실제 Tool 사용에는 기존 선택·허용 목록·승인 절차가 필요합니다. builtin Tool 참조는 이 형태로 지원하지 않습니다.
Data 참조는 파일을 읽으며 `skill_read_resource`는 UTF-8 텍스트 자료를 대상으로 합니다.
바이너리 자료는 앱에서 Data의 read_file로 조회합니다.

create/save/update 및 Run capability 구성 시 대상이 존재하고 revision이 일치하는지 검사합니다.
현재 선택된 Skill의 모든 저장 참조를 검사하므로 사용하지 않는 Skill의 낡은 참조도 Run admission을
막을 수 있습니다. 대상 수정은 허용하지만 Skill의 pinned revision은 자동 갱신하지 않습니다.
새 snapshot을 확인한 뒤 Skill resources를 명시적으로 수정해야 합니다.

참조 중인 Data 파일·패키지 및 Tool 패키지는 삭제할 수 없습니다. Skill Component를 선택 해제해도
저장된 참조의 삭제 보호는 유지합니다. 참조를 유지하는 소비자가 선택된 상태에서는 대상 Component를
선택 해제할 수 없습니다. URI는 외부 설명이므로 이러한 삭제 보호의 대상이 아닙니다.
전체 사용 예는 [skill_data.py](../../../examples/llm/skill_data.py)에 있습니다.

`await skills.aimpact(id)`는 현재 Agent 정의에서 해당 Skill을 참조하는 ID/revision을 계산합니다.
선택 해제된 Agent Component의 보존된 참조도 표시하며, 조회가 Component를 다시 활성화하지는 않습니다.
삭제 보호와 동일한 저장 참조를 사용하고 별도 역참조 인덱스는 없습니다.
FORK는 `lineage={"parent": id, "parent_revision": hash}`를
저장하며 생성 시 부모 버전을 검사합니다. lineage는 생성 후 바꾸지 않고 부모 업데이트를
자식에 자동 전파하지 않습니다. 모델 기반 UPDATE/CREATE/FORK는 [Refinement](../refinement/README.md)를 사용합니다.
새 Skill 생성과 Agent 연결은 서로 다른 승인 대상입니다.

`await skills.adependencies(id)`는 `{"agents": [{"agent_id", "revision"}],
"children": [{"skill_id", "version"}]}` 형태로 각각의 원본 정의를 읽어 반환합니다.
직접 delete와 Refinement CREATE/FORK rollback은 두 목록 중 하나라도 있으면 거부합니다.
먼저 Agent 참조/자식 Skill을 명시적으로 제거해야 합니다. 자동 cascade/unbind는 없습니다.

## LLM이 지침을 선택하는 경로

Project에서 `skills`를 선택하면 tools capability에 읽기 전용 Tool 세 개가 연결됩니다.
별도의 ToolComponent나 enable_search 같은 메서드는 필요하지 않습니다.

- `skill_list({query?, after?, limit?})`: ID 순서의 name·description·revision을 반환합니다.
  인자 없이 호출하면 전체 요약입니다. 본문은 포함하지 않습니다. query의 공백으로 나뉜
  모든 단어가 name/description에 있어야 일치하며, 대소문자는 구분하지 않습니다.
  다음 페이지가 있으면 next_after가 나오고 같은 query와 함께 after로 전달합니다.
- `skill_read({identifier, expected_revision?})`: `{id, revision, definition}`을 반환합니다.
  definition에는 markdown 원문과 resources·파일 목록 등 운영 정보의 Projection이 들어갑니다.
- `skill_read_resource({identifier, index})` 또는 `{identifier, path}`: 명시적 resources 한 항목이나
  Run snapshot에 등록된 상대 경로 파일을 조회합니다. index와 path는 함께 받을 수 없습니다.
  파일은 `{text, sha256, bytes, ...}`로 반환하고 Tool/URI는 참조 정보만 반환합니다.
  Run 시작 시 Skill revision과 대상의 pinned revision을 함께 검사합니다.

일반 Loop는 이 Tool들을 바로 사용할 수 있습니다. Graph 안의 behavioral Agent에서는 `tools` 허용
목록에 이름을 추가해야 하며, 추가하면 해당 Project의 전체 Skill 목록·본문 조회를 허용합니다.
특정 Skill만 정적으로 적용하려면 기존 `resources.skills`를 사용합니다. 두 경로 모두 기존
ToolRuntime·승인과 Run/Step 경계를 유지하며 Skill은 Tool 권한을 추가하지 않습니다.

Run 시작 시 정의를 복사합니다. 실행 중 편집은 다음 Run에 반영되며 같은 Run의 목록/본문에
다른 버전이 섞이지 않습니다. 전체 지침 지문은 ToolContract revision에 포함되어 변경 후
옛 체크포인트 재개를 거부합니다. Tool 결과는 기존 Step에 기록하고 별도 Skill 실행 이력은
만들지 않습니다. expected_revision이 다르면 현재 Run의 목록을 다시 조회해야 합니다.

시스템 프롬프트에 "먼저 skill_list로 적합한 지침을 찾고 skill_read로 읽은 뒤 실제 결과를
검증하라"는 사용자의 지침을 명시할 수 있습니다. 라이브러리가 숨은 프롬프트를 주입하거나
Skill 준수를 보장하지 않습니다. 강제 검증·승인은 Workflow와 실행 정책으로 구성합니다.
[개발·진단 예제](../../../examples/llm/developer_assistant.md)는 설치를 명시한 두 지침을 제공합니다.

참고 파일은 필요할 때만 읽습니다. 전체 자료를 프롬프트에 자동 삽입하지 않습니다.
공통 CRUD·설정은 [Component 계약](../README.md)을 따릅니다.

[상위 안내](../README.md)
