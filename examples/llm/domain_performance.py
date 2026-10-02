"""메인 도메인의 합성 부하 검사. 모델/API 키 없이 Linux Python 3.12.14에서 실행한다.

python -m examples.llm.domain_performance --sessions 5 --deltas 10000
JSON 보고서를 stdout에 출력하고 측정용 작업 디렉토리를 보존한다.
미세 측정은 실제 디스크 쓰기 처리량과 구분하며 통합 측정은 기본 영속 저장을 사용한다.
"""

import argparse
import asyncio
import gc
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace

from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.results import EngineDelta
from llm.core.models import MessageRole, MessageStatus
from llm.engines.base import EngineEvent, EngineEventType
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from llm.services.query import Query
from llm.services.runtime.output import RunOutputState
from llm.services.runtime.runs import RunRepository
from llm.services.history.conversation import ConversationStore, MemoryConversationStore
from llm.services.infrastructure.storage import record


def measure(operation, repeats):
    """시간은 추적기 없이 측정하고, Python 할당 최고치는 별도 실행에서 측정한다."""
    samples = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    gc.collect()
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {"median_seconds": statistics.median(samples), "samples_seconds": samples,
            "python_peak_bytes": peak}


def microbenchmarks(args, root):
    text = "x" * args.chunk_chars
    event = EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("out", text))

    def streaming():
        state = RunOutputState()
        for _ in range(args.deltas):
            _, change = state.accept(event, set())
            assert change == (text, "append")
        output = state.outputs["out"]
        assert output.text.count("x") == args.deltas * args.chunk_chars
        assert output.sequence == args.deltas

    # 읽기/투영만 분리 측정하기 위한 합성 저널. 실제 Run 경로를 수정하지 않는다.
    path = root / "outputs.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for number in range(1, args.deltas + 1):
            value = EngineDelta("out", text, sequence=number)
            stream.write(json.dumps({"type": "delta", "value": value.to_dict()}) + "\n")
    repository = RunRepository()
    run = SimpleNamespace(paths=SimpleNamespace(state=root))

    def conversation_append():
        store = MemoryConversationStore()
        message = store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING)
        for _ in range(args.deltas):
            store.delta(message.id, text)
        assert store.get(message.id).content.count("x") == args.deltas * args.chunk_chars

    message = MemoryConversationStore().create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING)
    conversation_path = root / "conversation.jsonl"
    with conversation_path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "message.create", "message": record(message)}) + "\n")
        for _ in range(args.deltas):
            stream.write(json.dumps({"type": "message.delta", "id": message.id, "text": text}) + "\n")

    def conversation_replay():
        store = ConversationStore(conversation_path)
        assert store.get(message.id).content.count("x") == args.deltas * args.chunk_chars

    def replay():
        outputs = repository.outputs(run)
        assert len(outputs) == 1 and outputs[0].text.count("x") == args.deltas * args.chunk_chars
        assert outputs[0].sequence == args.deltas

    history = {str(i): SimpleNamespace(id=str(i), status="completed") for i in range(args.history_size)}

    def latest_page():
        rows = Query(descending=True, limit=10).apply(history.values())
        assert [row.id for row in rows] == [str(i) for i in range(args.history_size - 1, max(-1, args.history_size - 11), -1)]

    return {"stream_projection": measure(streaming, args.repeats),
            "journal_projection": measure(replay, args.repeats),
            "conversation_append": measure(conversation_append, args.repeats),
            "conversation_replay": measure(conversation_replay, args.repeats),
            "latest_history_page": measure(latest_page, args.repeats)}


async def integration(args, root):
    """Project→Session→Run→Graph→Step 저장·결과 조회·재개방을 실제 API로 검사한다."""
    async def work(node):
        return {"count": node.state["count"] + 1}

    graph = WorkflowGraph(entry="n0", initial_state={"count": 0})
    for index in range(args.graph_nodes):
        graph.node(f"n{index}", "work").connect(f"n{index}", f"n{index + 1}" if index + 1 < args.graph_nodes else "end")
    graph.node("end", "end")
    options = {"components": [WorkflowComponent()],
               "engines": {"graph": GraphEngine("measure", handlers={"work": work})}}
    workspace = root / "workspace"
    start = time.perf_counter()
    latencies, lag = [], []
    stop = asyncio.Event()

    async def monitor():
        while not stop.is_set():
            before = time.perf_counter()
            try:
                await asyncio.wait_for(stop.wait(), .05)
            except asyncio.TimeoutError:
                lag.append(max(0, time.perf_counter() - before - .05))

    async def request(session):
        before = time.perf_counter()
        run = await (await session.run.submit("synthetic performance check", engine="graph")).wait(timeout=120)
        result = await run.aresult()
        assert result.status == "completed" and result.output.data["count"] == args.graph_nodes
        steps = await run.steps.alist()
        assert len(steps) == args.graph_nodes + 2 and all(step.status == "completed" for step in steps)
        latencies.append(time.perf_counter() - before)

    watcher = asyncio.create_task(monitor())
    try:
        async with LargeLanguageModel(workspace, **options) as app:
            project = await app.projects.acreate("Domain performance", components=["workflows"])
            await (await project.components.aget("workflows")).acreate(graph.to_dict(), identifier="measure")
            sessions = [await project.sessions.acreate(f"Session {i}") for i in range(args.sessions)]
            await asyncio.gather(*(request(session) for session in sessions))
        async with LargeLanguageModel(workspace, **options) as app:
            project = await app.projects.aload(project.id)
            for previous in sessions:
                session = await project.sessions.aload(previous.id)
                runs = await session.run.alist(query=Query(descending=True, limit=1))
                result = await runs[0].aresult()
                assert result.status == "completed" and result.output.data["count"] == args.graph_nodes
    finally:
        stop.set()
        await watcher
    return {"elapsed_seconds": time.perf_counter() - start, "completed_runs": len(latencies),
            "request_median_seconds": statistics.median(latencies), "request_max_seconds": max(latencies),
            "max_event_loop_lag_seconds": max(lag, default=0), "reopen_verified": True,
            "workspace": str(workspace), "live_model_calls": False}


def main(*argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deltas", type=int, default=10000)
    parser.add_argument("--chunk-chars", type=int, default=256)
    parser.add_argument("--history-size", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--graph-nodes", type=int, default=8)
    parser.add_argument("--skip-integration", action="store_true")
    args = parser.parse_args(list(argv) if argv else None)
    if sys.platform != "linux" or sys.version_info[:3] != (3, 12, 14):
        parser.error("Use Linux Python 3.12.14")
    if any(getattr(args, key) < 1 for key in ("deltas", "chunk_chars", "history_size", "repeats", "sessions", "graph_nodes")):
        parser.error("All sizes and repeat counts must be positive")
    root = Path(tempfile.mkdtemp(prefix="llm-domain-performance-"))
    report = {"python": sys.version.split()[0], "platform": sys.platform, "fixture": str(root),
              "parameters": vars(args), "microbenchmarks": microbenchmarks(args, root)}
    if not args.skip_integration:
        report["integration"] = asyncio.run(integration(args, root))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
