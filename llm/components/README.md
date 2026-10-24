# Components — Project가 선택하는 기능과 데이터

Component는 Project 안에서 특정 데이터와 기능을 소유합니다. 종류를 백엔드에 **등록**하고, 사용할 종류를 Project에서 **선택**합니다. 예를 들어 `RAGComponent` 등록은 구현을 사용할 수 있게 하는 것이고, `components=["rag"]` 선택은 해당 Project에 RAG 저장 공간과 capability를 연결하는 것입니다.

설정은 [명시적 설정 계약](../CONFIGURATION.md)에 따라 `ProjectConfig.parameters.components.<이름>`에만 저장합니다. 레코드는 Component가 관리하는 데이터이며 설정과 구분합니다.

`required_components=("id", ...)`로 정확한 Component 의존성을 선언할 수 있습니다.
Project 선택에 누락되면 저장 전에 component_dependency_missing 오류를 반환합니다.
Backend는 자동 선택·설치하지 않습니다. capability 의존성과는 별개입니다.
DefinitionComponent는 기본 schema가 닫혀 있으며 열린 plugin record는 open_schema로
소유자를 명시해야 합니다. [전체 계약](../../docs/llm/project-authority.md)을 참고하세요.

## 제공하는 Component

| 폴더 | 저장하는 데이터 | 실행 시 연결 |
| --- | --- | --- |
| [tools/](tools/README.md) | Project별 Python Tool 패키지 | ToolRegistry → ToolExecutor |
| [rag/](rag/README.md) | 문서·청크·벡터·관계와 출처 | 검색 Tool, embedding/rerank/관계 추출 |
| [vision/](vision/README.md) | 이미지 원본·가공본·출처 | 전처리/OCR/VLM Tool, RAG 문서 추출 |
| [memory/](memory/README.md) | 장기 기억·후보·출처·요약 | Memory Tool, completion processor |
| [agents/](agents/README.md) | 목적·Engine·모델·리소스·정책 | Graph AgentNode, 저장 ID 기반 agent_run Tool |
| [workflows/](workflows/README.md) | 노드·간선·입출력 mapping | GraphEngine |
| [skills/](skills/README.md) | 지침·참고 파일·Tool/Data 버전 참조 | skill_list/skill_read/skill_read_resource, Agent 지침 주입 |
| [data/](data/README.md) | 학습 자료·참고 자료·임시 결과 등의 파일 패키지 | 공개 파일 API, Skill의 명시적 자료 조회 |
| [mcp/](mcp/README.md) | 서버 연결 정의·발견 스냅샷 | 주입 connector, 선택적 표준 SDK 어댑터와 Tool alias |
| [prompts/](prompts/README.md) | 시스템 지침·few-shot 메시지 | RAG 등의 소비자가 ID로 참조 |
| [goals/](goals/README.md) | 장기 목적·진행·Run 참조 | CRUD, 읽기/승인 대상 쓰기 Tool, 선택적 문맥 주입 |
| [refinement/](refinement/README.md) | 리소스 개선 제안·승인·적용 이력 | 일반 Run의 근거 분석, 단일 대상 CAS 적용/되돌리기 |

기본 `LargeLanguageModel`은 위 종류를 등록합니다. `projects.acreate()`는 `components`로 선택한 종류만 연결합니다. 초기 선택 목록은 hub 등 호출 애플리케이션이 정합니다. 선택만으로 모델 인증, MCP connector, RAG 필수 설정까지 생성되지는 않습니다.

