"""Project source를 실행하는 child 전용 entrypoint. protocol과 print FD를 분리한다."""

import asyncio
import json
import os
from pathlib import Path
import sys
import types


class ProjectToolWorker:
    MODULE_PREFIX = "_ish_llm_tool_"
    CODE_MODE = "exec"
    ENTRY_FUNCTION = "main"
    ENTRYPOINT = "__main__"
    ABSENT_FUNCTION = None
    SOURCE_CHANGED = "Tool package changed after resolution; resolve again"
    DESCRIPTOR_CHANGED = "Tool descriptor changed after inspection"
    STDOUT_FD = 1
    STDERR_FD = 2
    PROTOCOL_MODE = "wb"
    PLUGIN_ROOT_ARGUMENT = 1
    PLUGIN_ROOT_PRIORITY = 0
    SHARED_LIB_PRIORITY = 1


def load(request):
    from llm.components.tools.function import function_tool
    from llm.components.tools.packages import ToolPaths, read_package
    from llm.components.tools.package_values import ToolPackageSource
    from llm.components.tools.process import fingerprint
    source = Path(request.source_path)
    data = ToolPackageSource.read(read_package(ToolPaths(source.parent.parent, request.project_id), request.tool_name))
    if (fingerprint(data.source) != request.source_hash or
            fingerprint(data.requirements_text) != request.requirements_hash):
        raise ValueError(ProjectToolWorker.SOURCE_CHANGED)
    identity = ProjectToolWorker.MODULE_PREFIX + request.source_hash
    module = types.ModuleType(identity)
    module.__file__ = str(source)
    sys.modules[identity] = module
    exec(compile(data.source, str(source), ProjectToolWorker.CODE_MODE), module.__dict__)
    return function_tool(request.tool_name, getattr(module, ProjectToolWorker.ENTRY_FUNCTION, ProjectToolWorker.ABSENT_FUNCTION))


async def dispatch(request):
    from llm.core.json_data import JsonObject
    from llm.core.tool_worker import (ToolWorkerDescriptor, ToolWorkerOperation, ToolWorkerFailureKind,
        ToolWorkerFailure, ToolWorkerResponse, ToolWorkerProtocolError)
    from llm.errors import stable_error_code
    from llm.services.runtime.tools import ToolExecutionError
    try:
        tool = load(request)
        descriptor = ToolWorkerDescriptor.from_tool(tool).to_dict()
        if request.operation == ToolWorkerOperation.INSPECT:
            value = descriptor
        elif request.operation == ToolWorkerOperation.EXECUTE:
            from llm.components.tools.process import descriptor_fingerprint
            # 같은 source라도 import 시 동적으로 만든 default/contract가 바뀌면
            # 승인받은 descriptor와 다른 함수를 실행하지 않는다.
            if descriptor_fingerprint(descriptor) != request.descriptor_hash:
                raise ValueError(ProjectToolWorker.DESCRIPTOR_CHANGED)
            # 읽기 전용 호출 출처/idempotency snapshot만 전달한다. 실행 scope/원장 owner는 부모다.
            from llm.services.runtime.tools import ToolCall
            from llm.services.runtime.tool_context import ToolCallContext
            call = ToolCall.from_dict(request.call) if request.call is not None else None
            with ToolCallContext.scope(call):
                value = await tool.handler(request.arguments)
        else:
            raise ValueError(ToolWorkerProtocolError.UNKNOWN_OPERATION)
        response = ToolWorkerResponse.success(value).to_dict()
        JsonObject.validate(response)
        return response
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if isinstance(error, ToolExecutionError):
            record = ToolWorkerFailure(ToolWorkerFailureKind.EXECUTION, str(error),
                ToolExecutionError.code, error.effect, error.retryable, error.recoverable)
        elif (code := stable_error_code(error)):
            record = ToolWorkerFailure(ToolWorkerFailureKind.CODED, str(error), code)
        elif request.operation == ToolWorkerOperation.INSPECT:
            record = ToolWorkerFailure(ToolWorkerFailureKind.INSPECTION, str(error))
        else:
            record = ToolWorkerFailure(ToolWorkerFailureKind.UNKNOWN, ToolWorkerProtocolError.EXECUTION_FAILED)
        return ToolWorkerResponse.failure(record).to_dict()


def main():
    request = json.load(sys.stdin)
    # fd 1/2 출력(os.write 포함)은 capture pipe만 사용한다. protocol은 별도 fd다.
    protocol = os.fdopen(os.dup(ProjectToolWorker.STDOUT_FD), ProjectToolWorker.PROTOCOL_MODE)
    os.dup2(ProjectToolWorker.STDERR_FD, ProjectToolWorker.STDOUT_FD)
    sys.path.insert(ProjectToolWorker.PLUGIN_ROOT_PRIORITY, sys.argv[ProjectToolWorker.PLUGIN_ROOT_ARGUMENT])
    from llm.core.tool_worker import ToolWorkerRequest, ToolWorkerWire
    request = ToolWorkerRequest.read(request)
    if request.plugin_lib:
        sys.path.insert(ProjectToolWorker.SHARED_LIB_PRIORITY, request.plugin_lib)
    response = asyncio.run(dispatch(request))
    protocol.write(ToolWorkerWire.encode(response))
    protocol.flush()
    protocol.close()


if __name__ == ProjectToolWorker.ENTRYPOINT:
    main()
