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

from llm.errors import CodedError
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.storage import drain_on_cancel
from llm.services.runtime._worker import ProcessGate, ProcessLaunch
from .registry import Tool, ToolRegistry
from llm.core.tool_codes import ToolErrorCode
from llm.core.tool_worker import (ToolWorkerRequest, ToolWorkerResponse, ToolWorkerOperation,
    ToolWorkerDescriptor, ToolWorkerFailureKind, ToolWorkerProtocolError)

# IPC/capture의 유한 메모리 계약이다. 출력은 자르지 않고 한도 초과 시 실패한다.
PROTOCOL_BYTES = 8 * 1024 * 1024
CAPTURE_BYTES = 1024 * 1024
READ_BYTES = 8192
REAP_INTERVAL_SECONDS = 0.02
WIRE_ENCODING = "utf-8"
WIRE_ENSURE_ASCII = True
WIRE_ALLOW_NON_FINITE = False
PLUGIN_ROOT_DEPTH = 3
WORKER_FILENAME = "_worker.py"
INTERPRETER_ARGUMENTS = ("-I",)
INTERPRETER_ENVIRONMENT = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
NORMAL_EXIT = 0


class WorkerError(CodedError, RuntimeError):
    OUTPUT_LIMIT = "Tool worker output exceeded IPC capture limit"
    EXIT = "Tool worker exited abnormally"
    INVALID_JSON = "Tool worker returned invalid JSON"
    COMMUNICATION = "Cannot communicate with Tool worker"

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InspectionError(CodedError, ValueError):
    code = ToolErrorCode.FAILED


class RemoteToolError(CodedError, RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fingerprint(text):
    return hashlib.sha256(text.encode(WIRE_ENCODING)).hexdigest()


def descriptor_fingerprint(value):
    return fingerprint(json.dumps(value, sort_keys=True, ensure_ascii=WIRE_ENSURE_ASCII, allow_nan=WIRE_ALLOW_NON_FINITE))


def _decode(payload, operation):
    from llm.services.runtime.tools import ToolExecutionError
    try:
        response = ToolWorkerResponse.read(payload)
    except ToolWorkerProtocolError as error:
        raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, str(error)) from error
    if response.ok:
        return response.value
    error = response.error
    if error.kind == ToolWorkerFailureKind.INSPECTION and operation == ToolWorkerOperation.INSPECT:
        raise InspectionError(error.message)
    if error.kind == ToolWorkerFailureKind.EXECUTION and error.code == ToolExecutionError.code:
        try:
            classified = ToolExecutionError(error.message, effect=error.effect,
                retryable=error.retryable, recoverable=error.recoverable)
        except ValueError as cause:
            raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, ToolWorkerProtocolError.CLASSIFICATION) from cause
        raise classified
    if error.kind == ToolWorkerFailureKind.CODED and isinstance(error.code, str) and error.code.strip():
        raise RemoteToolError(error.code, error.message)
    if error.kind == ToolWorkerFailureKind.UNKNOWN:
        raise ToolExecutionError(error.message, effect=ToolExecutionError.UNCERTAIN_EFFECT, retryable=False)
    raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, ToolWorkerProtocolError.UNKNOWN_ERROR)

async def invoke_worker(request):
    from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
    from llm.services.infrastructure.processes import current_process_cancellation
    cancellation = current_process_cancellation()
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    def cancel():
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass  # worker loop가 이미 정리된 경우
    started = time.monotonic()
    status, code = ObservationStatus.COMPLETED.value, None
    try:
        with cancellation.register(cancel) if cancellation is not None else nullcontext():
            return await _invoke_worker(request)
    except WorkerError as error:
        status, code = ObservationStatus.FAILED.value, error.code
        raise
    except asyncio.CancelledError:
        status = ObservationStatus.CANCELLED.value
        raise
    finally:
        record(ObservationKind.WORKERS.value, status, code=code, name=request.operation,
               duration_seconds=time.monotonic() - started)