## 공통 파일

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 공통 Component와 Registry의 공개 import입니다. 개별 종류는 하위 패키지에서 가져옵니다. |
| [base.py](base.py) | ProjectComponent 프로토콜과 선택 구현의 검증·설정·수명·복제 콜백을 조율합니다. |
| [storage.py](storage.py) | 공통 정의/사용량 파일의 경로·목록·무결성·원자적 쓰기/삭제를 소유합니다. 실행 허가와 Component 업무 정책은 해석하지 않습니다. |
| [record_manifest.py](record_manifest.py) | 정의 목록의 정규 필드·버전·소유권/해시 검증·Writer와 열린 JSON 레코드 codec을 소유합니다. |
| [naming.py](naming.py) | Component 디렉터리/기록 식별자의 정규 선언과 단일 검증을 소유합니다. |
| [usage.py](usage.py) | Component 모델 영수증의 현재 필드·예약 불변식·상태·manifest 형식을 선언합니다. [사용량 계약](../../docs/llm/model-usage-contract.md)을 따릅니다. |
| [registry.py](registry.py) | 이름·디렉터리 충돌을 검사하고 선택한 capability를 해석합니다. |
| [definitions.py](definitions.py) | JSON schema를 가진 정의형 Component의 공통 검증·스냅샷 구현입니다. |
| [file_manifest.py](file_manifest.py) | 패키지 정의의 files 필드·존재/조회/갱신·마지막 항목 삭제와 schema 위임을 소유합니다. 실제 파일 효과와 정책은 해석하지 않습니다. |
| [files.py](files.py) | Skill/Data 파일 패키지, manifest 검증, 파일과 정의의 원자적 변경입니다. |
| [processing.py](processing.py) | 모델 입력 변환·응답 관찰을 조합하는 completion processor 계약입니다. |
| [summarization.py](summarization.py) | 공통 SummaryAnswer 응답·검증과 명시적 입력 예산에 맞춘 분할·점진 요약을 소유합니다. 모델 어댑터를 주입받고 저장소나 실행 수명을 소유하지 않습니다. |

## 사용자/API 관점

다음은 `prompts`가 선택된 열린 Project에서 사용하는 예입니다.

```python
prompts = await project.components.aget("prompts")
await prompts.acreate({
    "messages": [{"role": "system", "content": "근거를 함께 설명하세요."}],
}, identifier="guide")
record = await prompts.aload("guide")
record["description"] = "공통 답변 지침"
await prompts.asave("guide", record)
records = await prompts.alist()  # {id: record, ...}
```

`project.components.prompts`도 같은 전용 핸들을 돌려주지만 조회 자체는 동기입니다. 비동기 UI에는 `aget()`를 권장합니다.

공통 JSON CRUD에서 create는 중복 ID를 거부하고, save는 기존 레코드를 전체 교체하며, update는 최상위 키를 갱신합니다. configure도 **설정 전체 교체**입니다. 변경할 부분만 있다면 현재 설정을 읽고 수정한 뒤 저장하세요. 동시 편집에는 snapshot과 expected_version을 사용합니다.

Tool, RAG, Memory는 전문 데이터 구조·수정 계약이 있으므로 해당 README를 따릅니다. 특히 RAG 문서는 `aadd_document()`, Memory 수정은 `expected_generation`과 `expected_revision`, Tool은 source/requirements를 사용합니다.

## 새 Component 개발

구현 전에 [Backend/Application 경계](../../docs/llm/architecture-boundaries.md)를 확인하세요.
제공하는 알고리즘, 불변식, 명시적 입력, 집행할 policy, 앱이 고를 전략을 구분합니다.
숨은 추천값을 넣지 않고 Project 정책을 자식 실행에서 완화하지 않습니다. Host는 기술 구현과 자원을 제공합니다.

