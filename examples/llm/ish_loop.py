"""명시적으로 생성·선택한 Project로 LoopEngine을 실행하는 ish 명령 예제.

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
    """Project ID가 없으면 새 Project를 생성한다. 이전 작업 선택은 호출자가 소유한다."""
    # 모델 인자는 이 테스트 실행의 명시적인 호스트 설정이다.
    # 기존 Project의 컴포넌트·정책·모델 설정을 덮어쓰지 않는다.
    async with LargeLanguageModel(
        workspace,
        engines={"loop": LoopEngine(completion_kwargs=completion,
                                    max_iterations=max_iterations,
                                    request_timeout=request_timeout)},
        on_event=print_event,
    ) as backend:
        project = (await backend.projects.aload(args.project_id) if args.project_id
                   else await backend.projects.acreate("ish LoopEngine test", conversation_storage="file"))
        session = (await project.sessions.aload(args.session_id) if args.session_id
                else await project.sessions.acreate("ish LoopEngine test"))
        print(f"Project: {project.paths.root.resolve()}", file=sys.stderr)
        print(f"Session: {session.id}", file=sys.stderr)

        # 실행 엔진 이름은 요청마다 명시한다.
        request = await session.run.submit(" ".join(args.prompt), engine="loop")
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

    completion은 LiteLLM 인자이며 Project/Session 설정보다 우선한다. 생략한 항목은
    기존 Project/Session 설정을 따른다. api_key_env는 실행 시점의 셸에서 읽으므로
    ish 안에서 export한 키도 다음 호출에 적용된다. None이면 별도로 읽지 않는다.
    --session-id 생략 시 새 Session, 지정 시 같은 Session의 이전 대화를 이어간다.
    """
    parser = argparse.ArgumentParser(prog="llm-test", description=__doc__)
    parser.add_argument("--project-id", help="이어갈 Project ID; 생략하면 새 Project 생성")
    parser.add_argument("--session-id", help="선택한 Project 안에서 이어갈 Session ID")
    parser.add_argument("prompt", nargs="+", help="모델에 전달할 작업 요청")
    args = parser.parse_args(list(argv))
    if args.session_id and not args.project_id:
        parser.error("--session-id requires --project-id")
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
