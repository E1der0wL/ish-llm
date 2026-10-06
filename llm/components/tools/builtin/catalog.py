"""기본 Tool Schema와 구현을 조립한다. 외부 서비스 Tool은 주입된 어댑터만 노출한다."""

import asyncio
import math
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from llm.components.tools.registry import Tool, ToolRegistry
from llm.core.schema import open_schema
from llm.services.infrastructure.storage import drain_on_cancel
from .files import FileTools
from .processes import ProcessTools
from .diagnostics import DiagnosticTools


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

    def __init__(self, root, *, allow_commands: bool = False, git: bool = False, diagnostics: bool = False,
                 checks: Optional[Mapping[str, Sequence[str]]] = None,
                 adapters: Optional[Mapping[str, Callable]] = None,
                 max_file_bytes: Optional[int] = None, max_output_bytes: Optional[int] = None,
                 max_seconds: Optional[float] = None, shell: Optional[Sequence[str]] = None):
        if any(value is not None and (type(value) is not int or value < 1) for value in (max_file_bytes, max_output_bytes)):
            raise ValueError("Byte limits must be positive integers")
        if max_seconds is not None and (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError("max_seconds must be positive and finite")
        if any(type(value) is not bool for value in (allow_commands, git, diagnostics)):
            raise TypeError("Tool feature switches must be booleans")
        self.files = FileTools(Path(root), max_file_bytes)
        self.processes = ProcessTools(self.files, max_seconds=max_seconds, max_output_bytes=max_output_bytes)
        self.registry = ToolRegistry()
        self.closed = False
        # 재개 binding에 필요한 호스트 실행 환경. client/함수나 숨은 사용자 설정을
        # 저장하지 않는다. 외부 어댑터의 내부 상태는 여전히 해당 호스트가 관리한다.
        self._execution_binding = {"root": str(self.files.root), "shell": list(shell) if shell is not None else None,
            "checks": deepcopy(dict(checks or {})), "max_file_bytes": max_file_bytes,
            "max_output_bytes": max_output_bytes, "max_seconds": max_seconds}
        path = string(minLength=1)
        digest = string(pattern="^[0-9a-f]{64}$")
        patterns = {"type": "array", "items": string(minLength=1), "minItems": 1}
        filters = {"include": patterns, "exclude": patterns}
        specs = {
            "file_read": ("Read UTF-8 source or logs and the whole-file SHA-256. Use line ranges or tail_lines (mutually exclusive). expected_sha256 rejects a changed file.", schema({"path": path, "start_line": {"type": "integer", "minimum": 1}, "max_lines": {"type": "integer", "minimum": 1}, "tail_lines": {"type": "integer", "minimum": 1}, "expected_sha256": digest}, ["path"])),
            "file_list": ("List working directory entries without following links. include/exclude are pathlib relative-path glob patterns; excluded directories are not traversed.", schema({"path": path, "recursive": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1}, **filters}, ["recursive"])),
            "file_search": ("Find literal text in UTF-8 sources or logs, with line numbers, file hashes and optional surrounding lines. include/exclude use pathlib globs. offset skips matches; paging rescans current files, so restart after edits.", schema({"path": path, "query": string(minLength=1), "case_sensitive": {"type": "boolean"}, "recursive": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0}, "context_lines": {"type": "integer", "minimum": 0}, **filters}, ["query", "case_sensitive", "recursive"])),
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
            process_schema = schema({"argv": {"type": "array", "minItems": 1, "items": string(minLength=1)}, "cwd": path, "stdin": {"type": "boolean", "description": "Explicitly open an input pipe for process_write. This is not a PTY."}, **limits}, ["argv"])
            self._register("process_start", "Start an argv command; returns an ID for polling/cancellation.", process_schema, self.processes.start)
            self._register("process_write", "Send exact text to a process started here with stdin=true, or close its input. No automatic newline. Do not replay uncertain writes.",
                           schema({"process_id": string(), "text": string(), "close": {"type": "boolean"}}, ["process_id"]), self.processes.write)
            prefix = list(shell) if shell is not None else []
            if isinstance(shell, str) or not prefix or any(not isinstance(item, str) or not item for item in prefix):
                raise ValueError("shell must be a nonempty argv prefix")
            async def execute_shell(args):
                return await self.processes.execute({"argv": prefix + [args["command"]],
                    **{key: value for key, value in args.items() if key != "command"}})
            self._register("shell_execute", "Execute a host shell command and wait for its result.",
                           schema({"command": string(minLength=1), "cwd": path, **limits}, ["command"]), execute_shell)
        if allow_commands or commands or git:
            for name, method in (("process_status", self.processes.status), ("process_cancel", self.processes.cancel)):
                self._register(name, "Inspect or cancel a process started by this Tool collection.", schema({"process_id": string()}, ["process_id"]), method)
            self._register("process_output", "Read retained process output incrementally. Offsets count decoded Unicode characters independently in stdout/stderr. Use next offsets to avoid duplicate text. truncated means configured capture discarded output; stream ordering is not a global event timeline.",
                schema({"process_id": string(), "stdout_offset": {"type": "integer", "minimum": 0},
                        "stderr_offset": {"type": "integer", "minimum": 0}, "max_chars": {"type": "integer", "minimum": 1, "description": "Maximum characters per stream in this page."}},
                       ["process_id"]), self.processes.output)
        if diagnostics:
            inspector = DiagnosticTools(self.files.root)
            diagnostic_specs = {
                "system_inspect": ("Read Linux kernel, Python, memory, load and work-root disk information.", schema({}), inspector.system),
                "process_list": ("List visible Linux processes and parent/terminal identity. Optional query matches process names. Results are live observations, not an atomic snapshot.",
                    schema({"query": string(), "after_pid": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1}}), inspector.processes),
                "process_inspect": ("Inspect a Linux PID's cwd, command, standard I/O and terminal/process groups. Optional file descriptors expose pipes/sockets. Unavailable fields are explicit; this cannot observe application UI focus or reconstruct past events.",
                    schema({"pid": {"type": "integer", "minimum": 1}, "expected_start_ticks": {"type": "integer", "minimum": 0}, "include_files": {"type": "boolean"}}, ["pid"]), inspector.inspect),
            }
            for name, (description, parameters, operation) in diagnostic_specs.items():
                async def inspect(args, operation=operation):
                    return await asyncio.to_thread(operation, args)
                self._register(name, description, parameters, inspect)
        if git:
            prefix = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--no-pager"]
            async def status(args):
                return await self.processes.execute({"argv": prefix + ["status", "--porcelain=v1"]})
            async def diff(args):
                return await self.processes.execute({"argv": prefix + ["diff", "--no-ext-diff", "--no-textconv"] + (["--cached"] if args.get("staged") else [])})
            self._register("git_status", "Read Git working tree status.", schema({}), status)
            self._register("git_diff", "Read a Git diff without external diff/textconv programs.", schema({"staged": {"type": "boolean"}}), diff)

        adapter_specs = {
            "web_search": schema({"query": string(minLength=1), "limit": {"type": "integer", "minimum": 1}}, ["query"]),
            "web_fetch": schema({"url": string(pattern="^https?://")}, ["url"]),
            "browser_open": schema({"url": string(pattern="^https?://")}, ["url"]),
            "browser_snapshot": schema({"session_id": string()}, ["session_id"]),
            "browser_act": schema({"session_id": string(), "action": open_schema("host browser adapter", category="adapter")}, ["session_id", "action"]),
            "kernel_execute": schema({"session_id": string(), "code": string()}, ["session_id", "code"]),
            "kernel_reset": schema({"session_id": string()}, ["session_id"]),
            "external_rag_search": schema({"corpus": string(), "query": string(minLength=1), "options": open_schema("host RAG adapter", category="adapter")}, ["corpus", "query"]),
            "external_graphrag_search": schema({"corpus": string(), "query": string(minLength=1), "options": open_schema("host GraphRAG adapter", category="adapter")}, ["corpus", "query"]),
            "context_expand": schema({"reference": string(), "scope": {"enum": ["chunk", "section", "topic", "document"]}}, ["reference", "scope"]),
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
        """조회만 공개한다. 영속 지침 변경은 별도의 Refinement 승인 경계를 사용한다."""
        async def read(args):
            return await agents.aprompt(args["agent_id"])
        self._register("prompt_read", "Read a saved Agent prompt and revision.", schema({"agent_id": string()}, ["agent_id"]), read)

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
