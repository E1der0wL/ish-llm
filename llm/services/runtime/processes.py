"""등록 Tool의 JSON 프로토콜과 일반 argv 실행이 공유하는 Linux 격리·회수 경계."""

from llm.core.models import RunErrorCode
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Optional, Sequence, TYPE_CHECKING, Union

from llm._platform import require_linux
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.process_environment import ProcessEnvironment
from llm.services.infrastructure.storage import drain_on_cancel
from llm.policies import ExecutionLimitError
from llm.core.tool_contracts import ToolIsolation
from ._worker import ProcessGate, ProcessLaunch

if TYPE_CHECKING:
    from llm.services.runtime.tools import ToolCall


copy_env = ProcessEnvironment.copy


class ProcessToolRunner:
    """sandbox는 Linux Bubblewrap 필수다. process는 명시적으로 선택하는 비보안 프로세스 격리다.

    commands={Tool명: 절대 실행 파일로 시작하는 argv}; 명령은 stdin의 ToolCall JSON을 읽고
    stdout에 JSON 결과 하나를 출력한다. 기존 Python 클로저를 자동 직렬화하지 않는다.
    execute(argv)는 일반 명령용으로 stdin EOF와 stdout/stderr/returncode 결과를 사용한다.
    cwd는 전용 작업 디렉토리로 지정하고 workspace 메타데이터 디렉토리를 노출하지 않는다.
    """

    def __init__(self, commands: Mapping[str, Sequence[str]], *, cwd: Union[str, Path],
                 isolation: str, env: Optional[Mapping[str, str]] = None,
                 read_only_paths: Sequence[Union[str, Path]] = (),
                 max_output_bytes: Optional[int] = None, memory_bytes: Optional[int] = None,
                 max_concurrency: Optional[int] = None,
                 allow_network: Optional[bool] = None,
                 python_executable: Optional[Union[str, Path]] = None):
        require_linux()
        if isolation not in (ToolIsolation.SANDBOX, ToolIsolation.PROCESS):
            raise ValueError("Choose sandbox or process")
        if isolation == ToolIsolation.SANDBOX and type(allow_network) is not bool:
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
        self.isolation, self.env = isolation, copy_env({} if env is None else env)
        self.allow_network = allow_network
        self.read_only_paths = tuple(Path(path).resolve(strict=True) for path in read_only_paths)
        self.max_output_bytes = max_output_bytes
        self.memory_bytes = memory_bytes
        self.max_concurrency = max_concurrency
        self.python = str(Path(python_executable or sys.executable).resolve(strict=True))
        self._loop, self._slots = None, None

    def _command(self, name):
        try:
            command = list(self.commands[name])
        except KeyError:
            raise ExecutionLimitError(RunErrorCode.PROCESS_FAILED, f"No process command registered for Tool: {name}") from None
        return self._sandbox(command)

    def _sandbox(self, command):
        if self.isolation == ToolIsolation.PROCESS:
            return command
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise ExecutionLimitError(RunErrorCode.SANDBOX_UNAVAILABLE, "Linux Bubblewrap is required; no unsandboxed fallback is allowed")
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
        # 대상의 loader/shell 환경은 namespace 구성 후에만 적용한다.
        # Python 시작 게이트와 bwrap 자체에는 이 환경을 전달하지 않는다.
        args += ["--clearenv"]
        for key, value in self.env.items():
            args += ["--setenv", key, value]
        args += ["--bind", str(self.cwd), str(self.cwd), "--chdir", str(self.cwd), "--", *command]
        return args

    async def _launch(self, command, input_value, *, pass_fds=()):
        env = {} if self.isolation == ToolIsolation.SANDBOX else copy_env(self.env)
        data = ProcessLaunch(command, str(self.cwd), env, input_value, self.memory_bytes)
        payload = json.dumps(data.to_dict(), ensure_ascii=False, allow_nan=False).encode("utf-8")
        pending = asyncio.create_task(asyncio.create_subprocess_exec(
            *ProcessGate.command(self.python, os.getpid()), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={}, start_new_session=True,
            pass_fds=pass_fds))
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
                        raise ExecutionLimitError(RunErrorCode.PROCESS_FAILED, "Tool process output limit exceeded")
                    chunks.append(chunk)

            readers = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr))]
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            stdout, stderr = await asyncio.gather(*readers)
            code = await process.wait()
            return code, stdout, stderr
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

    async def _execute(self, call):
        code, stdout, stderr = await self._launch(self._command(call.name), call.to_dict())
        if code != 0:
            raise ExecutionLimitError(RunErrorCode.PROCESS_FAILED, f"Tool process exited with {code}: {stderr.decode('utf-8', errors='replace')}")
        try:
            return json.loads(stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise ExecutionLimitError(RunErrorCode.PROCESS_FAILED, "Tool process must return one UTF-8 JSON value") from error

    def _admission(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop, self._slots = loop, None if self.max_concurrency is None else asyncio.Semaphore(self.max_concurrency)
        if self._loop is not loop:
            raise RuntimeError("Use ProcessToolRunner on its original event loop")
        return self._slots

    async def execute(self, argv: Sequence[str], *, pass_fds: Sequence[int] = ()) -> dict:
        """일반 명령의 출력과 종료 코드를 반환한다. 격리·회수는 JSON Tool 경로와 같다.

        pass_fds는 호스트 수명 잠금이다. 시작 게이트만 상속하며 사용자 명령에는 전달하지 않는다.
        """
        if (not isinstance(argv, (list, tuple)) or not argv or not argv[0]
                or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
                or not Path(argv[0]).is_absolute()):
            raise ValueError("Command requires argv with an absolute executable")
        slots = self._admission()
        async def invoke():
            code, stdout, stderr = await self._launch(self._sandbox(list(argv)), None, pass_fds=tuple(pass_fds))
            return {"returncode": code, "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace")}
        if slots is None:
            return await invoke()
        async with slots:
            return await invoke()

    def validate_tool(self, name, isolation):
        """실행 계약이 요구하는 격리 수준과 등록 명령을 사전 검증한다."""
        if name not in self.commands or isolation == ToolIsolation.SANDBOX and self.isolation != ToolIsolation.SANDBOX:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, "Runner does not satisfy Tool isolation requirements")
        if self.isolation == ToolIsolation.SANDBOX and shutil.which("bwrap") is None:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, "Bubblewrap is required")

    async def __call__(self, tool: Any, call: "ToolCall") -> Any:
        """큐 대기도 timeout에 포함한다. 호출별 프로세스 그룹은 완료·실패·취소 시 회수한다."""
        slots = self._admission()
        # 실행 기한은 ToolExecutor의 Project 정책이 집행한다. runner는 취소를 회수한다.
        if slots is None:
            return await self._execute(call)
        async with slots:
            return await self._execute(call)
