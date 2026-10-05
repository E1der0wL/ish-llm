# Tools — Project별 Python Tool 패키지

Project가 소유하는 Python Tool 소스를 저장하고 준비·활성화합니다. 소스 introspection과 실행은 child interpreter에서 수행하며, 승인·retry·operation receipt·Step 수명은 공통 ToolExecutor가 담당합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | tool 데코레이터, Tool/ToolContract/ToolRegistry, ToolComponent와 공개 핸들 import입니다. |
| [decorator.py](decorator.py) | @tool 메타데이터와 불변 실행 계약을 함수에 붙입니다. |
| [function.py](function.py) | 함수 signature·annotation을 JSON Schema로 변환하고 Python default도 검증합니다. |
| [registry.py](registry.py) | Tool/ToolContract와 실행용 ToolRegistry, 인자·definition 검증입니다. |
| [constraints.py](constraints.py) | Host-owned fixed/bounded/selectable을 원본 schema와 교차 검증하고 child 범위 확대를 거부합니다. |
| [component.py](component.py) | Project Python 패키지 저장 구조와 선택된 Tool capability를 관리합니다. |
| [data.py](data.py) | ToolData의 source CRUD, prepare, enable/disable 편의 API입니다. |
| [packages.py](packages.py) | 패키지 파일·requirements 검사, 호스트 의존성 준비와 inspector 연결입니다. |
| [process.py](process.py) | 신뢰한 backend adapter가 inspector/execution worker를 시작하고 응답을 검증합니다. |
| [_worker.py](_worker.py) | Project source를 읽고 검사·실행하는 내부 child 진입점입니다. |
| [resolver.py](resolver.py) | 선택한 Component가 제공한 Tool capability를 런타임 registry로 모읍니다. |
| [builtin/README.md](builtin/README.md) | 호스트가 선택해 제공하는 파일·프로세스·서비스 Tool 안내입니다. |

## 패키지 작성

Host의 `ToolPolicy.argument_constraints`는 Project Tool뿐 아니라 RAG/Memory/builtin에도
동일하게 적용됩니다. 모델용 effective schema와 실행 전 재검증, 승인/checkpoint binding을
연결하며 원본 Tool 정의는 변경하지 않습니다. [인자 제약 안내](../../../docs/llm/tool-constraints.md).

Project는 Python 소스를 소유하고, ish Host는 공용 의존성을 소유하며,
기존 `ToolRegistry → ToolExecutor`가 Run/Step 실행을 담당한다.

```text
<project>/tools/web_search/
  web_search.py
  requirements.txt   (선택)
```

이름은 디렉토리명이며 진입점은 같은 이름의 `.py` 안에 있는 `async main`이다.
경로·이름은 고정 계약이다. symlink, 특수 파일, 다른 파일은 허용하지 않는다.
별도 manifest, JSON Tool record, 생성된 definition cache는 저장하지 않는다.

```python
from typing import Annotated
from pydantic import Field
from llm.components.tools import tool

@tool(effect="read_only", revision="1")
async def main(
    query: Annotated[str, Field(description="Search query")],
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> list[dict]:
    """Search external documents matching the query."""
    # 구현에서 필요한 외부 API를 호출한다.
    return []
```

`@tool`은 원래 함수를 반환하고 기존 불변 `ToolContract`를 붙인다.
revision/effect/isolation/approval_required/operation_key_required를 지원한다.
provider의 `strict`는 명시한 경우에만 definition에 포함된다.
description은 비어 있지 않은 docstring, parameters는 signature/type hints에서 파생한다.
str/int/float/bool/None, Optional/Union/`|`, Literal, list[T], dict[str,T], Annotated를 지원한다.
Annotated의 Pydantic Field 제약은 JSON Schema에 반영한다. 선언하지 않은 인자는 거부한다.
위치 전용 인자, `*args`, `**kwargs`, 누락/지원하지 않는 annotation은 거부한다.
Python signature의 명시적 기본값만 schema default가 된다. JSON-safe 검사와 생성된 JSON Schema 검증을 모두 통과해야 한다. 예를 들어 `limit: int = "ten"`, `query: str = None`, Field 범위 밖의 값은 prepare/introspection에서 거부한다. coercion하지 않으며 `query: str | None = None`은 허용한다.

## Host/UI API

```python
tools = await project.components.aget("tools")
await tools.acreate({"source": source_text, "requirements": "httpx>=0.28,<1\n"},
                    identifier="web_search")
package = await tools.aload("web_search")
packages = await tools.alist()
await tools.aupdate("web_search", {"source": new_source})  # requirements 유지
await tools.asave("web_search", {"source": new_source})   # 전체 교체, requirements 제거
definition = await tools.aprepare("web_search")
await tools.aenable("web_search")
await tools.aset_enabled(["web_search"])
await tools.adisable("web_search")
await tools.adelete("web_search")
```

동기 create/load/list/save/update/prepare/enable/disable/delete도 제공한다.
create는 중복을, save는 없는 패키지를, delete는 enabled 패키지를 거부한다.
CRUD는 소스를 실행하지 않는다. enable은 정적 패키지 검증만 수행한다.
prepare는 의존성이 없어도 inspector child에서 import와 main/schema 검증을 수행한다.
backend에서는 Project source, decorator, type hints를 실행하지 않는다. Run/Step은 만들지 않는다.
선택은 ProjectConfig.parameters.components.tools.enabled에만 저장한다.
설정 저장은 list[str]/중복만 검증하며 패키지 유효성은 준비/실행 경계에서 확인한다.

