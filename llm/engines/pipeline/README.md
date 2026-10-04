# PipelineEngine — 준비 작업과 Engine의 순차 조합

문서를 갱신하거나 실행 환경을 준비한 뒤 기존 Engine을 실행할 때 사용합니다. 모든 단계가 같은 Run 안에 머물며 각 단계의 진행은 Step으로 관찰합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | PreparationStep, PipelineEngine 공개 import입니다. |
| [engine.py](engine.py) | 준비 함수와 Engine 단계의 순차 실행, 설정 연결, 출력·실패 전달을 담당합니다. |

## 사용 예

```python
from llm.engines.loop import LoopEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep

async def prepare(context):
    context.state["prepared"] = True  # 이 Run에만 필요한 임시 자료

engine = PipelineEngine({
    "prepare": PreparationStep("준비", prepare),
    "answer": LoopEngine(),
})
# LargeLanguageModel(..., engines={"prepared_loop": engine})
```

준비 함수에는 실제 작업을 구현하고, 후속 Engine이 필요한 `context.state` 값을 읽도록 연결합니다. 위 예제의 임시 값이 Loop 프롬프트에 자동 삽입되지는 않습니다.

Pipeline은 단계들이 요구한 capability를 합칩니다. 단계별 설정은 `parameters.engines.<등록 이름>.stages.<단계 이름>`으로 구성합니다. 실패·중단·pause가 발생하면 뒤 단계를 실행하지 않습니다. Pipeline은 별도 Run이나 저장 계층을 만들지 않습니다.

[공통 Engine 계약](../README.md)에 따라 이벤트 스트림을 닫고 실패 코드와 출력 소유권을 보존하세요.

[상위 안내](../README.md)
