# 현행 Backend API

아래는 `refactor/llm-clean`의 독립 Agent API다. [B/C 검증 상태](agent-refactor-verification.md)와
[클린 설계](clean-refactor.md)를 따른다. 수신자·병렬 실행은 [D 검증 상태](multi-agent-verification.md)를 함께 따른다.
이전 누적 안내는 [과거 기록](backend-api-history.md)에 보존했다.

## 소유권과 수명

`LargeLanguageModel`은 공개 핸들과 서비스를 조립한다. Project → Session → Run → Step의
영속 소유권, Project Component의 별도 자료 소유권을 유지한다. 한 backend를 같은 이벤트 루프에서
사용하고 종료 때 비동기 context manager 또는 `aclose()`로 정리한다. 공유 workspace의 동시 소유는
기존 잠금 계약을 따른다. 동기 API와 `a` 접두 비동기 API는 현재 공개 계약이다.

## 모델 없이 실행 가능한 최소 예제

다음 예제는 임시 workspace와 EchoEngine을 사용한다. 실제 모델·인증·Tool을 호출하지 않는다.

```python
import asyncio
import tempfile

from llm.llm import LargeLanguageModel, RunStatus
from llm.engines import BaseEngine


class EchoEngine(BaseEngine):
    async def run(self, context):
        yield context.messages[-1].content


async def example():
    with tempfile.TemporaryDirectory() as workspace:
        async with LargeLanguageModel(workspace, engines={"echo": EchoEngine()}) as backend:
            project = await backend.projects.acreate("예제", components=[])
            await project.agents.acreate({"engine": "echo"}, identifier="echo")
            session = await project.sessions.acreate("대화")
            await session.asave(agent_ids=['echo'])
            request = await session.run.submit("hello", recipients=["echo"])
            run, = await request.wait()
            result = await run.aresult()
            assert result.status == RunStatus.COMPLETED
            assert (await run.aresponse()).content == "hello"


if __name__ == "__main__":
    asyncio.run(example())
```

`submit`은 RequestHandle, `wait`는 수신자별 RunHandle 목록을 반환한다. 각 Run의 `aresult`는 조회용 ExecutionResult다.
Session 참여자는 생성 또는 비활성 상태의 `asave(agent_ids=[...])`로 명시 저장한다. `recipients`는 필수이며
명시한 `"all"`만 참여자 목록을 접수 때 동결한다. 같은 Agent는 FIFO, 다른 Agent는 병렬이다.
`request.aget_runs()`와 `aresults()`는 dispatch 전 빈 목록이며 단일 수신자도 같은 API를 쓴다.
완료 상태와 출력·진단을 함께 확인한다. 조회 view는 별도 실행 원본을 저장하지 않는다.
일반적인 `wait` 취소가 이미 접수된 요청의 취소를 뜻하지는 않는다.

## 설정과 모델 실행

