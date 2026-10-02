# 기본 Tool 사용법

`BuiltinTools`는 작업 디렉토리에 연결한 실행 카탈로그다. `ToolComponent(toolkit.registry)`로
등록하고 Project에서 필요한 이름을 활성화한다. 생성만으로 파일 변경/명령 실행을 하지
않고, LargeLanguageModel이 임의 작업 경로를 선택하여 자동 연결하지도 않는다.

```python
import sys
from pathlib import Path
from llm.llm import LargeLanguageModel
from llm.components.tools import ToolComponent
from llm.components.tools.builtin import BuiltinTools
from llm.engines.loop import LoopEngine

work = Path("my-project").resolve()  # 실제 작업할 코드/문서 디렉토리
work.mkdir(exist_ok=True)

async def main():
    async with BuiltinTools(
        work,
        allow_commands=False,
        checks={"tests": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]},
        git=True,
    ) as toolkit:
        async with LargeLanguageModel(
            "llm-workspace",
            components=[ToolComponent(toolkit.registry)],
            engines={"loop": LoopEngine()},
        ) as backend:
            project = await backend.projects.acreate("Coding", components=["tools"])
            tools = await project.components.aget("tools")
            await tools.aenable("file_list", "file_search", "file_read", "file_create",
                                "file_patch", "test_run", "git_status", "git_diff")
            # ProjectConfig나 LoopEngine에 모델 설정 후 session.run.submit(..., engine="loop") 사용.
```

위 tests 명령은 작업 디렉토리에 tests 폴더가 있는 프로젝트의 예시다. 각 프로젝트의
검증 명령으로 설정해야 한다. Toolkit은 backend보다 바깥 async with에서 관리하여
Run 종료 후 남은 프로세스를 정리한다. 같은 실행 루트/프로세스 카탈로그를 공유하지
않아야 하는 Project에는 별도 BuiltinTools 인스턴스를 제공한다.

ish에서는 `plugin.get("llm").BuiltinTools`, `ToolNode`로도 접근할 수 있다.

## 제공 목록

| 등록 조건 | Tool |
| --- | --- |
| 항상 제공 | file_list, file_search, file_read, file_create, file_patch, file_move, file_delete, file_restore |
| allow_commands=True | shell_execute, process_start |
| 명령/검증/Git 기능 중 하나 사용 | process_status, process_output, process_cancel |
| checks에 명령 등록 | check_run, test_run |
| git=True | git_status, git_diff |
| bind_prompts(AgentData) 호출 | prompt_read, prompt_update |
| 해당 어댑터 제공 | web_search, web_fetch, browser_open, browser_snapshot, browser_act, kernel_execute, kernel_reset, external_rag_search, external_graphrag_search, context_expand, harness_propose_update |

Tool 카탈로그 등록과 Project 활성화는 별개다. `toolkit.registry.names()`와
`toolkit.registry.definitions()`로 실제 등록된 이름과 전체 인자 Schema를 조회한다.

## 파일 작업

파일 Tool은 UTF-8 텍스트 파일을 대상으로 한다. 경로는 지정한 작업 루트의 상대 경로다.
상위 경로, 절대 경로, 링크/정션, Windows 장치 이름과 내부 `.llm-*` 경로를 거부한다.
`max_file_bytes` 기본값은 1,000,000바이트이며 큰 파일은 거부한다. 검색은 큰 파일과
텍스트로 해석할 수 없는 파일을 건너뛰고 skipped 수를 반환한다.

```python
# 아래는 모델이 호출할 Tool 인자의 예시다.
file_read = {"path": "src/main.py", "start_line": 1, "max_lines": 300}
file_search = {"query": "def run", "path": "src", "recursive": True, "limit": 100}
file_create = {"path": "notes/new.md", "content": "# 새 문서\n"}
file_patch = {
    "path": "src/main.py",
    "expected_sha256": "file_read가 반환한 SHA-256",
    "old_text": "return old_value",
    "new_text": "return new_value",
}
```

