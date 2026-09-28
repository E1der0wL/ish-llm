# llm — ish plugin

Linux의 ish 호스트에서 사용하는 AI 실행 플러그인입니다. Project → Task → Run → Engine → Step 구조로 대화, 스트리밍, Tool, Workflow, RAG 및 Memory를 관리합니다.

## 설치

이 저장소의 **`llm/` 폴더**를 ish의 플러그인 경로 아래 배치합니다.
폴더명은 Python 패키지 이름과 플러그인 이름이므로 `llm`을 사용해야 합니다.

```bash
# <plugin-script-directory>를 실제 ish 플러그인 경로로 바꾸세요.
git clone https://github.com/E1der0wL/ish-llm.git ish-llm-source
cp -R ish-llm-source/llm <plugin-script-directory>/llm
```

설치 결과는 다음 형태입니다.

```text
<plugin-script-directory>/
└─ llm/
   ├─ llm.py
   ├─ __init__.py
   ├─ components/
   ├─ core/
   ├─ engines/
   ├─ providers/
   └─ services/
```

이 플러그인 자체를 `pip install`할 필요는 없습니다. 필요한 Python 라이브러리는
`llm.py`의 `PLUGIN_META.dependencies`에 선언되어 있으며 ish의 플러그인 로더가 처리합니다.
운영체제는 Linux이며 현재 검증 버전은 Python 3.12.14입니다.

## ish에서 사용

`.ishrc.py`에서 로드된 플러그인의 공개 클래스를 가져옵니다.

```python
llm_plugin = plugin.get("llm")
if llm_plugin is not None:
    LargeLanguageModel = llm_plugin.LargeLanguageModel
    LoopEngine = llm_plugin.LoopEngine
```

호스트의 비동기 실행 경로에서는 다음처럼 사용할 수 있습니다.

```python
async def greet(workspace, model):
    async with LargeLanguageModel(
        workspace,
        engines={"loop": LoopEngine()},
    ) as backend:
        project = await backend.projects.acreate(
            "Greeting", config={"completion": {"model": model}}
        )
        task = await project.tasks.acreate("Greeting request")
        request = await task.run.submit("한국어로 짧게 인사해줘.", engine="loop")
        run = await request.wait()
        result = await run.aresult()
        if result.status != "completed":
            raise RuntimeError(f"Run failed: {result.status}")
        return (await run.aresponse()).content
```

모델 인증은 사용하는 공급자의 환경변수 또는 completion 설정으로 전달합니다.
상시 UI에서는 백엔드를 유지하여 사용하고, 호스트 종료 시 `await backend.shutdown()`으로 정리합니다.
전체 공개 API 예제는 `llm.py`의 `LargeLanguageModel` 독스트링에 있습니다.

## 검증 범위

개발 저장소에서 Linux Python 3.12.14 전체 774개 테스트와 합성 Engine 5 Task/60초/655 Run,
정상 종료 후 새 백엔드에서의 저장 결과 조회를 검증했습니다. 이 `llm/` 폴더에는 개발용
테스트, 실행 결과 로그, workspace 및 pip 패키징 파일을 포함하지 않습니다.
저장소 루트의 기존 `ish/` 및 개발 자료는 이전 이력 보존을 위해 유지되며 이 플러그인의 실행에 필요하지 않습니다.
실제 공급자 기반 3시간 작업과 2GB RAG 규모를 보장하는 결과는 아닙니다.
