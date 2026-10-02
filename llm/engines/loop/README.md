# LoopEngine — 모델과 Tool을 반복 실행

LiteLLM completion 스트림을 읽고, 모델이 요청한 Tool을 실행한 뒤 결과를 다음 모델 호출에 전달합니다. 모델 응답, Tool 실행, 반복 체크포인트를 같은 Run 안에서 관찰할 수 있습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | LoopEngine 공개 import를 제공합니다. |
| [engine.py](engine.py) | 설정 해석, 스트리밍, Tool 호출, 반복 제한, 체크포인트·명시적 재개를 구현합니다. |

## 등록과 실행

```python
from llm.engines.loop import LoopEngine

engines = {"assistant": LoopEngine()}
# LargeLanguageModel(..., engines=engines)에 전달한 뒤:
# request = await session.run.submit("문서를 설명해줘", engine="assistant")
```

등록 이름에 맞춰 `ProjectConfig.engines["assistant"]`에 설정합니다. `settings_name`을 명시하면 별도 설정 키를 사용할 수 있습니다. 요청마다 Engine 이름을 지정합니다.

## 한 반복의 흐름

1. Session 문맥과 선택한 completion processor로 입력을 구성합니다.
2. LiteLLM을 `stream=True`로 호출하고 델타·Completion 정보를 전달합니다.
3. Tool 요청이 있으면 공통 ToolExecutor를 통해 실행합니다.
4. 완료 응답·Tool 영수증을 체크포인트 이벤트로 전달하고 다음 반복으로 갑니다.
5. 최종 응답 또는 명시적 반복 제한·중단·실패로 실행을 끝냅니다.

`request_timeout`은 Loop 호출 제한이고 `completion.timeout`은 SDK 인자입니다. 서로 자동 복사하지 않습니다. 미설정 request/tool timeout이나 max_iterations를 임의로 만들지 않습니다.

## 재개와 확장

완료된 Tool 결과는 재사용합니다. 실행 효과가 불확실한 Tool은 `retry_nodes`가 필요하며 자동 재실행하지 않습니다. 승인 대기는 저장된 Interaction과 checkpoint key로 연결합니다.

Loop가 요청하는 capability는 `tools`, `completion_processors`입니다. RAG 검색이나 Memory 처리를 추가할 때 Loop 내부에 전용 저장 코드를 넣지 않고 Component의 capability를 연결하세요. 출력·취소·오류 경계는 [상위 Engine 계약](../README.md)을 따릅니다.

[상위 안내](../README.md)
