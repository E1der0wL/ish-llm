"""Linux 프로세스 그룹의 수명 종료를 공통으로 처리한다."""

import asyncio
import os
import signal


async def kill_process_tree(process, *, timeout_seconds=5):
    """새 세션으로 시작한 그룹을 종료한다. setsid로 이탈한 자식은 sandbox가 필요하다."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout_seconds)
    except asyncio.TimeoutError:
        raise RuntimeError("Process group did not terminate within cleanup deadline") from None