[메인 README의 NotesComponent](../README.md#새-component-만들기)가 최소 예제입니다. 데이터 검증에 JSON Schema를 사용하려면 `DefinitionComponent`도 사용할 수 있습니다.

- `name`: 등록과 선택에 사용할 고유 이름.
- `directory`: Project 루트의 직접 하위 디렉터리. 핵심 경로 sessions/logs/state/cache나 다른 Component와 겹치지 않아야 합니다.
- `describe_config()` / `validate_config(data)`: 사용자 설정의 형태와 의미.
- `validate_record(identifier, data)`: 데이터 레코드의 의미 검증.
- `capabilities`: 제공하는 실행 기능의 이름들.
- `resolve(project, capability)`: 요청한 기능의 런타임 값을 반환.
- `resolve_runtime(project, capability, *, data_factory)`: 수명 검사 핸들이 필요한 기능을 연결. 기본 구현은 resolve에 위임.
- `data_class`: 필요할 때 지정하는 ComponentData 하위 클래스.
- `references(project)`: 저장된 Component 참조를 원본 정의에서 계산합니다. 각 항목은
  `component`(대상 Component), `identifier`(대상 ID), `source`(소비자 record ID), 선택 `path`입니다.
  개별 ID 없이 Component 전체를 소비하는 참조는 identifier=None으로 선언합니다. 예를 들어
  Agent의 RAG 선택은 전체 corpus를 가리키며 개별 문서·세대 삭제 대상으로 해석하지 않습니다.
  registry는 소비자 내부 구조를 읽지 않고 이 공개 계약으로 삭제·선택 해제를 보호합니다.
  공통 계층은 로컬 폴더 유무로 이 조회를 생략하지 않습니다. `model_usage(project)`와
  `history_references(project)`도 같은 규칙이며, 자료가 없으면 소유자가 빈 결과를 반환합니다.

직렬화 가능한 JSON만 레코드에 저장하고 client/함수/lock은 capability에 둡니다. 공통 직렬화는 문자열 키와 유한한 값을 확인하며, 선택 Component가 record schema를 소유합니다. 내장 정의는 알려진 필드만 허용하고 Application 확장은 `metadata`에 둡니다. `data_class` 없이 Component에 메서드만 추가해도 Facade로 자동 전달되지는 않습니다.

설정 schema는 `llm.core.schema.implementation_schema(config=..., policy=...)`로 선언합니다.
두 section은 object이며 선택 사항입니다. 기능 입력·SDK 인자는 config, 구현체가 집행하는
한도·재시도·실패 처리는 policy입니다. 레코드 schema와 Tool 함수 인자는 이 외형으로 감싸지
않습니다. 여러 계층에서 필요한 입력 선택 알고리즘은 `llm.policies.CompletionPolicy`처럼
공용 알고리즘을 가져와 자신의 명시 설정으로 구성합니다. 중앙에서 자동 적용하지 않습니다.

앱은 [ComponentData](../services/lifecycle/README.md)를 통해 접근합니다. 핸들은 최신 Project와 선택 여부를 확인하고 workspace 잠금을 사용합니다. Component의 저수준 메서드를 직접 호출하는 확장 코드는 해당 소유권·수명 경계를 책임져야 합니다.

등록 identity 문법은 `ComponentIdentity`, 디렉터리/기록 식별자 문법은 `ComponentName`이
각각 소유합니다. 두 문법의 허용 범위는 다릅니다. 선택적 data_class·directory·required_components
조회와 생략값은 Component 공개 조회 함수가, 선택 hook 이름은 ComponentHook이 선언합니다.
Registry와 설정/수명 소비자는 이 선언을 공유하며 기존 선택 구현의 속성 조회 순서·예외·
의존성 검사와 저장 Owner 호출을 보존합니다.

## 저장과 수명

```text
<project>/
  project.json                     # 선택·initialized_components 이력·parameters.components
  <component>/usage/manifest.json   # 필수 영수증 파일 집합·리비전
  <component>/usage/<id>.json       # 모델 호출 예약·종료 증거
  <json-owner>/records.manifest.json # JSON 기록의 필수 ID 집합·원본 해시
  prompts/records/guide.json       # 공통 JSON 레코드
  agents/records/reviewer.json
  workflows/records/review.json
  tools/packages.manifest.json    # Tool owner의 패키지 목록·파일 해시
  tools/search/search.py           # Tool 전용 Python 패키지
  tools/search/requirements.txt    # 존재가 선언된 경우 필수인 의존성 파일
  skills/review/SKILL.md           # 유일한 표준 정의 원본
  skills/review/references/example.md
  skills/_state/records/review.json # 참조·계보·파일 무결성 증거
  data/manual/data.json            # 자료 설명·파일 manifest
  data/manual/files/reference.md
  rag/...                          # 문서·색인 세대·작업 체크포인트
```

새로 선택한 Component의 저장소만 생성합니다. Project의 관리 필드 `initialized_components`는
현재 선택과 별개로 생성 이력을 보존합니다. 기본 records 구조는 `super().initialize(project)`를
호출하며 반복 초기화는 기존 필수 자료를 검증합니다. 선언된 루트·사용량 manifest가 사라져도
새 저장소로 만들지 않으며 미선언 기존 디렉터리도 임의로 채택하지 않습니다.
초기화에 전달된 Project는 아직 공개되지 않은 생성 초안일 수 있으므로 전달된 설정·경로를
사용합니다. 외부 저장소 구현은 자기 생성/검증 계약을 소유하며 Registry가 내부 형식을 추측하지
않습니다. Component 내부 경로를 ProjectPaths에 추가하지 않습니다.

비활성화와 영구 삭제는 다릅니다. `project.components.aremove(name)`은 자료와 저장소 이력을
남기고, `permanent=True`는 안전성 검사를 통과한 뒤 전용 자료와 이력을 함께 제거합니다.
재선택은 남은 자료를 검증합니다. 백업은 비활성 상태로 보존된 owner의 scope와 validator도
호출하며 미등록 owner를 생략하지 않습니다. 삭제·clone·backup은 Project 서비스의 수명 검사를
통과해야 합니다.

`ComponentStorage`는 선택 구현의 `_record_path`, `_record_files`, `_decode_record`,
`validate_record`와 사용량 조회 콜백을 유지합니다. Component의 기존 layout 속성은 정규
저장/manifest 선언을 참조하여 FileComponent 등 선택 구현의 경로 계약을 보존합니다.
`ModelUsageRecord.require_reservation()`과 `validate_update()`가 신규/기존 영수증의 허용
상태를 검사합니다. 공통 저장은 예산이나 실행 허가를 계산하지 않습니다. 기존 Project
디렉터리·저장 형식·workspace transaction·공개 API는 그대로이며 이중 Reader는 없습니다.

공통 JSON 저장은 `records.manifest.json`으로 전체 기록의 존재와 원본 바이트를 검증합니다.
기록·목록·디렉터리 소실이나 미선언 자료를 빈 상태로 읽거나 다시 만들지 않습니다.
생성·수정·삭제는 기록과 목록을 같은 transaction으로 확정하며 `validate_record`의 의미
검증도 유지합니다. SkillStorage는 표준 디렉터리와 _state를, FileComponent는 Data 패키지 정의 경로를 소유하고 첨부
파일 검증을 추가합니다. Memory 캐시도 원본 검증을 거칩니다. Tool Python 패키지와 외부
저장소는 별도 owner 계약을 따릅니다. 원본 해시 검증의 대용량 비용은 아직 측정하지 않았습니다.

별도 `component.json`이나 과거 형식의 자동 변환은 제공하지 않습니다. JSON 정의는 기본 복제로 복사되지만 색인·특수 파일을 가진 Component는 자신의 clone 계약을 구현해야 합니다.
Project 복제는 선택된 Component에 clone을 위임합니다. 전체 백업/복원은 비활성 상태로 보존된
Component도 포함하며, 복사된 정의의 참조 보호는 복제/복원 후에도 같은 owner가 제공합니다.

## 모델 처리기를 추가할 때

`processing.py`는 Memory 전용 API가 아닙니다. 여러 processor의 순서, 입력 변경, 응답 관찰과 정리를 조합합니다. 새 Skill/MCP/다른 Component가 이 기능을 쓰더라도 Run 정책이나 Step 저장 책임을 가져오지 않습니다. 자세한 확장은 [공통 처리기 계약](../../docs/llm/completion-processing.md)에 있습니다.
