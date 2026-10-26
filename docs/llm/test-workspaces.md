# 테스트 공간과 OS 샌드박스

빈 공간 생성, 선택 파일 복사, Linux Bubblewrap 실행, 결과 확인, 명시적 삭제를 기본 Tool로
연결한다. 실행은 기존 Run의 Tool Step이며 승인·retry·receipt·재개는 ToolExecutor가 소유한다.
새 실행 도메인, 별도 실행 로그, 자동 원본 반영 또는 자동 정리는 없다.

## 호스트 연결

```python
import sys
from llm.llm import LargeLanguageModel, BackendServices, ToolRuntime
from llm.components.tools.builtin import (
    BuiltinTools, BuiltinToolComponent, BuiltinToolRunner, TestWorkspaces,
)
from llm.engines.loop import LoopEngine

spaces = TestWorkspaces(
    "/srv/ish-test-spaces",             # 다른 관리 파일이 없는 전용 자원 저장소
    source_root="/srv/source",          # 기존 디렉터리; 선택 파일의 복사 원본
    allow_network=False,                # 호스트가 반드시 명시
    read_only_paths=[sys.base_prefix, sys.prefix],
    env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    max_output_bytes=1_000_000,          # 예제의 명시적 자원 선택
)

async def main():
    async with BuiltinTools("/srv/source", test_workspaces=spaces) as toolkit:
        async with LargeLanguageModel(
            "/srv/llm-workspace",
            components=[BuiltinToolComponent(toolkit, name="computer")],
            engines={"loop": LoopEngine()},
            services=BackendServices(tool_runtime=ToolRuntime(runner=BuiltinToolRunner())),
        ) as backend:
            project = await backend.projects.acreate(components=["computer"], config={
                "parameters": {
                    "components": {"computer": {"config": {"enabled": list(toolkit.registry.names())}}},
                },
            })
            await project.agents.acreate({"engine": "loop", "completion": {"model": "openai/your-model"},
                                         "tools": list(toolkit.registry.names())},
                                         identifier="assistant")
            session = await project.sessions.acreate(agent_ids=["assistant"])
            handle = await session.run.submit(
                "빈 테스트 공간에 테스트를 작성하고 실행해줘. 결과는 남겨둬.", recipients=["assistant"])
            result = await handle.wait()
            # PAUSED이면 기존 interaction API로 응답을 저장하고 명시적으로 resume한다.
            # 후속 Tool에도 승인이 필요할 수 있으므로 UI는 동일 절차를 반복한다.
```

`BuiltinToolRunner`는 테스트 실행만 신뢰된 sandbox handler로 전달한다. 파일/RAG/Memory 등의
기존 handler는 그대로 호출한다. 다른 실행기도 있다면 `BuiltinToolRunner(fallback=runner)`로
연결한다. fallback은 테스트 실행 이외의 Tool을 담당한다. runner가 없거나 격리 계약을
만족하지 못하면 실행을 거절한다. 직접 handler 호출은 승인 API가 아니다.

## Tool과 결과

| Tool | 필수 인자 | 의미 |
| --- | --- | --- |
| `test_workspace_create` | `source_paths` | `[]`는 빈 공간. 상대 파일/디렉터리 목록은 같은 위치에 독립 사본 생성. `workspace_id` 반환 |
| `test_workspace_list` | 없음 | 현재 Project의 공간과 생성 완료 여부 조회 |
| `test_workspace_execute` | `workspace_id`, `argv` | 절대 실행 파일로 시작하는 argv 실행. 선택적 env로 환경 전체 대체. `returncode`, `stdout`, `stderr` 반환 |
| `test_workspace_delete` | `workspace_id` | 해당 공간과 산출물 영구 삭제. busy이면 거절 |

기존 파일 Tool에 선택 인자 `workspace_id`를 추가한다. 예를 들어
`file_create(workspace_id=..., path="test.py", content=...)`,
`file_read(workspace_id=..., path="result.txt")`, `file_list(workspace_id=..., recursive=True)`로
테스트 파일을 작성·조회한다. 생략하면 기존 source root에 적용된다.