read는 부분 내용과 함께 **전체 파일**의 sha256을 반환한다. patch는 해당 버전과
old_text가 정확히 한 번 일치해야 진행한다. move/delete에도 expected_sha256이 필요하다.
create/move/restore는 기존 대상을 덮어쓰지 않는다. 수정은 임시 파일을 작성한 뒤 원자적으로
교체하며 인스턴스 내부 작업은 잠금으로 직렬화한다. 외부 편집기나 별도 인스턴스와의
검사/교체 전체를 OS 수준의 compare-and-swap으로 보장하는 것은 아니다.

delete는 파일을 `<work>/.llm-trash/<trash_id>/content`로 이동하고 trash_id를 반환한다.
`file_restore({trash_id, path})`로 복원할 수 있다. 재귀 디렉토리 삭제는 제공하지 않는다.
휴지통 원본 경로는 삭제 Tool의 Step 이력에 남는다. 일반 목록/검색에서는 내부 보관
파일을 제외한다. Toolkit이 사용 중인 프로젝트 데이터 저장 디렉토리를 편집 대상으로
노출하지 않으려면 코드 작업 디렉토리와 llm workspace를 분리해서 지정한다.

## 명령 실행과 프로세스

`allow_commands=True`는 임의 호스트 명령 실행을 허용한다. 작업 루트는 cwd로 사용하지만
셸/커널의 파일 접근을 OS 차원에서 제한하는 샌드박스는 아니다. 컨테이너나 ish의 격리
실행기가 필요하다면 별도 실행 어댑터를 사용해야 한다.

* shell_execute: `{command, cwd?, timeout_seconds?}`. 기본 셸은 Windows PowerShell,
  POSIX `/bin/sh -c`이며 생성자의 shell 인자로 argv 접두사를 바꿀 수 있다.
* process_start: `{argv: [실행파일, 인자, ...], cwd?, timeout_seconds?}`. 셸 해석 없이 실행한다.
* process_status/process_output/process_cancel: `{process_id}`. 이 인스턴스가 생성한 ID만 처리한다.
* check_run/test_run: `{name, timeout_seconds?}`. 개발자가 checks에 등록한 argv만 실행한다.
* git_diff: `{staged?: bool}`. 외부 diff/textconv를 끄고 Git diff를 읽는다. Git 설치가 필요하다.

프로세스 결과는 status, returncode, stdout, stderr, truncated를 포함한다. status=completed는
프로세스 종료를 의미하므로 **검증 성공 여부는 returncode==0인지 확인**해야 한다.
출력을 계속 읽으면서 최대 max_output_bytes만 보관한다(기본 100,000바이트).
timeout_seconds는 생성자의 max_seconds(기본 60초)를 넘길 수 없다.

전경 명령 호출을 취소하면 종료 정리를 기다린다. process_start로 시작한 백그라운드 작업은
호출한 Run 종료 뒤에도 지정 시간까지 유지되며 process_cancel 또는 Toolkit.close로 종료한다.
따라서 Run 중단과 백그라운드 프로세스 수명은 서로 다르다. 최대 32개 세션을 보관하고 새
세션 등록 시 가장 오래된 완료 세션부터 제거한다. 출력 조회는 보관된 출력 전체를 반환한다.

POSIX에서는 생성한 프로세스 그룹을 종료하고 Windows에서는 살아 있는 부모에 sessionkill /T를
사용한다. 부모가 먼저 종료하거나 자식이 분리된 경우까지 완전한 격리를 보장하지 않는다.
웹/커널 등 주입 어댑터는 자체 연결/세션 자원의 종료와 취소를 담당한다.

## 웹·브라우저·커널·검색 어댑터

프로젝트에서 통합 RAG 컴포넌트를 선택하면 별도 활성화 API나 BuiltinTools 어댑터 없이
현재 프로젝트에 묶인 검색 Tool을 제공한다. [검색 Tool 사용법](rag-components.md)을 참고한다.
아래 corpus/query/options 계약은 외부 검색 서비스를 연결하는 기존 호스트 어댑터 전용이며,
내장 컴포넌트의 검색 Tool과 다르다. 외부 어댑터는 external_ 접두사를 사용하므로
내장 검색 Tool과 동시에 사용할 수 있다.

