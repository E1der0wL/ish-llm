"""기본 Project로 LoopEngine을 실행하는 ish 명령 예제.

.ishrc.py에서는 main을 functools.partial로 등록한다. 살아 있는 백엔드는
프로세스 사이에 전달하지 않고, 호출마다 worker의 이벤트 루프 안에서 생성한다.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from llm.llm import LargeLanguageModel, LoopEngine, RunStatus, print_event


async def run_request(args: argparse.Namespace, *, workspace: Path,
                      completion: Mapping[str, Any], max_iterations: int,
                      request_timeout: float) -> int:
    """기본 Project와 선택한 Task로 요청 한 건을 처리하고 저장된 결과를 확인한다."""
    # 모델 인자는 이 테스트 실행의 명시적인 호스트 설정이다.
    # 기존 Project의 컴포넌트·정책·모델 설정을 덮어쓰지 않는다.
    async with LargeLanguageModel(
        workspace,
        engines={"loop": LoopEngine(completion_kwargs=completion,
                                    max_iterations=max_iterations,
                                    request_timeout=request_timeout)},
        on_event=print_event,
    ) as backend:
        # 처음에는 파일 대화 저장 및 모든 기본 컴포넌트가 선택된다.
        # 이후에는 같은 Project와 사용자가 변경한 설정을 그대로 불러온다.
        project = await backend.projects.aget_default()
        task = (await project.tasks.aload(args.task_id) if args.task_id
                else await project.tasks.acreate("ish LoopEngine test"))
        print(f"Project: {project.paths.root.resolve()}", file=sys.stderr)
        print(f"Task: {task.id}", file=sys.stderr)

        # 기본 Project에서도 실행 엔진 이름은 요청마다 명시한다.
        request = await task.run.submit(" ".join(args.prompt), engine="loop")
        handle = await request.wait()
        result = await handle.aresult()
        print()
        print(f"Run: {handle.id} ({result.status.value})", file=sys.stderr)
        print(f"종료 이유: {result.finish_reasons}", file=sys.stderr)
        print(f"전체 사용 토큰: {result.total_tokens}", file=sys.stderr)
        if result.status != RunStatus.COMPLETED:
            print(f"작업 미완료: {result.error or result.status.value}", file=sys.stderr)
            if result.status == RunStatus.PAUSED:
                print("승인/재개가 필요합니다. 저장된 Run의 interaction과 resume API를 확인하세요.",
                      file=sys.stderr)
            return 1
        return 0


def main(*argv: str, workspace: Optional[str] = None,
         completion: Optional[Mapping[str, Any]] = None,
         api_key_env: Optional[str] = "ISH_LLM_API_KEY",
         max_iterations: int = 8, request_timeout: float = 120) -> None:
    """ish가 pickle로 전달하는 동기 진입점. 반환값 대신 프로세스 종료 코드를 사용한다.

    completion은 LiteLLM 인자이며 Project/Task 설정보다 우선한다. 생략한 항목은
    기존 Project/Task 설정을 따른다. api_key_env는 실행 시점의 셸에서 읽으므로
    ish 안에서 export한 키도 다음 호출에 적용된다. None이면 별도로 읽지 않는다.
    --task-id 생략 시 새 Task, 지정 시 같은 Task의 이전 대화를 이어간다.
    """
    parser = argparse.ArgumentParser(prog="llm-test", description=__doc__)
    parser.add_argument("--task-id", help="기본 Project 안에서 이어갈 Task ID")
    parser.add_argument("prompt", nargs="+", help="모델에 전달할 작업 요청")
    args = parser.parse_args(list(argv))
    parameters = dict(completion or {})
    if api_key_env and os.environ.get(api_key_env):
        parameters["api_key"] = os.environ[api_key_env]
    root = Path(workspace).expanduser() if workspace else Path.home() / ".ish" / "llm-workspace"
    try:
        code = asyncio.run(run_request(args, workspace=root, completion=parameters,
                                       max_iterations=max_iterations,
                                       request_timeout=request_timeout))
    except KeyboardInterrupt:
        code = 130
    except Exception as error:
        print(f"llm-test: {type(error).__name__}: {error}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main(*sys.argv[1:])
