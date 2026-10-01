> 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# Engine 구성

Engine은 Run에 등록·선택되는 실행 전략입니다. 영속 소유 관계는
Project → Session → Run → Step이며 Engine은 이벤트를 발생시킵니다.
도메인 저장과 실행 수명 관리는 기존 서비스가 담당합니다.

```text
engines/
├── base.py                 # Engine 계약, 문맥·이벤트, BaseEngine
├── registry.py             # Engine 등록·조회·설정 사전 검증
├── loop/
│   ├── __init__.py         # LoopEngine 공개 진입점
│   └── engine.py           # LiteLLM 스트리밍·Tool 반복 실행
├── graph/
│   ├── __init__.py         # GraphEngine, GraphNodeContext 공개 진입점
│   ├── engine.py           # LangGraph 컴파일·실행·노드 수명 관리
│   ├── agent.py            # AgentNode: 등록 Engine을 Graph 노드에 연결
│   ├── tool.py             # ToolNode: 공통 ToolExecutor에 연결
│   └── checkpoints.py      # 자식 Engine 체크포인트를 부모 Graph에 연결
└── pipeline/
    ├── __init__.py         # PipelineEngine, PreparationStep 공개 진입점
    └── engine.py           # 준비 작업과 여러 Engine의 순차 조합
```

## 공개 import

```python
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine, GraphNodeContext
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.pipeline import PipelineEngine, PreparationStep
```

각 패키지의 `__init__.py`는 공개 클래스를 노출합니다. 실제 구현을 패치하거나 내부
실행부를 조사할 때는 `llm.engines.loop.engine` 같은 구현 모듈을 사용합니다.
이전 `engines.agent`, `engines.tool`, `engines.checkpoints`와
`engines.base.EngineRegistry`의 호환 경로는 제공하지 않습니다.

신규 실행 전략도 `engines/<name>/engine.py`와 공개 `__init__.py`로 추가할 수 있습니다.
플러그인 외부에서 구현한 객체를 EngineRegistry 또는 LargeLanguageModel의 engines에
등록하는 방식도 유지됩니다. 폴더 구조가 등록 이름이나 ProjectConfig 설정 키를 결정하지는
않으며 Engine을 파일에서 자동 발견·실행하는 기능은 추가하지 않습니다.

Graph 구현과 Run별 내부 실행 객체는 같은 `graph/engine.py`에 유지합니다.
Agent/Tool 노드는 Graph에 특화된 어댑터이고, 여러 Engine이 공유하는 Tool 실행 정책은
`services/runtime/tools.py`가 계속 담당합니다. LangGraph는 실제 Graph 실행 시 로드합니다.

이번 폴더 정리는 도메인 저장 버전이나 체크포인트 형식을 바꾸지 않습니다.
