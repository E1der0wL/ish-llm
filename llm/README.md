> 설정은 [명시적 설정 계약](CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# llm — ish 플러그인

Linux의 ish에서 사용하는 AI 실행 백엔드입니다. 영속 도메인의 소유 관계는
**Project → Session → Run → Step**입니다. Session은 프로세스 종료 후에도 다시 열 수 있는
작업·대화 공간이며 Conversation은 Session에 속합니다.

Engine은 Run의 실행 전략이고 Component는 프로젝트에 연결되는 기능과 자료의 소유자입니다.
Loop·Graph 등 Engine을 바꾸어도 Run/Step 기록과 Session의 대화 수명은 유지됩니다.
Engine별 소스 구조와 공개 import는 [Engine 안내](engines/README.md)를 참고하세요.
Project의 Python Tool 생성·준비·활성화는 [Tool 패키지 안내](components/tools/README.md)에 정리되어 있습니다.
Project Tool의 introspection과 실제 호출은 각각 child interpreter에서 실행됩니다.
승인·재시도·Step·영수증은 기존 ToolExecutor가 소유합니다. worker는 OS sandbox가 아닙니다.
UI용 `backend.observability.snapshot()`은 [운영 관찰 계약](services/infrastructure/OBSERVABILITY.md)을 참고하세요.

사내 OpenAI-compatible 서버의 재시도·캐시·응답 검증은 [공급자 안정화](providers/README.md),
배치 재개·벡터 재사용·JSON mode·불완전 그래프 정책은 [RAG 설정](components/rag/README.md)을 참고하세요.

## 설치와 진입점

이 저장소의 `llm/` 폴더를 ish의 플러그인 스크립트 디렉토리에 배치합니다.
플러그인 자체의 pip 설치는 필요하지 않습니다. 외부 라이브러리 의존성은
`llm.py`의 `PLUGIN_META.dependencies`에 선언되어 있습니다.
개발·실행 검증 대상은 **Linux Python 3.12.14 하나**입니다. 구버전 호환 계층 없이
표준 `dataclass`, `StrEnum`, `asyncio.timeout`, `contextlib.aclosing`을 사용합니다.
개발 저장소의 `.python-version`과 `pyproject.toml`도 이 버전으로 고정합니다.

```python
llm_plugin = plugin.get("llm")
LargeLanguageModel = llm_plugin.LargeLanguageModel
LoopEngine = llm_plugin.LoopEngine
ProjectConfig = llm_plugin.ProjectConfig
```

호스트의 비동기 실행 경로에서 다음처럼 호출합니다. 모델과 인증 설정은 호출자가 전달합니다.

```python
async def request_once(workspace, completion):
    async with LargeLanguageModel(
        workspace, engines={"loop": LoopEngine()},
    ) as backend:
        project = await backend.projects.acreate(
            "Workspace", config=ProjectConfig(completion=completion),
        )
        session = await project.sessions.acreate("Conversation")
        request = await session.run.submit("안녕하세요.", engine="loop")
        run = await request.wait()
        result = await run.aresult()
        if result.status != "completed":
            raise RuntimeError(result.error or str(result.status))
        return (await run.aresponse()).content
```

상시 UI는 백엔드를 유지하고 `backend.projects.aload(project_id)`와
`project.sessions.aload(session_id)`로 선택한 세션을 다시 엽니다. 한 Session의 Run은
순서대로 실행하며 다른 Session들은 동시에 실행할 수 있습니다. 호스트 종료 시
`await backend.shutdown()`으로 정리합니다. API 사용 예시는 LargeLanguageModel 독스트링에 있습니다.

## Session으로 이름 변경

- `Task` / `TaskManager` / `TaskRepository` / `TaskPaths` → `Session` / `SessionManager` / `SessionRepository` / `SessionPaths`
- `project.tasks` → `project.sessions`
- `EngineContext.task`, `Run.task_id` → `EngineContext.session`, `Run.session_id`
- `task_defaults`, `task_config` → `session_defaults`, `session_config`
- Memory의 세션 범위는 `scope="session"`, `session_id=...`로 지정합니다.
- ish Loop 예제의 기존 세션 선택 인자는 `--session-id`입니다.

Project/Session/Run/Step 기록은 `storage_version=1`를 사용합니다. 세션 저장 경로는
`projects/<project_id>/sessions/<session_id>/session.json`이며 그 아래에 대화 파일과
`runs/`가 있습니다. Engine 객체는 영속 소유 계층에 들어가지 않습니다.

이전 Task 형식의 데이터는 자동 변환하지 않고 명시적으로 거부합니다. 기존 테스트 데이터는
그대로 보관하고 **새 workspace 경로**를 지정하세요. 구형 API의 호환 별칭은 제공하지 않습니다.
선택적인 메모리 대화 저장은 종료 시 대화가 사라지는 기존 특성을 유지합니다.

## 검증 예제

RAG 추출은 few-shot·고정 temperature=0·제한된 JSON 수정 호출을 지원합니다.
[PromptComponent](components/prompts/README.md)에서 지침을 편집할 수 있습니다.
관계의 출처·시각·가중치 속성 추가로 기존 RAG 색인은 새 Project에 다시 등록해야 합니다
(`graph_schema_version=1`; 핵심 도메인 `storage_version`은 1).

- [GraphEngine·RAG 통합 검사](../examples/llm/graph_rag.md)
- [사용자 요청·검증·승인·적용 Workflow](../examples/llm/configuration_workflow.md)
- [메인 도메인 성능 검사](../examples/llm/domain_performance.md)
- [장애 복구·다중 프로세스 검사](../examples/llm/recovery_probe.md)

예제 안내에서 독립 Python 실행과 ish 명령 등록 방법을 확인할 수 있습니다.
