"""Project Python Tool interpreter 경계. OS filesystem/network sandbox는 아니다."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import MappingProxyType

from llm.errors import CodedError
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.storage import drain_on_cancel
from .registry import Tool, ToolContract, ToolRegistry

# IPC/capture의 유한 메모리 계약이다. 출력은 자르지 않고 한도 초과 시 실패한다.
PROTOCOL_BYTES = 8 * 1024 * 1024
CAPTURE_BYTES = 1024 * 1024


class WorkerError(CodedError, RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InspectionError(CodedError, ValueError):
    code = "tool_failed"


class RemoteToolError(CodedError, RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def descriptor_fingerprint(value):
    return fingerprint(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False))


def _decode(payload, operation):
    from llm.services.runtime.tools import ToolExecutionError
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        raise WorkerError("tool_worker_protocol", "Invalid Tool worker envelope")
    if payload["ok"]:
        if set(payload) != {"ok", "value"}:
            raise WorkerError("tool_worker_protocol", "Invalid Tool worker result")
        return payload["value"]
    error = payload.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("message"), str):
        raise WorkerError("tool_worker_protocol", "Invalid Tool worker error")
    kind, message = error.get("kind"), error["message"]
    if kind == "inspection" and operation == "inspect":
        raise InspectionError(message)
    if kind == "tool_execution" and error.get("code") == "tool_failed":
        if error.get("effect") not in ("none", "uncertain") or type(error.get("retryable")) is not bool:
            raise WorkerError("tool_worker_protocol", "Invalid Tool failure classification")
        raise ToolExecutionError(message, effect=error["effect"], retryable=error["retryable"])
    if kind == "coded" and isinstance(error.get("code"), str) and error["code"].strip():
        raise RemoteToolError(error["code"], message)
    if kind == "unknown":
        raise ToolExecutionError(message, effect="uncertain", retryable=False)
    raise WorkerError("tool_worker_protocol", "Unknown Tool worker error envelope")


async def invoke_worker(request):
    from llm.services.infrastructure.observability import record
    from llm.services.infrastructure.processes import current_process_cancellation
    cancellation = current_process_cancellation()
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    def cancel():
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass  # worker loop가 이미 정리된 경우
    started = time.monotonic()
    status, code = "completed", None
    try:
        with cancellation.register(cancel) if cancellation is not None else nullcontext():
            return await _invoke_worker(request)
    except WorkerError as error:
        status, code = "failed", error.code
        raise
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        record("workers", status, code=code, name=request["operation"],
               duration_seconds=time.monotonic() - started)


async def _invoke_worker(request):
    """새 process group에서 실행한다. 취소/실패/정상 완료 모두 descendants를 회수한다."""
    from llm._platform import require_linux
    from llm.services.infrastructure.observability import record
    require_linux()
    root = Path(__file__).resolve().parents[3]
    worker = Path(__file__).with_name("_worker.py")
    gate = root / "llm/services/runtime/_worker.py"
    # 부모 환경을 복사하지 않는다. -I는 PYTHONPATH/user site 주입도 차단한다.
    env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    command = [sys.executable, "-I", str(worker), str(root)]
    # 기존 handler의 상대 경로 의미를 유지한다. import 경로와 작업 디렉토리는 별개다.
    data = {"argv": command, "cwd": os.getcwd(), "env": env, "payload": request, "memory_bytes": None}
    pending = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", str(gate), str(os.getpid()),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True))
    process, readers = None, []
    spawned = False
    def note_spawn():
        nonlocal spawned
        if not spawned:
            spawned = True
            record("workers", "spawned", name=request["operation"])
    try:
        process = await asyncio.shield(pending)
        note_spawn()

        async def read(stream, limit):
            pieces, size = [], 0
            while True:
                block = await stream.read(8192)
                if not block:
                    return b"".join(pieces)
                size += len(block)
                if size > limit:
                    raise WorkerError("tool_worker_output_limit", "Tool worker output exceeded IPC capture limit")
                pieces.append(block)

        readers = [asyncio.create_task(read(process.stdout, PROTOCOL_BYTES)),
                   asyncio.create_task(read(process.stderr, CAPTURE_BYTES))]
        process.stdin.write(json.dumps(data, ensure_ascii=True, allow_nan=False).encode())
        await process.stdin.drain()
        process.stdin.close()
        # Reap the leader before waiting for pipe EOF: children may still hold the pipes.
        async def reap():
            # Process.wait 자체는 상속된 pipe EOF를 기다릴 수 있다. leader의 exit를
            # 관찰한 뒤 그룹을 회수해야 정상 반환한 Tool의 자식도 남지 않는다.
            while process.returncode is None:
                await asyncio.sleep(0.02)
            await kill_process_tree(process)
        readers.append(asyncio.create_task(reap()))
        stdout, _, _ = await asyncio.gather(*readers)
        if process.returncode != 0:
            raise WorkerError("tool_worker_failed", "Tool worker exited abnormally")
        try:
            payload = json.loads(stdout, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            from llm.core.models import ProjectConfig
            ProjectConfig.validate_settings({"response": payload})
        except (ValueError, TypeError, UnicodeError) as error:
            raise WorkerError("tool_worker_protocol", "Tool worker returned invalid JSON") from error
        return _decode(payload, request["operation"])
    except (OSError, BrokenPipeError) as error:
        raise WorkerError("tool_worker_failed", "Cannot communicate with Tool worker") from error
    finally:
        async def cleanup():
            nonlocal process
            if process is None:
                try:
                    process = await pending
                except Exception:
                    return
            note_spawn()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            async def discard(stream):
                while await stream.read(8192):
                    pass
            drains = [asyncio.create_task(discard(s)) for s in (process.stdout, process.stderr)]
            try:
                await kill_process_tree(process)
                # 그룹 종료 후 EOF를 회수한다. host가 지정하지 않은 cleanup 기한은 만들지 않는다.
                await asyncio.gather(*drains)
            finally:
                for task in drains:
                    task.cancel()
                await asyncio.gather(*drains, return_exceptions=True)
                process.stdin.close()
        await drain_on_cancel(cleanup())


class ProjectToolHandler:
    """함수 대신 검증된 소스 fingerprint만 보관하는 trusted execution adapter."""
    def __init__(self, request):
        self._request = MappingProxyType(dict(request))

    @property
    def execution_binding(self):
        return {key: self._request[key] for key in ("source_hash", "requirements_hash")}

    async def __call__(self, arguments):
        from llm.services.runtime.tools import current_tool_call
        call = current_tool_call()
        return await invoke_worker({**self._request, "operation": "execute", "arguments": arguments,
                                    "call": asdict(call) if call is not None else None})


def inspect_tool(project, name, data, plugin_lib=None):
    from .packages import ToolPaths
    request = {"operation": "inspect", "tool_name": name,
               "source_path": str(ToolPaths.for_project(project).source(name).absolute()),
               "source_hash": fingerprint(data["source"]),
               "requirements_hash": fingerprint(data.get("requirements", "")),
               "plugin_lib": str(plugin_lib) if plugin_lib is not None else None}
    def run():
        return asyncio.run(invoke_worker(request))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        descriptor = run()
    else:
        # 동기 API를 직접 호출한 경우에도 backend loop에서 source를 실행하지 않는다.
        with ThreadPoolExecutor(max_workers=1) as executor:
            descriptor = executor.submit(copy_context().run, run).result()
    if not isinstance(descriptor, dict) or set(descriptor) != {"name", "description", "parameters", "definition", "contract"} or descriptor["name"] != name:
        raise WorkerError("tool_worker_protocol", "Invalid Tool descriptor")
    if (not isinstance(descriptor["description"], str) or not descriptor["description"].strip()
            or any(not isinstance(descriptor[key], dict) for key in ("parameters", "definition", "contract"))
            or not isinstance(descriptor["definition"].get("function"), dict)):
        raise WorkerError("tool_worker_protocol", "Invalid Tool descriptor fields")
    try:
        contract = ToolContract(**descriptor["contract"])
        request["descriptor_hash"] = descriptor_fingerprint(descriptor)
        tool = Tool(name, descriptor["description"], descriptor["parameters"],
                    ProjectToolHandler(request), descriptor["definition"], contract)
        registry = ToolRegistry((tool,))
        return registry.get(name)
    except Exception as error:
        raise WorkerError("tool_worker_protocol", "Invalid Tool descriptor contract") from error
