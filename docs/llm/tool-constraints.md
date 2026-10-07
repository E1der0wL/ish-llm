# Project-owned Tool 인자 제약

`ProjectConfig.policies.tools.argument_constraints`는 Tool 이름 → 인자 이름 → 제약의 JSON dict다.
Project가 저장하며, 모델이나 Component metadata가 정책을 변경하지 않는다.
아래 숫자는 예제의 명시값이며 backend default가 아니다.

```python
from llm.core.models import ProjectConfig

config = ProjectConfig(policies={"tools": {"argument_constraints": {
    "rag_search": {
        "method": {"mode": "selectable", "values": ["hybrid", "vector"]},
        "limit": {"mode": "bounded", "minimum": 1, "maximum": 20},
        "max_hops": {"mode": "fixed", "value": 2},
    },
    "memory_search": {"status": {"mode": "fixed", "value": "confirmed"}},
    "file_read": {"max_lines": {"mode": "bounded", "maximum": 200}},
}}})
# await backend.projects.acreate(config=config)
```

| mode | 계약 |
|---|---|
| fixed | 생략 시 Project가 명시한 value를 실제 인자로 채움. 다른 값은 실행 전에 거부 |
| bounded | 명시 입력 필수. minimum/maximum 안의 숫자만 허용. 하나의 경계만 지정 가능 |
| selectable | 명시 입력 필수. 비어 있지 않은 values 중 선택. 원본 schema도 만족해야 함 |

원본 JSON Schema ∩ Project 제약이 effective contract다. allOf/const/enum을 사용하므로
원본 타입, enum, exclusive bounds, $defs/$ref 등의 구조가 보존된다. 원본 Tool은 mutate하지
않는다. fixed/selectable 값이 원본 property schema에 맞지 않으면 사전 검증에서 거부한다.
bounded는 원본 범위를 확대하지 못하며 실제 허용 범위는 교집합이다. 선언된 property만 대상으로
한다. 조건부 schema에서 만족 불가능한 조합도 실행 전 전체 schema 검증에서 거부한다.

Loop는 effective schema를 모델에 전달한다. Registry.prepare와 ToolExecutor는 실제 인자를
다시 검증하고 **검증/고정값 반영 → 승인 → receipt → 실행** 순서를 지킨다.
ToolExecutor를 직접 사용하는 custom Engine도 같은 검사를 받는다. 별도 constraint override를
Tool 인자로 받지 않는다. RAG/Memory/builtin은 별도 정책 엔진 없이 이 경계를 공유한다.

자식 `ToolExecutionScope.child(argument_constraints=...)`는 부모 것을 상속하고 좁힐 수만 있다.
bounded의 빠진 경계는 상속한다. 부모 fixed/selectable을 연속 bounded로 바꾸는 등 포함 관계를
증명하기 어려운 교차 mode는 거부한다. 자식은 finite fixed/selectable subset으로 좁힐 수 있다.
허용 Tool 이름과 승인/예산/원장은 기존 부모 경계를 계속 적용한다.

constraints를 지정하지 않으면 추가 상한을 만들지 않는다. Component config의 검색 method는
호출 인자의 기본 baseline일 수 있지만 변경 권한 제한은 아니다. `fixed`를 명시해야 모델이
그 값을 바꿀 수 없다. bounded/selectable은 missing 인자의 default를 생성하지 않는다.
대신 effective schema의 required에 포함한다. 생략 후 Python/Component 내부 기본값이
적용되어 제약을 우회하는 것을 막으며, 원본 schema 자체의 required는 변경하지 않는다.

ToolExecutionScope.binding과 durable Interaction action에 effective 제약을 저장한다. Loop/Graph의
checkpoint binding에도 포함되므로 제약을 바꾸고 옛 승인을 조용히 재사용하지 못한다.
변경한 정책으로 실행하려면 새로운 요청을 제출한다. 기존 storage version은 그대로 1이며
옛 checkpoint를 자동 변환하지 않는다.

Provider별 strict JSON Schema 지원 범위는 SDK/provider 계약에 따른다. 모델이 schema를 무시해도
runtime 검증은 유지된다. Host가 직접 handler를 호출하면 ToolExecutor 경계를 사용하지 않는
것이므로 이 정책이 적용되지 않는다.