async def _invoke_worker(request):
    """새 process group에서 실행한다. 취소/실패/정상 완료 모두 descendants를 회수한다."""
    from llm._platform import require_linux
    from llm.services.infrastructure.observability import record, ObservationKind, ObservationStatus
    require_linux()
    root = Path(__file__).resolve().parents[PLUGIN_ROOT_DEPTH]
    worker = Path(__file__).with_name(WORKER_FILENAME)
    # 부모 환경을 복사하지 않는다. -I는 PYTHONPATH/user site 주입도 차단한다.
    env = dict(INTERPRETER_ENVIRONMENT)
    command = [sys.executable, *INTERPRETER_ARGUMENTS, str(worker), str(root)]
    # 기존 handler의 상대 경로 의미를 유지한다. import 경로와 작업 디렉토리는 별개다.
    data = ProcessLaunch(command, os.getcwd(), env, request.to_dict(), ProcessLaunch.UNLIMITED_MEMORY)
    pending = asyncio.create_task(asyncio.create_subprocess_exec(
        *ProcessGate.command(sys.executable, os.getpid()),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True))
    process, readers = None, []
    spawned = False
    def note_spawn():
        nonlocal spawned
        if not spawned:
            spawned = True
            record(ObservationKind.WORKERS.value, ObservationStatus.SPAWNED.value, name=request.operation)
    try:
        process = await asyncio.shield(pending)
        note_spawn()

        async def read(stream, limit):
            pieces, size = [], 0
            while True:
                block = await stream.read(READ_BYTES)
                if not block:
                    return b"".join(pieces)
                size += len(block)
                if size > limit:
                    raise WorkerError(ToolErrorCode.WORKER_OUTPUT_LIMIT, WorkerError.OUTPUT_LIMIT)
                pieces.append(block)

        readers = [asyncio.create_task(read(process.stdout, PROTOCOL_BYTES)),
                   asyncio.create_task(read(process.stderr, CAPTURE_BYTES))]
        process.stdin.write(json.dumps(data.to_dict(), ensure_ascii=WIRE_ENSURE_ASCII, allow_nan=WIRE_ALLOW_NON_FINITE).encode())
        await process.stdin.drain()
        process.stdin.close()
        # Reap the leader before waiting for pipe EOF: children may still hold the pipes.
        async def reap():
            # Process.wait 자체는 상속된 pipe EOF를 기다릴 수 있다. leader의 exit를
            # 관찰한 뒤 그룹을 회수해야 정상 반환한 Tool의 자식도 남지 않는다.
            while process.returncode is None:
                await asyncio.sleep(REAP_INTERVAL_SECONDS)
            await kill_process_tree(process)
        readers.append(asyncio.create_task(reap()))
        stdout, _, _ = await asyncio.gather(*readers)
        if process.returncode != NORMAL_EXIT:
            raise WorkerError(ToolErrorCode.WORKER_FAILED, WorkerError.EXIT)
        try:
            payload = json.loads(stdout, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            from llm.core.json_data import JsonObject
            JsonObject.validate(ToolWorkerResponse.success(payload).to_dict())
        except (ValueError, TypeError, UnicodeError) as error:
            raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, WorkerError.INVALID_JSON) from error
        return _decode(payload, request.operation)
    except (OSError, BrokenPipeError) as error:
        raise WorkerError(ToolErrorCode.WORKER_FAILED, WorkerError.COMMUNICATION) from error
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
                while await stream.read(READ_BYTES):
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
    def __init__(self, request, descriptor_hash):
        self._request, self._descriptor_hash = request, descriptor_hash

    @property
    def execution_binding(self):
        return self._request.binding().to_dict()

    async def __call__(self, arguments):
        from llm.services.runtime.tools import current_tool_call
        call = current_tool_call()
        return await invoke_worker(self._request.execute(self._descriptor_hash, arguments,
                                    asdict(call) if call is not None else None))


def inspect_tool(project, name, data, plugin_lib=None):
    from .packages import ToolPaths
    from .package_values import ToolPackageSource
    data = ToolPackageSource.read(data)
    request = ToolWorkerRequest(ToolWorkerOperation.INSPECT, name, project.id,
        str(ToolPaths.for_project(project).source(name).absolute()), fingerprint(data.source),
        fingerprint(data.requirements_text), str(plugin_lib) if plugin_lib is not None else None)
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
    try:
        value = ToolWorkerDescriptor.read(descriptor, name=name)
    except ToolWorkerProtocolError as error:
        raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, str(error)) from error
    try:
        tool = Tool(name, value.description, value.parameters,
                    ProjectToolHandler(request, descriptor_fingerprint(descriptor)),
                    value.definition, contract=value.contract)
        registry = ToolRegistry((tool,))
        return registry.get(name)
    except Exception as error:
        raise WorkerError(ToolErrorCode.WORKER_PROTOCOL, ToolWorkerProtocolError.DESCRIPTOR_CONTRACT) from error