어댑터는 `async callable(arguments: dict) -> JSON 값`이다. 아래처럼 제공하면 해당 Tool이
표준 Schema와 함께 등록된다. 어댑터가 없으면 해당 Tool 자체가 모델에 노출되지 않는다.

```python
async def search_documents(arguments):
    return await my_retriever.search(
        corpus=arguments["corpus"], query=arguments["query"],
        **arguments.get("options", {}),
    )

toolkit = BuiltinTools(work, adapters={"external_rag_search": search_documents})
```

`my_retriever`는 호스트에서 준비한 실제 검색 서비스다. 기본 패키지는 검색 공급자 계정,
브라우저 드라이버, Python/Jupyter 또는 ish 커널, RAG 인덱스를 자동으로 만들지 않는다.
외부 서비스별 구현과 인증은 호스트가 담당한다. 아래 블리치 예제는 실제 HTTP 조회를
검증하며 브라우저/커널/RAG 서버를 테스트하지는 않는다.

주요 인자는 web_search의 query, web_fetch/browser_open의 url,
browser_snapshot/browser_act의 session_id(조작은 action 객체 추가),
kernel_execute의 session_id/code, kernel_reset의 session_id,
external_rag_search/external_graphrag_search의 corpus/query/options,
context_expand의 reference/scope(chunk/section/topic/document)다.

| 구분 | 이름 | 검색 대상/구현 |
| --- | --- | --- |
| 내장 통합 검색 | rag_search | 현재 Project의 문서·관계·출처를 함께 반환 |
| 외부 검색 어댑터 | external_rag_search, external_graphrag_search | 호스트 함수가 corpus/query/options를 해석하여 외부 서비스 호출 |

외부 어댑터는 내장 문서를 자동 등록하거나 로컬 컴포넌트로 우회하지 않는다.
`context_expand`도 외부 reference를 해석하는 호스트 연결이다. 내장 검색은 자체 expand 인자를 사용한다.

이전 BuiltinTools 어댑터 키 rag_search/graphrag_search는 제거했다. 사용 중이었다면
adapters 키, 프로젝트의 활성화 이름, 저장된 Tool 정의의 ID/function.name 및 Workflow의
Tool 참조를 external_ 이름으로 변경한다. 기존 키로 구성하면 변경 방법을 담은 오류를 반환한다.
사용자 프로젝트 JSON은 자동 수정하지 않는다. 내장 컴포넌트 사용자는 변경할 필요가 없다.

harness_propose_update는 `{harness_id, changes, reason}`를 호스트 어댑터에 전달한다.
하네스 런타임이나 변경 적용/승인 흐름은 이번에 구현하지 않았으며 이 Tool은 제안 접점이다.

## 실제 웹 조회와 파일 저장 예제

저장소 루트에서 다음 명령을 실행하면 공식 웹페이지 세 곳을 조회하여 블리치 소개를
Markdown으로 저장한다. Python 3.12.14와 외부 HTTPS 연결이 필요하다.

```sh
.venv-linux312/bin/python -m examples.llm.bleach_research
```

실행할 때마다 `tests/llm/reports/runs/research-demo/<실행ID>/`를 만들며 보고서는 `report/bleach.md`,
검증 결과는 `result.json`, Project/Session/Run/Step은 `workspace/` 아래에 남는다.
`--output 새디렉토리`로 위치를 지정할 수도 있다. 기존 디렉토리는 덮어쓰지 않는다.

이 예제는 `GraphEngine → ToolNode → web_fetch/file_create/file_read`를 실제로 실행한다.
HTTP 성공과 근거 문자열, 파일 재조회 내용 및 SHA-256을 확인하고 실패하면 Run을
실패로 남긴다. 페이지 원문 전체 대신 URL, 응답 해시, 조회 시각과 검토된 요약을 기록한다.

