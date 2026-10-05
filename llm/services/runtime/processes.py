"""ToolPolicy에 주입하는 유한 프로세스 실행기. 등록 명령만 실행하며 모델 인자는 JSON stdin이다."""

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Optional, Sequence, TYPE_CHECKING, Union

from llm._platform import require_linux
from asyncio import timeout
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.storage import drain_on_cancel
from llm.policies import ExecutionLimitError
from llm.services.runtime.policies import positive_seconds

if TYPE_CHECKING:
    from llm.services.runtime.tools import ToolCall


class ProcessToolRunner:
    """sandbox는 Linux Bubblewrap 필수다. process는 명시적으로 선택하는 비보안 프로세스 격리다.

    commands={Tool명: 절대 실행 파일로 시작하는 argv}; 명령은 stdin의 ToolCall JSON을 읽고
    stdout에 JSON 결과 하나를 출력한다. 기존 Python 클로저를 자동 직렬화하지 않는다.
    cwd는 전용 작업 디렉토리로 지정하고 workspace 메타데이터 디렉토리를 노출하지 않는다.
    """

    def __init__(self, commands: Mapping[str, Sequence[str]], *, cwd: Union[str, Path],
                 isolation: str, env: Optional[Mapping[str, str]] = None,
                 read_only_paths: Sequence[Union[str, Path]] = (), timeout_seconds: Optional[float] = None,
                 max_output_bytes: Optional[int] = None, memory_bytes: Optional[int] = None,
                 max_concurrency: Optional[int] = None,
                 allow_network: Optional[bool] = None,
                 python_executable: Optional[Union[str, Path]] = None):
        require_linux()
        positive_seconds(timeout_seconds, "Process timeout")
        if isolation not in ("sandbox", "process"):
            raise ValueError("Choose sandbox or process")
        if isolation == "sandbox" and type(allow_network) is not bool:
            raise ValueError("allow_network must be boolean")
        for value in (max_output_bytes, memory_bytes, max_concurrency):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("Process limits must be positive integers")
        self.commands = {}
        for name, argv in commands.items():
            if (not isinstance(name, str) or not name or not isinstance(argv, (list, tuple)) or not argv
                    or any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not Path(argv[0]).is_absolute()):
                raise ValueError("Commands require named argv lists with absolute executables")
            self.commands[name] = tuple(argv)
        self.cwd = Path(cwd).resolve(strict=True)
        if not self.cwd.is_dir():
            raise ValueError("Process cwd must be a directory")
        self.isolation, self.env = isolation, dict(env or {})
        self.allow_network = allow_network
        if any(not isinstance(k, str) or not isinstance(v, str) or "=" in k or "\0" in k + v for k, v in self.env.items()):
            raise ValueError("Invalid process environment")
        self.read_only_paths = tuple(Path(path).resolve(strict=True) for path in read_only_paths)
        self.timeout_seconds, self.max_output_bytes = timeout_seconds, max_output_bytes
        self.memory_bytes = memory_bytes
        self.max_concurrency = max_concurrency
        self.python = str(Path(python_executable or sys.executable).resolve(strict=True))
        self._loop, self._slots = None, None

    def _command(self, name):
        try:
            command = list(self.commands[name])
        except KeyError:
            raise ExecutionLimitError("process_failed", f"No process command registered for Tool: {name}") from None
        if self.isolation == "process":
            return command
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise ExecutionLimitError("sandbox_unavailable", "Linux Bubblewrap is required; no unsandboxed fallback is allowed")
        args = [bwrap, "--unshare-all", "--unshare-user", "--die-with-parent", "--new-session",
                "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        if self.allow_network:
            args += ["--share-net"]
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists():
                args += ["--ro-bind", path, path]
        for path in self.read_only_paths:
            if path == Path("/") or any(root in (path, *path.parents) for root in (Path("/proc"), Path("/dev"), Path("/sys"))):
                raise ValueError("Cannot expose a whole host or special filesystem")
            args += ["--ro-bind", str(path), str(path)]
        if str(self.cwd) in ("/", "/usr", "/bin", "/lib", "/lib64", "/proc", "/dev", "/tmp", "/var", "/run", "/home", "/etc", "/sys"):
            raise ValueError("Sandbox workdir must be a dedicated directory")
        for protected in (Path(self.python), Path(__file__).resolve()):
            if self.cwd in (protected, *protected.parents):
                raise ValueError("Sandbox workdir cannot contain the trusted runner or Python runtime")
        args += ["--bind", str(self.cwd), str(self.cwd), "--chdir", str(self.cwd), "--", *command]
        return args

    async def _execute(self, call):
        command = self._command(call.name)
        env = dict(self.env)
        data = {"argv": command, "cwd": str(self.cwd), "env": env, "payload": asdict(call), "memory_bytes": self.memory_bytes}
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        worker = Path(__file__).with_name("_worker.py")
        pending = asyncio.create_task(asyncio.create_subprocess_exec(
            self.python, "-I", "-S", str(worker), str(os.getpid()), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True))
        process, readers = None, []
        try:
            process = await asyncio.shield(pending)
            total = 0

            async def read(stream):
                nonlocal total
                chunks = []
                while True:
                    chunk = await stream.read(8192)
                    if not chunk:
                        return b"".join(chunks)
                    total += len(chunk)
                    if self.max_output_bytes is not None and total > self.max_output_bytes:
                        raise ExecutionLimitError("process_failed", "Tool process output limit exceeded")
                    chunks.append(chunk)

            readers = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr))]
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            stdout, stderr = await asyncio.gather(*readers)
            code = await process.wait()
            if code != 0:
                raise ExecutionLimitError("process_failed", f"Tool process exited with {code}: {stderr.decode('utf-8', errors='replace')}")
            try:
                return json.loads(stdout.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                raise ExecutionLimitError("process_failed", "Tool process must return one UTF-8 JSON value") from error
        finally:
            async def cleanup():
                # 취소가 생성과 겹친 경우에도 프로세스 핸들을 회수한다. 입력 전 게이트는 실행하지 않는다.
                nonlocal process
                if process is None:
                    process = await pending
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                async def discard(stream):
                    while await stream.read(8192):
                        pass
                # 출력 한도 초과로 reader가 멈췄어도 OS 파이프를 비워 wait의 EOF를 진행시킨다.
                drains = [asyncio.create_task(discard(stream)) for stream in (process.stdout, process.stderr)]
                try:
                    await kill_process_tree(process)
                    await asyncio.gather(*drains)
                finally:
                    for drain in drains:
                        drain.cancel()
                    await asyncio.gather(*drains, return_exceptions=True)
                if process.stdin is not None:
                    process.stdin.close()
            await drain_on_cancel(cleanup())

    def validate_tool(self, name, isolation):
        """실행 계약이 요구하는 격리 수준과 등록 명령을 사전 검증한다."""
        if name not in self.commands or isolation == "sandbox" and self.isolation != "sandbox":
            raise ExecutionLimitError("tool_contract", "Runner does not satisfy Tool isolation requirements")
        if self.isolation == "sandbox" and shutil.which("bwrap") is None:
            raise ExecutionLimitError("tool_contract", "Bubblewrap is required")

    async def __call__(self, tool: Any, call: "ToolCall") -> Any:
        """큐 대기도 timeout에 포함한다. 호출별 프로세스 그룹은 완료·실패·취소 시 회수한다."""
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop, self._slots = loop, None if self.max_concurrency is None else asyncio.Semaphore(self.max_concurrency)
        if self._loop is not loop:
            raise RuntimeError("Use ProcessToolRunner on its original event loop")
        async with timeout(self.timeout_seconds):
            if self._slots is None:
                return await self._execute(call)
            async with self._slots:
                return await self._execute(call)