cwd는 선택 공간의 files 디렉터리다. 셸 문법은
`argv=["/bin/sh", "-c", "python3 test.py > result.txt"]`처럼 명시한다.
`env`를 생략하면 TestWorkspaces의 호스트 환경 설정을 사용하고, `{}`이면 빈 환경을 전달한다.
문자열 딕셔너리를 지정하면 해당 호출에서만 환경 전체를 대체한다. 예:
`env={"PATH": "/usr/bin:/bin", "LM_LICENSE_FILE": "27000@license-server"}`.
상속·변수 치환·문자열 자동 변환은 하지 않는다. 명시한 env는 승인된 Tool 인자에 포함된다.
Bubblewrap은 자체 동작으로 실제 작업 디렉터리를 PWD에 넣는다. 따라서 빈 env여도 sandbox의
최종 환경에는 PWD가 있을 수 있으며, Python/셸이 생성하는 변수 역시 해당 프로그램의 동작이다.
임의의 cwd나 network/mount 옵션은 Tool 인자로 받지 않는다. 라이선스 서버 주소만 전달해도
차단된 네트워크가 열리지는 않는다. 상세 의미는 [명령 환경변수](builtin-tools.md#명령의-환경변수)를 참고한다.
`/usr` 밖의 Python/venv를 쓰려면 해당 runtime을 호스트가 읽기 전용 노출하고 절대 실행 경로를
사용한다. 폐쇄망 의존성은 호스트가 준비한다. 자동 설치는 없다.
기존 `test_run`은 이름 있는 검증 명령 Tool이며 별개로 유지한다.

## 승인·취소·재시작

- 생성·실행·삭제와 파일 변경은 `approval_required=True`다. Project 정책이 자동 응답해도
  명시적 resume 전에는 효과가 없다. 실행의 `isolation="sandbox"` 검증은 프로세스를 생성하지 않는다.
- root, mount, env, network, 자원 설정은 기존 durable binding에 포함한다. 변경된 환경에서
  과거 승인을 재사용하지 않는다. Project ID는 trusted ToolCall에서 얻으며 모델 인자가 아니다.
- 공간별 Linux flock은 실행·파일 접근·삭제를 직렬화한다. busy이면 즉시 거절한다.
  여러 백엔드 프로세스도 동일 잠금을 쓰며 서로 다른 공간은 병렬 실행할 수 있다.
- 명시적 Tool/Loop/Run timeout과 interrupt를 그대로 사용한다. 종료·취소는 프로세스 회수를
  기다린 뒤 잠금을 해제한다. 새로운 기본 timeout/retry를 추가하지 않는다.
- 완료·실패·취소·백엔드 종료 후에도 파일을 유지한다. 재시작 시 list/file_read로 확인할 수
  있으며 명령은 재생하지 않는다. 삭제 이후에는 산출물 복구를 제공하지 않는다.

## 저장 경계

```text
<test_root>/<project_id>/
  .locks/<workspace_id>.lock
  <workspace_id>/
    record.json
    files/                      # sandbox에서 쓰기 가능한 호스트 디렉터리
```

record는 `storage_version=1`, 소유 Project/공간 ID, 생성 출처 Run/Step, 시각, 선택 파일 목록,
`ready`만 보관한다. 실행 결과는 기존 Run/Step이 소유한다. record와 lock은 sandbox에 노출하지 않는다.
`WorkspaceRecord`가 필드·버전·타입·identity·완료 상태의 정규 Owner이며 저장 Reader는 모든
필드를 요구한다. 생성 API의 `ready=false` 기본값을 손실된 저장 필드의 복원에 사용하지 않는다.
출처의 session/run/step 값은 null일 수 있지만 세 필드 자체는 필요하다. 시각은 기존 문자열,
선택 파일은 문자열 목록 계약을 유지한다. 손상된 기록을 정상 기록으로 재작성하거나 삭제하지 않는다.
`WorkspaceRepository`는 경로·복사·잠금·원자적 게시·명시적 삭제를 담당하며 승인·예산·실행
정책을 해석하지 않는다. 실행/파일 조회는 검증된 기록의 `require_ready`를 통과해야 한다.
복사·fsync 후 ready를 원자적으로 게시하며, 중간 실패는 부분 공간을 정리한다.
강제 종료로 남은 `ready=false` 공간은 조회·명시적 삭제가 가능하고 실행은 거절한다.
record 생성 전이나 삭제 중에 중단되어 record가 없는 공간도 `reason=missing_record`로
조회·명시적 삭제가 가능하다. 소유권은 신뢰된 저장소의 Project/공간 경로로 제한하고 실행은 거절한다.
이 관측은 별도 `MissingWorkspaceRecord`이며 `WorkspaceRecord`로 복원하지 않는다.
목록은 관측이므로 동시 생성/삭제 항목이 일시적으로 빠질 수 있다.
생성·삭제 직후 결과 전달 전에 종료되면 효과가 불확실할 수 있어 목록/Step 확인이 필요하다.

정규 파일/디렉터리만 복사한다. symlink/FIFO/device, 상위 경로, 중복·중첩 선택은 거절한다.
원본 root 전체(`.`) 대신 복사할 항목을 명시한다. hardlink를 만들지 않는다.
복사 중 변경을 감지하면 실패하지만 여러 원본 파일의 원자적 snapshot까지 보장하지는 않는다.
lock 파일은 동시 접근 inode를 유지하기 위해 남는다. 이 호스트 자원은 Project clone/export/
삭제에 자동 포함되지 않는다. 산출물 반영과 정리는 Application이 명시적으로 수행한다.

## 격리 범위와 Hub

Bubblewrap과 커널 user namespace 지원이 필요하다. 실패 시 호스트 실행으로 fallback하지 않는다.
nonzero 종료는 결과의 returncode/stderr로 확인한다. filesystem/PID/network namespace,
capability 제거, 부모 사망·취소 시 자손 회수를 사용한다. `/usr`, `/bin`, `/lib`, `/lib64`의
존재하는 경로와 호스트가 지정한 runtime mount는 읽기 전용이다. 환경변수는 명시한 것만 전달한다.
대상 환경은 격리 구성 후 적용하며 Python 시작 게이트와 Bubblewrap 자체는 빈 환경으로 시작한다.
추가 mount에 home/소스 전체, 비밀 파일이나 호스트 소켓을 넣지 않는다.

VM, seccomp, cgroup 합산 메모리/PID 제한 또는 디스크 quota를 제공하지 않는다.
`memory_bytes`는 프로세스별 RLIMIT_AS, `max_output_bytes`는 stdout+stderr 상한이다.
출력 상한 초과 시 잘린 성공 결과 대신 실패한다. 미설정 제한은 추가하지 않는다.
호스트 shell_execute도 허용된 Agent는 그것을 별도로 쓸 수 있으므로, 테스트 전용 실행을 원하면
Application이 Tool allow-list/argument constraints를 좁힌다.

Hub는 `<hub-workspace>/.hub/test-workspaces`와 네트워크 차단 preset을 명시해 연결한다.
기존 Project의 명시적 enabled 목록은 자동 확장하지 않으므로 Settings에서 네 가지 테스트 Tool과
필요한 파일 Tool을 선택한다. 기존 승인 UI와 Run 중단을 그대로 사용한다.
Bubblewrap이 없을 때 Hub는 새 Project의 최초 선택에서 `test_workspace_execute`만 제외한다.
설치 후 직접 활성화한다. 이미 명시한 선택은 자동 변경하지 않으며, 사용할 수 없는 격리 Tool을
명시적으로 선택하면 기존 Run 사전 검증에서 실패한다.

Linux Python 3.12.14 테스트는 `tests/llm/test_test_workspaces.py`, `test_isolated_tools.py`,
Hub `test_question_tools.py`에 있다. 결정적 fake completion과 실제 OS 프로세스/Bubblewrap을
함께 사용하며 외부 모델 호출은 필요하지 않다.