**기본 모드에는 LLM 호출이 없다.** 출처 선택과 요약 문장은 공식 자료를 검토해 작성한 템플릿이다.
문자열 검사는 변경 감지를 돕지만 의미상의 사실 검증을 대체하지 않는다. 모델의 자율
Tool 선택·요약까지 검증하려면 `--model`을 지정한다. 이 경우 GraphEngine 대신
LoopEngine을 사용하고 모델에 미리 작성한 요약이 아닌 실제 페이지 본문을 전달한다.

```sh
# .env에 GEMINI_API_KEY가 있거나 실행 프로세스 환경변수에 설정되어 있어야 한다.
.venv-linux312/bin/python -m examples.llm.bleach_research --model gemini/gemini-3.8-flash --env-file .env
```

계정에서 사용 가능한 모델명을 지정한다. `--api-base`로 호환 서버 주소도 지정할 수 있다.
모델은 web_fetch/file_create/file_read 중 다음 Tool을 직접 선택한다. 예제에 지정된
공식 URL 세 곳만 조회할 수 있고 검색 엔진을 통한 사이트 발견은 이 테스트에 포함하지 않는다.
최대 10회 모델 호출을 허용한다. `result.json`의 `mode=live_llm_and_http`와
`artifact_verified=true`를 확인하고 보고서 내용도 검토해야 한다. 실행 완료만으로 출처
링크나 파일 검증이 통과한 것으로 간주하지 않는다.

## 프롬프트 변경 API

AgentComponent를 백엔드에 등록하고 Project에서 agents를 선택한 뒤 연결한다.

```python
agents = await project.components.aget("agents")
toolkit.bind_prompts(agents)
await project.components.tools.aenable("prompt_read", "prompt_update")
```

prompt_read는 `{agent_id}`를 받아 프롬프트와 전체 Agent 정의의 revision을 반환한다.
prompt_update는 `{agent_id, system_prompt, expected_revision}`를 받는다. 같은 Project
잠금에서 revision 확인과 저장을 처리하며 system_prompt 이외의 모델/정책은 유지한다.
직접 UI에서 `agents.aprompt(id)`, `agents.aupdate_prompt(id, text, expected_revision=...)`도
사용할 수 있다. 실행 중인 Run의 capability 스냅샷은 바뀌지 않는다. Agent 설정을 읽는
실행기는 다음 Run에서 새 프롬프트를 반영한다. LoopEngine이 Agent를 자동 선택하지는 않는다.

## GraphEngine 연결과 실행 이력

```python
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.components.workflows import WorkflowGraph

graph = (WorkflowGraph(entry="read")
    .node("read", "tool", tool="file_read", arguments={"path": "README.md"}, result_key="document")
    .node("end", "end").connect("read", "end").to_dict())
engine = GraphEngine("read-document", handlers={"tool": ToolNode()})
```

그래프를 workflows에 read-document ID로 저장하고 Project Tool에서 file_read를
활성화해야 한다. `arguments_key`로 이전 노드 상태의 인자 dict를 참조할 수도 있다.
arguments와 arguments_key는 동시에 지정하지 않는다. 처리 결과는 result_key에 저장한다.

LoopEngine과 ToolNode는 services/runtime/tools.py의 ToolExecutor를 공유한다. registry.prepare로
인자를 검증하고 ToolExecutor가 timeout/JSON 결과/출력 길이를 검사한다. 인자는 시작 Step,
결과는 완료 Step metadata에 저장한다. Run/Step 저장은 기존 서비스가 수행한다.
ToolExecutor는 OS 샌드박스나 하네스 권한 엔진이 아니다. 핸들러 내부의 외부 작업 취소는
해당 구현이 담당한다. 대용량 원문은 결과에 전부 반환하기보다 참조를 반환하는 편이 좋다.

## 검증

```sh
.venv-linux312/bin/python -m unittest tests.llm.test_builtin_tools -v
.venv-linux312/bin/python -m unittest discover -s tests/llm -t . -v
```

임시 폴더에서 실제 파일·셸·프로세스를 사용한다. 웹/브라우저/커널/검색은 주입된 테스트
어댑터를 호출하며 외부 네트워크와 모델 API는 사용하지 않는다.