모델 실행은 [LLM 사용법](../../llm/README.md#요청-한-건-실행하고-결과-받기)의 Loop 예제를 따른다.
모델과 인증은 호출자가 지정한다. ProjectConfig에는 정책/인자/앱 데이터를 구분한다.

```python
from llm.llm import ProjectConfig

config = ProjectConfig(
    policies={"tools": {"max_calls": 1}},
    parameters={"engines": {"loop": {
        "config": {"completion": {"model": "openai/YOUR_MODEL"}},
        "policy": {"max_tool_calls": 1, "provider": {"max_attempts": 2}},
    }}},
    data={"label": "Application-owned example"},
)
```

위 수치는 호출자가 선택한 예제이지 backend 기본값이 아니다. SDK 인자는 Provider가 검증한다.
엔진은 Project→Session→Agent, Component는 명시 client→Project 순서로 해석하며 제한 확대는 거부한다.
`project.adescribe_config()`는 원본 값·Schema·출처·CAS를 설명한다. `ComponentData.aconfigure()`는
해당 설정 전체를 교체하므로 기존 설정을 읽어 명시적으로 수정한다.

[설정 계약](../../llm/CONFIGURATION.md), [설정 편집](project-settings.md),
[Schema 소유권](schema-ownership.md), [정책](project-policies.md)을 기준으로 검증한다.

## Tool·승인·Resume

Tool은 공통 ToolExecutor를 사용한다. handler/worker 실행 전에 권한·제약·한도·승인·효과 원장을
검사한다. 승인 화면은 durable InteractionRequest를 읽고 선택한 응답을 저장한다. 응답 저장은 실행이 아니다.

```python
# paused_run/session은 현재 Project의 핸들이다. selected_option_id는 실제 선택지의 ID다.
requests = await paused_run.ainteractions(pending_only=True)
interaction = requests[0]
response = interaction.respond(selected_option_id)
await paused_run.arespond(response)
plan = await session.run.resume_plan(paused_run.id, agent_id=paused_run.agent_id)
# UI에서 blockers와 효과를 검토한 뒤, can_resume일 때만 명시 실행한다.
if plan.can_resume:
    continuation = await session.run.resume(
        paused_run.id, agent_id=paused_run.agent_id, expected_revision=plan.revision,
    )
    resumed_run, = await continuation.wait()
```

선택지 ID를 항상 approve라고 가정하지 않는다. 불확실한 효과의 `retry_nodes`는 별도 명시 검토가
필요하다. `can_resume=False`를 새 submit으로 우회하지 않는다. Resume는 새 Run이며 완료 Tool은
재사용한다. `restart(..., expected_revision=...)`는 dispatch 이전 중단의 영속 증거가 있을 때만 허용한다.

[Interaction](interactions.md), [checkpoint plan/삭제](checkpoints.md),
[오류·복구 API](backend-recovery.md), [조건부 retry](retry.md)를 함께 따른다.

## 기능별 공개 계약

| 기능 | 문서 |
|---|---|
| Engine 개발·등록 | [Engine README](../../llm/engines/README.md), [Graph](graph-engine.md), [중첩](nested-workflows.md) |
| Component 정의·선택·CRUD | [정의 API](component-definitions.md), [Component README](../../llm/components/README.md) |
| 현재 Agent 정의·위임 | [Agents](agents.md), [Project 권한](project-authority.md) |
| Prompt/Skill/Data/Refinement | 해당 [Component 안내](../../llm/components/README.md)와 [Revision](revisions.md) |
| Memory/RAG/Vision | [Memory](memory.md), [RAG](rag-components.md), [Vision](../../llm/components/vision/README.md) |
| 모델·Provider 확장 | [Provider README](../../llm/providers/README.md), [공통 경계](shared-contracts.md) |
| 출력·구독·추가 지시 | [출력](engine-output.md), [서비스 확장](service-extensions.md), [Steering](steering.md) |
| 보관·복구·백업 | [운영 API](long-running.md), [저장](operational-storage.md), [트랜잭션](storage-transactions.md) |
| 실제 ish 연결 | [설정 예제](ishrc-loop-test.md), [LLM 설치](../../llm/README.md#ish에서-실행하기) |

Project/Session 자동 생성·선택은 backend 정책이 아니다. Hub의 생성 preset은 앱 계약이다.
ResourceRef·plan·Diagnostic을 역직렬화한 것만으로 권한을 얻지 않는다. 각 소유 API가 실제 실행·저장
직전에 검증한다. 구 API나 구 저장 자료를 자동 변환하는 새 경로는 추가하지 않는다.

## 검증 명령과 한계

Linux Python 3.12.14에서 저장소 루트 기준:

```bash
python -m tests.llm.run_regression --suite llm --output /tmp/ish-llm-regression.json
python -m tests.llm.run_regression --suite hub --output /tmp/ish-hub-regression.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6
python -m tests.llm.check_docs
```

실제 host는 `ish.platform/src/ish`에 준비하고 사용 SHA를 기록한다. `ish.platform/`은 외부 참조이며
이 저장소에 커밋하지 않는다. Bubblewrap 격리 검사와 Xvfb/xclip 검사는 실제 환경에서 실행해야 한다.
명령을 적어 두었다는 사실은 실행 성공 증거가 아니다. [검증 상태](documentation-review.md)를 참고한다.


Session 간 durable 전송은 [Session 전송 계약](session-transfers.md)의 `session.exchange`를 사용한다.
명시 수신자·idempotency key·target 자체 승인/예산과 기존 RunManager 접수 경계를 유지한다.
