"""Linux의 실제 프로세스 종료·잠금·LiteLLM 연결 단절과 ENOSPC 복구 검사.

python3.12 -m llm.examples.recovery_probe --output-dir ~/llm-probes
기존 workspace는 받지 않는다. 매번 새 테스트 디렉토리에 보고서와 로그를 보존한다.
"""

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from llm.core.models import ProjectConfig
from llm.engines.base import BaseEngine, EngineEventType
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.locking import WorkspaceBusyError
from llm.services.infrastructure.storage import atomic_json


CASES = ("crash", "disk-full", "connection", "multiprocess")


def check(condition, message):
    """python -O에서도 검증이 생략되지 않게 명시적으로 실패한다."""
    if not condition:
        raise AssertionError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(root, context):
    """외부 효과의 반복 여부를 볼 합성 작업 기록. 도메인 저장 경로와 분리한다."""
    prompt = next(m.content for m in context.messages if m.id == context.run.input_message_id)
    with (root / "effects.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"prompt": prompt, "run_id": context.run.id}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return prompt


def effects(root):
    path = root / "effects.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def probe_engine(root, gate=None):
    async def work(context):
        prompt = audit(root, context)
        yield "partial" if prompt == "blocked" else "answer:" + prompt
        if prompt == "blocked" and gate is not None:
            await gate.wait()
    return BaseEngine(action=work, name="Recovery probe")


async def hold_workspace(root, spec):
    """영속 부분 출력과 QUEUED 요청이 모두 확인된 뒤 부모에 준비 완료를 알린다."""
    gate, streamed = asyncio.Event(), asyncio.Event()
    def observe(run, event):
        if event.type == EngineEventType.TEXT_DELTA:
            streamed.set()
    async with LargeLanguageModel(root / "workspace", components=[],
            engines={"probe": probe_engine(root, gate)}, on_event=observe) as app:
        project = await app.projects.acreate("Recovery probe", components=[])
        task = await project.tasks.acreate()
        first = await task.run.submit("blocked", engine="probe")
        await streamed.wait()
        queued = await task.run.submit("queued", engine="probe")
        run = await first.aget_run()
        check(run is not None, "Running request has no Run")
        check((await run.aresponse()).content == "partial", "Partial response was not persisted")
        check((await queued.aget_data()).status == "queued", "Second request is not durable QUEUED")
        atomic_json(root / "ready.json", {"project": project.id, "task": task.id,
            "first_request": first.id, "queued_request": queued.id, "run_id": run.id})
        while not (root / "release").exists():
            await asyncio.sleep(.02)
        gate.set()
        await task.run.wait_idle()
    return {"released_normally": True}


async def contend(root, spec):
    """다른 프로세스의 실행권 보유 중 조회·생성의 명시적 거부를 검사한다."""
    ids = read(root / "ready.json")
    blocked = []
    async with LargeLanguageModel(root / "workspace", components=[], engines={"probe": probe_engine(root)}) as app:
        for name, operation in (("create", lambda: app.projects.acreate("must not exist", components=[])),
                                ("load", lambda: app.projects.aload(ids["project"])),
                                ("list", app.projects.alist)):
            try:
                await operation()
            except WorkspaceBusyError:
                blocked.append(name)
            else:
                raise AssertionError("Workspace access was not rejected: " + name)
    return {"blocked": blocked}


async def recover(root, spec):
    """새 프로세스에서 상태를 복구한다. 이전 효과는 재실행하지 않고 대기 요청만 처리한다."""
    ids = read(root / "ready.json")
    async with LargeLanguageModel(root / "workspace", components=[], engines={"probe": probe_engine(root)}) as app:
        project = await app.projects.aload(ids["project"])
        task = await project.tasks.aload(ids["task"])
        await task.run.wait_idle()
        first = await (await task.run.arequest(ids["first_request"])).wait()
        data, response = await first.aget_data(), await first.aresponse()
        expected = spec["expected_status"]
        check(data.status == expected, f"Expected {expected}, got {data.status}")
        if ids.get("run_id"):
            check(first.id == ids["run_id"], "Stale Run was replaced/replayed")
        steps = await first.steps.alist()
        check(steps and all(s.status not in ("pending", "running") for s in steps), "Unfinished Steps remain")
        check(response.status == expected, "Run and Assistant state disagree")
        if spec["case"] in ("crash", "multiprocess"):
            check(response.content == "partial", "Partial output changed")
            queued = await (await task.run.arequest(ids["queued_request"])).wait()
            check((await queued.aresult()).status == "completed", "Queued request was lost")
            check((await queued.aresponse()).content == "answer:queued", "Queued result is incorrect")
            check([e["prompt"] for e in effects(root)] == ["blocked", "queued"], "Effects repeated or queue order changed")
        else:
            check([e["prompt"] for e in effects(root)] == ["work"], "Failed request effect was repeated")
            following = await (await task.run.submit("next", engine="probe")).wait()
            check((await following.aresult()).status == "completed", "Next request cannot run")
            check([e["prompt"] for e in effects(root)] == ["work", "next"], "Unexpected effect count")
        state = await task.run.astatus()
        check(state.active_run_id is None and state.queued_count == 0, "Task did not become idle")
        run_count = len(await task.run.alist())
        check(run_count == 2, "Unexpected orphan/replayed Runs")
    return {"first_run_status": str(data.status), "step_statuses": [str(s.status) for s in steps],
            "partial_text": response.content, "run_count": run_count,
            "effect_count": len(effects(root)), "no_replay": True, "queue_drained": True}


async def disk_failure(root, spec):
    """실제 저장 경로에서 ENOSPC 한 번을 주입한다. 디스크 자체를 채우지 않는다."""
    fired = []
    original_replace, original_sync = os.replace, os.fsync
    stage = spec["stage"]
    app = LargeLanguageModel(root / "workspace", components=[], engines={"probe": probe_engine(root)})
    try:
        project = await app.projects.acreate("Disk failure probe", components=[])
        task = await project.tasks.acreate()
        await task.run.start()

        def replace(source, destination, *args, **kwargs):
            path = Path(destination)
            target = (stage == "begin" and path.name == "task.json" or
                      stage == "finish" and path.name == "run.json" and read(Path(source)).get("status") == "completed")
            if target and not fired:
                fired.append(str(path))
                raise OSError(errno.ENOSPC, "Injected test disk full", str(path))
            return original_replace(source, destination, *args, **kwargs)

        def sync(fd):
            path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if stage == "output" and path.name == "outputs.jsonl" and not fired:
                fired.append(str(path))
                raise OSError(errno.ENOSPC, "Injected test disk full", str(path))
            return original_sync(fd)

        # 전용 자식 프로세스에서만 교체한다. ish 호스트/다른 검사의 저장 함수는 건드리지 않는다.
        os.replace, os.fsync = replace, sync
        try:
            request = await task.run.submit("work", engine="probe")
            try:
                run = await request.wait()
            except OSError as error:
                check(error.errno == errno.ENOSPC and stage != "output", "Unexpected storage error")
            else:
                check(stage == "output" and (await run.aget_data()).status == "failed", "Disk error was reported as success")
        finally:
            os.replace, os.fsync = original_replace, original_sync
        check(len(fired) == 1, "Fault boundary was not reached")
        runs = await task.run.alist()
        data = await task.aget_data()
        if stage == "begin":
            check(not runs and not effects(root), "Failed begin leaked a Run or executed work")
            check((await request.aget_data()).status == "queued", "Input lost after begin rollback")
            check(data.current_run_id is None and data.status == "idle", "Task begin was partially committed")
        else:
            check(len(runs) == 1 and len(effects(root)) == 1, "Unexpected execution count")
            check((await runs[0].aget_data()).status == ("running" if stage == "finish" else "failed"),
                  "Run state differs from storage boundary")
            response = await runs[0].aresponse()
            if stage == "output":
                check(response.content == "" and await runs[0].aoutput_events() == [], "Failed delta was committed")
            else:
                check(response.status == "streaming" and data.status == "running", "Finish rollback was incomplete")
        atomic_json(root / "ready.json", {"project": project.id, "task": task.id,
            "first_request": request.id, "run_id": runs[0].id if runs else None})
    finally:
        try:
            await app.shutdown()
        except OSError as error:
            # 실패한 worker의 원래 예외가 shutdown에서도 회수될 수 있다.
            if not fired or error.errno != errno.ENOSPC:
                raise
    return {"injected_errno": errno.ENOSPC, "stage": stage, "fault_count": len(fired),
            "injection_path": fired[0], "rollback_verified": True}


async def connection_failure(root, spec):
    """로컬 OpenAI 호환 SSE 서버가 TCP를 끊어 실제 LiteLLM 스트림을 실패시킨다."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    calls, disconnected = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
            calls.append(prompt)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def send(value):
                data = ("data: " + (json.dumps(value) if isinstance(value, dict) else value) + "\n\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def chunk(text, finish=None):
                return {"id": "probe", "object": "chat.completion.chunk", "created": 1,
                    "model": "probe", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}]}

            if prompt != "disconnect-before":
                send(chunk("partial" if prompt == "disconnect-mid" else "recovered"))
            if prompt.startswith("disconnect-"):
                # 마지막 HTTP chunk/종료 이벤트 없이 전송을 끊는다.
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                disconnected.append(prompt)
                return
            send(chunk("", "stop"))
            send("[DONE]")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = ProjectConfig(completion={"model": "openai/probe", "api_key": "local-test-only",
        "api_base": f"http://127.0.0.1:{server.server_port}/v1", "num_retries": 0},
        policies={"provider_retry": {"max_retries": 0}})
    engine = LoopEngine(request_timeout=spec["timeout"], max_iterations=2)
    observed = []
    try:
        # SDK 최초 import/클라이언트 준비가 단절 검사의 실행 시간에 섞이지 않게 한다.
        # 준비 호출도 로컬 서버로만 보내고 요청 횟수에 명시적으로 포함한다.
        def warmup():
            import litellm
            return list(litellm.completion(**config["completion"], stream=True,
                timeout=spec["timeout"], messages=[{"role": "user", "content": "warmup"}]))
        await asyncio.to_thread(warmup)
        async with LargeLanguageModel(root / "workspace", components=[], engines={"loop": engine}) as app:
            project = await app.projects.acreate("Connection probe", config=config, components=[])
            task = await project.tasks.acreate()
            for prompt in ("disconnect-before", "disconnect-mid"):
                failed = await task.run.submit(prompt, engine="loop")
                next_request = await task.run.submit("continue", engine="loop")
                run = await failed.wait()
                response, data = await run.aresponse(), await run.aget_data()
                check(data.status == "failed", "Broken connection was treated as success")
                check(prompt in disconnected and bool(data.error) and "timed out" not in data.error.lower(),
                      f"Run failed outside the intended connection boundary: {data.error}")
                check(response.content == ("partial" if prompt == "disconnect-mid" else ""), "Partial response lost or duplicated")
                steps = await run.steps.alist()
                check(steps and all(s.status not in ("running", "pending") for s in steps), "Steps left active")
                completed = await next_request.wait()
                check((await completed.aresult()).status == "completed", "Queue stopped after connection loss")
                check((await completed.aresponse()).content == "recovered", "Recovery response mismatch")
                observed.append({"request": prompt, "run_id": run.id, "status": str(data.status),
                    "partial_text": response.content, "error": data.error})
        async with LargeLanguageModel(root / "workspace", components=[], engines={"loop": engine}) as app:
            project = await app.projects.aload(project.id)
            task = await project.tasks.aload(task.id)
            check(len(await task.run.alist()) == 4, "Request was duplicated after reopen")
            for item in observed:
                run = await task.run.aload(item["run_id"])
                check((await run.aget_data()).status == "failed", "Failure state not durable")
                check((await run.aresponse()).content == item["partial_text"], "Partial response not durable")
        check(calls == ["warmup", "disconnect-before", "continue", "disconnect-mid", "continue"], f"Unexpected provider requests: {calls}")
        return {"transport": "local HTTP/1.1 SSE through litellm.completion", "requests": calls,
                "observations": observed, "reopen_verified": True, "live_remote_model": False}
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def child(root, kind, timeout, tag=None):
    """자식마다 세션·로그를 분리하고 예외/중단 시 이 검사에서 생성한 프로세스만 정리한다."""
    tag = tag or kind
    # ish가 런타임에 추가한 plugin/script·plugin/lib도 외부 Python 자식에게 전달한다.
    paths = [str(Path(__file__).resolve().parents[2]), *sys.path]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(dict.fromkeys(
        str(Path(path or os.getcwd()).resolve()) for path in paths)))
    with (root / f"{tag}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "llm.examples.recovery_probe",
            "--worker", kind, "--root", str(root), "--tag", tag, "--timeout", str(timeout)],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=environment)
        try:
            yield process
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # poll 직후 정상 종료한 자식도 아래 wait에서 회수한다.
            process.wait(timeout=10)


def finished(process, root, tag, timeout):
    code = process.wait(timeout=timeout)
    check(code == 0, f"Worker {tag} exited {code}; inspect {root / (tag + '.log')}")
    return read(root / f"result-{tag}.json")


def ready(process, root, timeout):
    deadline = time.monotonic() + timeout
    while not (root / "ready.json").exists():
        check(process.poll() is None, f"Holder stopped before readiness; inspect {root / 'hold.log'}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for durable partial output and queued input")
        time.sleep(.02)


def workspace_digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "workspace").rglob("*")) if p.is_file() and p.suffix in (".json", ".jsonl")}


def run_case(name, root, *, timeout, processes):
    spec = {"case": name, "expected_status": "completed" if name == "multiprocess" else "interrupted"}
    atomic_json(root / "probe.json", spec)
    if name in ("crash", "multiprocess"):
        with child(root, "hold", timeout) as holder:
            ready(holder, root, timeout)
            if name == "crash":
                os.killpg(holder.pid, signal.SIGKILL)
                check(holder.wait(timeout=timeout) == -signal.SIGKILL, "Holder was not killed with SIGKILL")
                observations = {"signal": "SIGKILL"}
            else:
                before = workspace_digest(root)
                with ExitStack() as stack:
                    contenders = [stack.enter_context(child(root, "contend", timeout, f"contend-{i}")) for i in range(processes)]
                    results = [finished(p, root, f"contend-{i}", timeout) for i, p in enumerate(contenders)]
                check(before == workspace_digest(root), "Rejected processes modified persisted domain records")
                (root / "release").touch()
                finished(holder, root, "hold", timeout)
                observations = {"contenders": results, "records_unchanged": True, "mode": "exclusive_workspace_owner"}
        with child(root, "recover", timeout) as process:
            observations["recovery"] = finished(process, root, "recover", timeout)
        return observations
    if name == "disk-full":
        observations = {}
        for stage, expected in (("begin", "completed"), ("output", "failed"), ("finish", "interrupted")):
            directory = root / stage
            directory.mkdir()
            atomic_json(directory / "probe.json", {**spec, "stage": stage, "expected_status": expected})
            with child(directory, "disk", timeout) as process:
                failure = finished(process, directory, "disk", timeout)
            with child(directory, "recover", timeout) as process:
                recovery = finished(process, directory, "recover", timeout)
            observations[stage] = {"failure": failure, "recovery": recovery}
        return {"mode": "one_shot_ENOSPC_at_three_storage_boundaries", "stages": observations}
    with child(root, "connection", timeout) as process:
        return finished(process, root, "connection", timeout)


def main(*argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--output-dir", type=Path, help="새 테스트 디렉토리를 생성할 부모 경로")
    parser.add_argument("--processes", type=int, default=3, help="동시에 접근할 경쟁 프로세스 수")
    parser.add_argument("--timeout", type=float, default=60, help="자식 프로세스별 제한 시간(초)")
    parser.add_argument("--worker", choices=("hold", "contend", "recover", "disk", "connection"), help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--tag", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv) if argv else None)
    if sys.platform != "linux" or sys.version_info[:3] != (3, 12, 14):
        parser.error("Use Linux Python 3.12.14")
    if args.processes < 2 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("processes must be at least 2; timeout must be positive and finite")
    if args.worker:
        if args.root is None or not (args.root / "probe.json").is_file() or not re.fullmatch(r"[a-z0-9-]+", args.tag or ""):
            parser.error("Internal worker requires its probe directory and tag")
        handlers = {"hold": hold_workspace, "contend": contend, "recover": recover,
                    "disk": disk_failure, "connection": connection_failure}
        spec = {**read(args.root / "probe.json"), "timeout": args.timeout}
        result = asyncio.run(asyncio.wait_for(handlers[args.worker](args.root, spec), args.timeout))
        atomic_json(args.root / f"result-{args.tag}.json", result)
        raise SystemExit(0)
    directory = args.output_dir.expanduser().resolve() if args.output_dir else None
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="llm-recovery-probe-", dir=directory))
    report = {"python": sys.version.split()[0], "root": str(root), "status": "running", "cases": {}}
    selected = CASES if args.case == "all" else (args.case,)
    interrupted = False
    try:
        for name in selected:
            case_root = root / name
            case_root.mkdir()
            started = time.monotonic()
            try:
                details = run_case(name, case_root, timeout=args.timeout, processes=args.processes)
                report["cases"][name] = {"status": "passed", "details": details}
            except Exception as error:
                report["cases"][name] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
            report["cases"][name]["elapsed_seconds"] = time.monotonic() - started
            atomic_json(root / "report.json", report)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        report["status"] = ("interrupted" if interrupted else "passed" if
            len(report["cases"]) == len(selected) and all(c["status"] == "passed" for c in report["cases"].values()) else "failed")
        atomic_json(root / "report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(130 if interrupted else 0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main(*sys.argv[1:])
