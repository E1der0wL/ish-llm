"""Linux의 동시 Session·반복 대화·저장·재시작을 측정한다. 모델 품질/공급자 장애 시험은 아니다.

python -m examples.llm.operational_probe --seconds 60 --sessions 5
--seconds 10800으로 장시간 관찰할 수 있다. 실행 자료는 새 임시 폴더에 보존한다.
보고서는 stdout의 JSON이며 API 키/네트워크/기존 프로젝트가 필요하지 않다.
"""

import argparse
import asyncio
from collections import deque
import json
from pathlib import Path
import resource
import sys
import tempfile
import threading
import time

from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.query import Query


class ProbeEngine(BaseEngine):
    async def run(self, context):
        text = context.messages[-1].content
        for offset in range(0, len(text), 64):
            yield text[offset:offset + 64]
            await asyncio.sleep(0)


def resources():
    """Linux 현재 RSS와 FD/스레드 수. peak RSS와 혼동하지 않는다."""
    status = Path('/proc/self/status').read_text()
    rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:'))
    return {"rss_kib": rss, "fds": len(list(Path('/proc/self/fd').iterdir())),
            "threads": threading.active_count()}


async def probe(args):
    if sys.version_info[:3] != (3, 12, 14) or sys.platform != 'linux':
        raise RuntimeError('Run this probe on Linux Python 3.12.14')
    root = Path(tempfile.mkdtemp(prefix='llm-operation-probe-'))
    started = time.monotonic()
    latencies, samples = deque(maxlen=10000), deque(maxlen=10000)
    maximum_lag, completed, rounds = 0.0, 0, 0
    stop = asyncio.Event()

    async def monitor():
        nonlocal maximum_lag
        while not stop.is_set():
            before = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), timeout=.1)
            except asyncio.TimeoutError:
                maximum_lag = max(maximum_lag, time.monotonic() - before - .1)

    async def request(session, text):
        before = time.monotonic()
        run = await (await session.run.submit(text, engine='probe')).wait(timeout=120)
        data = await run.aget_data()
        if data.status != 'completed' or (await run.aresponse()).content != text:
            raise AssertionError(f'Incorrect persisted result: {data.id}, {data.status}')
        latencies.append(time.monotonic() - before)

    watcher = asyncio.create_task(monitor())
    try:
        async with LargeLanguageModel(root, components=[], engines={'probe': ProbeEngine()}) as app:
            project = await app.projects.acreate('Operational probe')
            sessions = [await project.sessions.acreate(f'Session {i}') for i in range(args.sessions)]
            samples.append(resources())
            while rounds == 0 or time.monotonic() - started < args.seconds:
                text = f'round={rounds} ' + 'x' * args.payload_chars
                await asyncio.gather(*(request(session, text) for session in sessions))
                completed += len(sessions)
                rounds += 1
                if rounds % 10 == 0:
                    samples.append(resources())
                await asyncio.sleep(args.interval)
            for session in sessions:
                await session.run.shutdown()
        # 같은 저장소를 새 백엔드로 열어 완료 결과를 확인한다. 이전 실행을 재실행하지 않는다.
        async with LargeLanguageModel(root, components=[], engines={'probe': ProbeEngine()}) as app:
            reopened = await app.projects.aload(project.id)
            for previous in sessions:
                session = await reopened.sessions.aload(previous.id)
                latest = await session.run.alist(query=Query(descending=True, limit=1))
                if not latest or (await latest[0].aresponse()).content != text:
                    raise AssertionError('Restart projection mismatch')
                if (await session.run.astatus()).active_run_id is not None:
                    raise AssertionError('Completed request replayed after restart')
    finally:
        stop.set()
        await watcher
    samples.append(resources())
    ordered = sorted(latencies)
    return {"python": sys.version.split()[0], "workspace": str(root), "session_count": args.sessions,
            "elapsed_seconds": time.monotonic() - started, "completed_runs": completed,
            "restart_verified": True, "max_event_loop_lag_seconds": maximum_lag,
            "latency_p95_seconds": ordered[int((len(ordered) - 1) * .95)] if ordered else None,
            "latency_sample_count": len(ordered), "sample_window_limit": 10000,
            "resources_first": samples[0], "resources_last": samples[-1],
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "workspace_bytes": sum(path.stat().st_size for path in root.rglob('*') if path.is_file()),
            "live_model_calls": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--sessions', type=int, default=5)
    parser.add_argument('--payload-chars', type=int, default=512)
    parser.add_argument('--interval', type=float, default=.05)
    args = parser.parse_args()
    import math
    if (not math.isfinite(args.seconds) or args.seconds <= 0 or args.sessions < 1 or args.payload_chars < 1
            or not math.isfinite(args.interval) or args.interval < 0):
        parser.error('seconds/sessions/payload must be positive; interval must be finite and nonnegative')
    print(json.dumps(asyncio.run(probe(args)), indent=2))


if __name__ == '__main__':
    main()
