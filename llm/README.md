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

개발 저장소에서 Linux Python 3.12.14 전체 **835개 테스트**를 통과했습니다.
Run 수명과 다중 파일 저장 트랜잭션, 강제 종료 후 복구, 저장 오류, 스트리밍 연결 단절,
workspace의 경쟁 프로세스 접근 차단을 검증했습니다. 이 `llm/` 폴더에는 실행 가능한
검증 예제를 포함하며 개발용 unittest 모음, 실행 결과 로그, workspace 및 pip 패키징
파일은 포함하지 않습니다.
저장소 루트의 기존 `ish/` 및 개발 자료는 이전 이력 보존을 위해 유지되며 이 플러그인의 실행에 필요하지 않습니다.
실제 공급자 기반 3시간 작업과 2GB RAG 규모를 보장하는 결과는 아닙니다.

## 사내 환경 검증 예제

- [GraphEngine·RAG 통합 검사](examples/graph_rag.md)
- [사용자 요청에 따른 설정 검증·승인·적용 Workflow](examples/configuration_workflow.md)
- [메인 도메인 성능 검사](examples/domain_performance.md)
- [장애 복구·다중 프로세스 검사](examples/recovery_probe.md)

의존성을 사용할 수 있고 `llm`의 부모 디렉토리가 import 경로에 있는 환경에서 다음처럼
실행할 수 있습니다. ish 명령 등록 방법과 모델 설정은 각 안내를 참고하세요.

```bash
python3.12 -m llm.examples.recovery_probe --output-dir ~/llm-probes
```

장애 검사는 API 키 없이 로컬 서버/새 테스트 workspace를 사용합니다. ENOSPC는
저장 경계의 단발 오류 주입이며 실제 디스크 고갈이나 사내 모델 서버 장애를 모두
재현하는 검사는 아닙니다. Graph·RAG 예제의 모델 주소/API 키는 사용자가 설정합니다.
