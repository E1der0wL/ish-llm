# Project Python Tools

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
Python signature의 명시적 기본값만 schema default가 된다.

## Host/UI API

```python
tools = project.components.tools
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
prepare는 의존성이 없어도 import와 main/schema 검증을 수행한다. Run/Step은 만들지 않는다.
선택은 ProjectConfig.component_configurations.tools.enabled에만 저장한다.
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

enabled 패키지만 매 Run 로딩한다. 모듈 identity는 Project ID/Tool 이름/소스와 requirements
해시를 포함한다. pyc나 함수 캐시를 재사용하지 않으므로 저장 후 다음 Run에 반영된다.
실행 중 Run은 자신의 기존 Tool snapshot을 유지한다.
clone/backup은 소스와 requirements만 포함한다. backup 검증은 정적이며 restore는 설치하지 않는다.
Project 삭제가 Host의 공용 plugin/lib를 삭제하지 않는다.

소스는 신뢰한 Host/UI 관리 코드다. Python import도 코드 실행이므로 prepare/resolve가
sandbox라고 가정하면 안 된다. 소스 생성·수정·준비를 모델 Tool로 자동 노출하지 않는다.
ToolContract의 isolation은 기존 ToolExecutor/명시적 runner 계약을 그대로 따른다.
process/sandbox 실행은 해당 Tool의 runner command 등록이 별도로 필요하다.
승인·operation key·retry/resume 계약이 바뀌는 소스 수정은 작성자가 revision도 변경해야 한다.

RAG/Memory/MCP 등 다른 Component의 `tools` capability와 BuiltinTools는 유지한다.
Host-owned 함수는 별도 Component의 `resolve(project, "tools")`에서 ToolRegistry를 반환할 수 있다.
ToolComponent에는 catalog를 주입하지 않는다.
