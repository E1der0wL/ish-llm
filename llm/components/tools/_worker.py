"""Project source를 실행하는 child 전용 entrypoint. protocol과 print FD를 분리한다."""

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import types


def load(request):
    from llm.components.tools.function import function_tool
    from llm.components.tools.packages import ToolPaths, read_package
    from llm.components.tools.process import fingerprint
    source = Path(request["source_path"])
    data = read_package(ToolPaths(source.parent.parent), request["tool_name"])
    if (fingerprint(data["source"]) != request["source_hash"] or
            fingerprint(data.get("requirements", "")) != request["requirements_hash"]):
        raise ValueError("Tool package changed after resolution; resolve again")
    identity = "_ish_llm_tool_" + request["source_hash"]
    module = types.ModuleType(identity)
    module.__file__ = str(source)
    sys.modules[identity] = module
    exec(compile(data["source"], str(source), "exec"), module.__dict__)
    return function_tool(request["tool_name"], getattr(module, "main", None))


async def dispatch(request):
    from llm.core.models import ProjectConfig
    from llm.errors import stable_error_code
    from llm.services.runtime.tools import ToolExecutionError
    try:
        tool = load(request)
        descriptor = {"name": tool.name, "description": tool.description, "parameters": tool.parameters,
                      "definition": tool.definition, "contract": asdict(tool.contract)}
        if request["operation"] == "inspect":
            value = descriptor
        elif request["operation"] == "execute":
            from llm.components.tools.process import descriptor_fingerprint
            # 같은 source라도 import 시 동적으로 만든 default/contract가 바뀌면
            # 승인받은 descriptor와 다른 함수를 실행하지 않는다.
            if descriptor_fingerprint(descriptor) != request["descriptor_hash"]:
                raise ValueError("Tool descriptor changed after inspection")
            # 읽기 전용 호출 출처/idempotency snapshot만 전달한다. 실행 scope/원장 owner는 부모다.
            from llm.services.runtime.tools import ToolCall, _tool_call
            call = ToolCall(**request["call"]) if request.get("call") is not None else None
            token = _tool_call.set(call)
            try:
                value = await tool.handler(request["arguments"])
            finally:
                _tool_call.reset(token)
        else:
            raise ValueError("Unknown Tool worker operation")
        ProjectConfig.validate_settings({"value": value})
        return {"ok": True, "value": value}
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if isinstance(error, ToolExecutionError):
            record = {"kind": "tool_execution", "message": str(error), "code": "tool_failed",
                      "effect": error.effect, "retryable": error.retryable}
        elif stable_error_code(error):
            record = {"kind": "coded", "code": stable_error_code(error), "message": str(error)}
        elif request["operation"] == "inspect":
            record = {"kind": "inspection", "message": str(error)}
        else:
            record = {"kind": "unknown", "message": "Project Tool execution failed"}
        return {"ok": False, "error": record}


def main():
    request = json.load(sys.stdin)
    # fd 1/2 출력(os.write 포함)은 capture pipe만 사용한다. protocol은 별도 fd다.
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.path.insert(0, sys.argv[1])
    if request.get("plugin_lib"):
        sys.path.insert(1, request["plugin_lib"])
    response = asyncio.run(dispatch(request))
    protocol.write(json.dumps(response, ensure_ascii=True, allow_nan=False).encode())
    protocol.flush()
    protocol.close()


if __name__ == "__main__":
    main()