## Dependencies

`requirements.txt`에는 PEP 508 requirement와 빈 줄/주석을 허용한다.
pip 옵션, `-r`, editable, URL/경로 설치는 거부한다.
준비 순서는 requirements 확인 → 의존성 확인/설치 → 소스 import → 함수 검증이다.

`ish --home /foo/bar`라면 `ish.config.config.PLUGIN_LIB_DIR`인
`/foo/bar/plugin/lib`를 사용한다. `ish.plugin.dependencies.install_dependency`의
staging/ownership/loaded-package 보호/rollback을 그대로 사용한다.
현재 실행 중인 `sys.executable`을 전달하며 별도 pip 구현이나 Tool venv는 없다.
이미 만족하는 배포판은 재사용한다. 기존 버전이 요구조건을 만족하지 않으면 실패하며,
설치 시 기존 공용 배포판 버전을 constraints로 고정해 자동 교체를 방지한다.
같은 Host의 다른 Project/플러그인도 이 환경을 공유한다.
Run/resolve에서는 설치하지 않는다. 누락이면 Host/UI에서 명시적으로 prepare해야 한다.

## 실행과 저장 경계

enabled 패키지만 매 Run inspector에서 로딩한다. child가 JSON-safe descriptor를 반환하면
backend가 schema/definition/contract를 다시 검증해 ToolRegistry에 등록한다.
backend handler는 Project 함수가 아닌 trusted process adapter다.
실제 호출마다 fresh execution child가 생성된다. pyc나 함수/모듈 캐시는 재사용하지 않는다.
모듈 전역 counter/cache/client는 호출 간 보존되지 않는다. 영속 상태는 Project 자료나 외부 저장소를 사용한다.
Run은 해석 당시 source/requirements SHA-256 binding을 유지한다. child가 실제 읽은 내용의
fingerprint를 다시 확인하며, 변경되었으면 실행을 거부한다. 다음 Run은 새 소스를 해석한다.
소스가 같아도 import 시 동적으로 생성한 schema/default/contract가 달라지면 descriptor hash 검사로 실행을 거부한다.
binding은 Loop/Graph 승인·재개 검사에도 포함되므로 변경된 소스에 과거 승인을 적용할 수 없다.
clone/backup은 소스와 requirements만 포함한다. backup 검증은 정적이며 restore는 설치하지 않는다.
Project 삭제가 Host의 공용 plugin/lib를 삭제하지 않는다.

소스는 신뢰한 Host/UI 관리 코드다. worker는 Python runtime isolation이며 **OS sandbox가 아니다**.
filesystem/network/credential 격리는 제공하지 않는다. 같은 process group을 벗어나는
악의적인 descendant까지 격리하려면 기존 sandbox/OS 정책이 필요하다.
소스 생성·수정·준비를 모델 Tool로 자동 노출하지 않는다.
ToolContract의 isolation은 기존 ToolExecutor/명시적 runner 계약을 그대로 따른다.
process/sandbox 실행은 해당 Tool의 runner command 등록이 별도로 필요하다.

ToolExecutor가 승인 → operation 예약/재사용 → handler 호출 순서를 유지한다.
ASK/DENY 상태에서는 execution worker가 생성되지 않는다. prepare/resolve inspector는
Tool invocation 승인의 대상이 아니다. worker는 승인·재시도·Step·영수증을 직접 관리하지 않는다.
명시적인 Tool timeout/Run 중단/backend 종료 시 기존 Linux process-group helper로 회수한다.
비동기 prepare 취소도 inspector를 회수하고 storage transaction 정리를 기다린다.

worker는 `-I`와 최소 locale 환경으로 시작하며 부모 API key/PYTHONPATH/임의 환경을 복사하지 않는다.
신뢰한 plugin root, Python 설치 라이브러리, Host PLUGIN_LIB_DIR을 사용한다. 새 venv/installer는 없다.
작업 디렉토리는 기존 backend 호출의 상대 경로 의미를 유지한다. `current_tool_call()`의
출처/operation/idempotency 정보는 JSON 스냅샷으로 전달하며 runtime scope나 저장소 객체는 전달하지 않는다.
stdout/stderr print는 protocol FD와 분리된다. capture 1 MiB, protocol 8 MiB의 메모리 경계를
넘으면 잘라서 성공시키지 않고 `tool_worker_output_limit`으로 실패한다.

오류 경계는 ToolExecutionError(effect/retryable 보존), trusted CodedError(code 보존),
unknown(tool_failed/uncertain/nonretryable), infrastructure의 네 종류다.
infrastructure code는 tool_worker_failed / tool_worker_protocol / tool_worker_output_limit이다.
CancelledError는 일반 Tool 실패로 변환하지 않는다. full traceback이나 원시 출력은 telemetry에 넣지 않는다.

RAG/Memory/MCP 등 다른 Component의 `tools` capability와 BuiltinTools는 유지한다.
Host-owned 함수는 별도 Component의 `resolve(project, "tools")`에서 ToolRegistry를 반환할 수 있다.
ToolComponent에는 catalog를 주입하지 않는다.

[상위 안내](../README.md)
