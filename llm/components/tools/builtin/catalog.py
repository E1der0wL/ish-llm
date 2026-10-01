"""기본 Tool Schema와 구현을 조립한다. 외부 서비스 Tool은 주입된 어댑터만 노출한다."""

import asyncio
import math
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from llm.components.tools.registry import Tool, ToolRegistry
from llm.services.infrastructure.storage import drain_on_cancel
from .files import FileTools
from .processes import ProcessTools


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def string(**options):
    return {"type": "string", **options}


class BuiltinTools:
    """파일 Tool과 선택적 실행/서비스 어댑터를 소유하는 async 수명 관리 객체.

    registry는 호스트가 정의한 Component의 tools capability로 제공한다.
    ToolComponent는 Project Python 소스만 담당하며 이 런타임 객체를 받지 않는다.
    allow_commands는 임의의 호스트 코드 실행을 허용한다. 경로 제한형 샌드박스가 아니다.
    """

    def __init__(self, root, *, allow_commands: bool = False, git: bool = False,
                 checks: Optional[Mapping[str, Sequence[str]]] = None,
                 adapters: Optional[Mapping[str, Callable]] = None,
                 max_file_bytes: Optional[int] = None, max_output_bytes: Optional[int] = None,
                 max_seconds: Optional[float] = None, shell: Optional[Sequence[str]] = None):
        if any(value is not None and (type(value) is not int or value < 1) for value in (max_file_bytes, max_output_bytes)):
            raise ValueError("Byte limits must be positive integers")
        if max_seconds is not None and (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError("max_seconds must be positive and finite")
        if type(allow_commands) is not bool or type(git) is not bool:
            raise TypeError("Tool feature switches must be booleans")
        self.files = FileTools(Path(root), max_file_bytes)
        self.processes = ProcessTools(self.files, max_seconds=max_seconds, max_output_bytes=max_output_bytes)
        self.registry = ToolRegistry()
        self.closed = False
        path = string(minLength=1)
        digest = string(pattern="^[0-9a-f]{64}$")
        specs = {
            "file_read": ("Read a UTF-8 file and its SHA-256 version.", schema({"path": path, "start_line": {"type": "integer", "minimum": 1}, "max_lines": {"type": "integer", "minimum": 1, "maximum": 5000}}, ["path"])),
            "file_list": ("List working directory entries without following links.", schema({"path": path, "recursive": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}, ["recursive"])),
            "file_search": ("Find literal text in UTF-8 files; return line numbers.", schema({"path": path, "query": string(minLength=1), "case_sensitive": {"type": "boolean"}, "recursive": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["query", "case_sensitive", "recursive"])),
            "file_create": ("Create a new UTF-8 file; never overwrite an existing file.", schema({"path": path, "content": string()}, ["path", "content"])),
            "file_patch": ("Replace exactly one matching text after checking the file version.", schema({"path": path, "expected_sha256": digest, "old_text": string(minLength=1), "new_text": string()}, ["path", "expected_sha256", "old_text", "new_text"])),
            "file_move": ("Move a version-checked file without overwriting the destination.", schema({"path": path, "destination": path, "expected_sha256": digest}, ["path", "destination", "expected_sha256"])),
            "file_delete": ("Move a version-checked file to recoverable trash.", schema({"path": path, "expected_sha256": digest}, ["path", "expected_sha256"])),
            "file_restore": ("Restore a trash item to an unused relative path.", schema({"path": path, "trash_id": string(pattern="^[0-9a-f]{32}$")}, ["path", "trash_id"])),
        }
        for name, (description, parameters) in specs.items():
            operation = getattr(self.files, name)
            async def file_call(args, operation=operation):
                # 승인된 파일 변경은 완료를 기다린 뒤 취소를 전달한다.
                return await drain_on_cancel(asyncio.to_thread(operation, args))
            self._register(name, description, parameters, file_call)

        limits = {"timeout_seconds": {"type": "number", "exclusiveMinimum": 0, **({"maximum": max_seconds} if max_seconds is not None else {})}}
        commands = dict(checks or {})
        for name, argv in commands.items():
            if not isinstance(name, str) or not name or isinstance(argv, str) or not argv or any(not isinstance(item, str) or not item for item in argv):
                raise ValueError("Checks must map names to nonempty argv sequences")
            commands[name] = list(argv)
        if commands:
            async def check(args):
                return await self.processes.execute({"argv": commands[args["name"]], **{key: args[key] for key in limits if key in args}})
            for name in ("check_run", "test_run"):
                self._register(name, "Run a developer-configured validation command.",
                               schema({"name": {"enum": list(commands)}, **limits}, ["name"]), check)
        if allow_commands:
            process_schema = schema({"argv": {"type": "array", "minItems": 1, "items": string(minLength=1)}, "cwd": path, **limits}, ["argv"])
            self._register("process_start", "Start an argv command; returns an ID for polling/cancellation.", process_schema, self.processes.start)
            prefix = list(shell) if shell is not None else []
            if isinstance(shell, str) or not prefix or any(not isinstance(item, str) or not item for item in prefix):
                raise ValueError("shell must be a nonempty argv prefix")
            async def execute_shell(args):
                return await self.processes.execute({"argv": prefix + [args["command"]],
                    **{key: value for key, value in args.items() if key != "command"}})
            self._register("shell_execute", "Execute a host shell command and wait for its result.",
                           schema({"command": string(minLength=1), "cwd": path, **limits}, ["command"]), execute_shell)
        if allow_commands or commands or git:
            for name, method in (("process_status", self.processes.status), ("process_output", self.processes.status), ("process_cancel", self.processes.cancel)):
                self._register(name, "Inspect or cancel a process started by this Tool collection.", schema({"process_id": string()}, ["process_id"]), method)
        if git:
            prefix = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--no-pager"]
            async def status(args):
                return await self.processes.execute({"argv": prefix + ["status", "--porcelain=v1"]})
            async def diff(args):
                return await self.processes.execute({"argv": prefix + ["diff", "--no-ext-diff", "--no-textconv"] + (["--cached"] if args.get("staged") else [])})
            self._register("git_status", "Read Git working tree status.", schema({}), status)
            self._register("git_diff", "Read a Git diff without external diff/textconv programs.", schema({"staged": {"type": "boolean"}}), diff)

        adapter_specs = {
            "web_search": schema({"query": string(minLength=1), "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
            "web_fetch": schema({"url": string(pattern="^https?://")}, ["url"]),
            "browser_open": schema({"url": string(pattern="^https?://")}, ["url"]),
            "browser_snapshot": schema({"session_id": string()}, ["session_id"]),
            "browser_act": schema({"session_id": string(), "action": {"type": "object"}}, ["session_id", "action"]),
            "kernel_execute": schema({"session_id": string(), "code": string()}, ["session_id", "code"]),
            "kernel_reset": schema({"session_id": string()}, ["session_id"]),
            "external_rag_search": schema({"corpus": string(), "query": string(minLength=1), "options": {"type": "object"}}, ["corpus", "query"]),
            "external_graphrag_search": schema({"corpus": string(), "query": string(minLength=1), "options": {"type": "object"}}, ["corpus", "query"]),
            "context_expand": schema({"reference": string(), "scope": {"enum": ["chunk", "section", "topic", "document"]}}, ["reference", "scope"]),
            "harness_propose_update": schema({"harness_id": string(), "changes": {"type": "object"}, "reason": string()}, ["harness_id", "changes", "reason"]),
        }
        for name, handler in (adapters or {}).items():
            if name in ("rag_search", "graphrag_search"):
                raise ValueError(f"BuiltinTools adapter {name!r} was renamed to 'external_{name}'. "
                                 "Update the adapter key and Project enabled tool names. "
                                 "Local RAG components provide the unprefixed search tools.")
            if name not in adapter_specs or not callable(handler):
                raise ValueError("Unknown or non-callable builtin adapter")
            description = (f"Search an external corpus through the host adapter {name}. "
                           "The host interprets corpus/query/options; this does not search the current Project's local RAG."
                           if name in ("external_rag_search", "external_graphrag_search")
                           else f"Invoke the configured host adapter: {name}.")
            self._register(name, description, adapter_specs[name], handler)

    def _register(self, name, description, parameters, handler):
        async def guarded(args):
            if self.closed:
                raise RuntimeError("BuiltinTools is closed")
            return await handler(args)
        self.registry.register(Tool(name, description, parameters, guarded))

    def bind_prompts(self, agents) -> None:
        """Agent 핸들에 프롬프트 조회/변경 Tool을 연결한다. 모델·권한 설정은 수정하지 않는다."""
        async def read(args):
            return await agents.aprompt(args["agent_id"])
        async def update(args):
            return await agents.aupdate_prompt(args["agent_id"], args["system_prompt"], expected_revision=args["expected_revision"])
        self._register("prompt_read", "Read a saved Agent prompt and revision.", schema({"agent_id": string()}, ["agent_id"]), read)
        self._register("prompt_update", "Update only a saved Agent system prompt after a revision check.",
                       schema({"agent_id": string(), "system_prompt": {"type": ["string", "null"]}, "expected_revision": string()}, ["agent_id", "system_prompt", "expected_revision"]), update)

    async def close(self):
        if not self.closed:
            self.closed = True
            await drain_on_cancel(self.processes.close())

    async def __aenter__(self):
        if self.closed:
            raise RuntimeError("BuiltinTools is closed")
        return self

    async def __aexit__(self, *exc):
        await self.close()
