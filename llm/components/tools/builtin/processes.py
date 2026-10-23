"""작업 루트에서 시작한 프로세스만 조회/취소하고 출력과 실행 시간을 제한한다."""

import asyncio
import codecs
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from llm._platform import require_linux
from llm.services.infrastructure.processes import kill_process_tree
from asyncio import timeout
from llm.services.infrastructure.storage import drain_on_cancel
from llm.core.configuration import UNSET
from .process_values import (ProcessStatus, ProcessText, ProcessStartArguments, ProcessReferenceArguments,
    ProcessOutputArguments, ProcessWriteArguments, ProcessSnapshot, ProcessOutput, ProcessWritten)


@dataclass(slots=True)
class ProcessSession:
    process: asyncio.subprocess.Process
    argv: list[str]
    cwd: str
    started_at: str
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    status: ProcessStatus = ProcessStatus.RUNNING
    truncated: bool = False
    ended_at: str | None = None
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None

    def snapshot(self, identifier):
        return ProcessSnapshot(identifier, self.status.value, self.process.returncode, self.process.pid,
            list(self.argv), self.cwd, self.started_at, self.ended_at,
            self.process.stdin is not None and not self.process.stdin.is_closing(),
            bytes(self.stdout).decode(ProcessText.ENCODING, errors=ProcessText.ERRORS),
            bytes(self.stderr).decode(ProcessText.ENCODING, errors=ProcessText.ERRORS), self.truncated)


@dataclass(frozen=True, slots=True)
class OutputPage:
    INVALID_OFFSET = "Output offset is outside the retained stream"
    INVALID_COUNT = "max_chars must be a positive integer"

    text: str
    next_offset: int
    remaining: int

    @classmethod
    def read(cls, buffer, offset, requested_count, *, final):
        # Incomplete UTF-8 is withheld until complete, preserving character cursors.
        decoder = codecs.getincrementaldecoder(ProcessText.ENCODING)(errors=ProcessText.ERRORS)
        text = decoder.decode(bytes(buffer), final=final)
        if type(offset) is not int or not ProcessOutputArguments.MIN_OFFSET <= offset <= len(text):
            raise ValueError(cls.INVALID_OFFSET)
        count = len(text) if requested_count is UNSET else requested_count
        if type(count) is not int or count < ProcessOutputArguments.MIN_COUNT and requested_count is not UNSET:
            raise ValueError(cls.INVALID_COUNT)
        page = text[offset:offset + count]
        following = offset + len(page)
        return cls(page, following, len(text) - following)


class ProcessTools:
    """호스트 프로세스 실행기다. cwd는 보안 샌드박스가 아니며 별도 OS 격리를 제공하지 않는다."""

    OPEN = False
    CLOSED = True
    READ_BYTES = 8192
    NEW_PROCESS_GROUP = True
    COLLECT_CLEANUP_ERRORS = True
    WRONG_LIFETIME = "Use open process tools on their original event loop"
    CLOSED_TOOLS = "Process tools are closed"
    INVALID_CWD = "Process cwd must be an existing directory"
    CLOSED_INPUT = "Process has no open input pipe"

    def __init__(self, files):
        require_linux()
        self.files = files
        self.sessions = {}
        self.closed = self.OPEN
        self.loop = None
        self.lock = asyncio.Lock()

    async def _terminate_unobserved(self, process):
        # 출력 reader 생성 전 취소된 프로세스도 파이프를 비워야 wait()가 완료된다.
        await asyncio.gather(kill_process_tree(process), process.communicate())

    async def _stop(self, item):
        if not item.task.done():
            item.task.cancel()
        await asyncio.gather(item.task, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
        if item.status == ProcessStatus.RUNNING:
            # monitor가 첫 실행 기회를 얻기 전에 취소된 경우에도 프로세스를 회수한다.
            item.status = ProcessStatus.CANCELLED
            await self._terminate_unobserved(item.process)

    def check(self):
        current = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = current
        if self.closed or self.loop is not current:
            raise RuntimeError(self.WRONG_LIFETIME)

    def snapshot(self, identifier):
        return self.sessions[identifier].snapshot(identifier).to_dict()

    async def start(self, args):
        self.check()
        args = ProcessStartArguments.read(args)
        # 생략은 기존 subprocess 상속, {}는 빈 환경. 전역 os.environ은 변경하지 않는다.
        environment = args.environment()
        async with self.lock:
            if self.closed:
                raise RuntimeError(self.CLOSED_TOOLS)
            cwd = self.files.path(args.directory)
            if not cwd.is_dir():
                raise ValueError(self.INVALID_CWD)
            args.validate_limits()
            seconds, maximum = args.timeout, args.output_limit
            args.validate_input()
            interactive = args.stdin
            # 취소가 프로세스 생성과 겹치더라도 핸들을 회수해 종료한다.
            pending = asyncio.create_task(asyncio.create_subprocess_exec(
                *args.argv, cwd=cwd, stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=self.NEW_PROCESS_GROUP,
                **environment))
            try:
                process = await asyncio.shield(pending)
            except asyncio.CancelledError:
                process = await pending
                await drain_on_cancel(self._terminate_unobserved(process))
                raise
            identifier = uuid4().hex
            item = ProcessSession(process, list(args.argv), str(cwd), datetime.now(timezone.utc).isoformat())
            self.sessions[identifier] = item

            async def read(stream, buffer):
                while True:
                    chunk = await stream.read(self.READ_BYTES)
                    if not chunk:
                        break
                    available = len(chunk) if maximum is None else max(0, maximum - len(item.stdout) - len(item.stderr))
                    buffer.extend(chunk[:available])
                    item.truncated |= len(chunk) > available

            async def monitor():
                readers = [asyncio.create_task(read(stream, buffer))
                           for stream, buffer in ((process.stdout, item.stdout), (process.stderr, item.stderr))]
                try:
                    async with timeout(seconds):
                        await process.wait()
                        await asyncio.gather(*readers)
                    item.status = ProcessStatus.COMPLETED
                except asyncio.CancelledError:
                    item.status = ProcessStatus.CANCELLED
                    await kill_process_tree(process)
                    raise
                except (TimeoutError, asyncio.TimeoutError):
                    item.status = ProcessStatus.TIMED_OUT
                    await kill_process_tree(process)
                except Exception:
                    item.status = ProcessStatus.FAILED
                    await kill_process_tree(process)
                    raise
                finally:
                    if process.stdin is not None:
                        process.stdin.close()
                    for reader in readers:
                        if not reader.done():
                            reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
                    item.ended_at = datetime.now(timezone.utc).isoformat()
            item.task = asyncio.create_task(monitor())
            return self.snapshot(identifier)

    async def execute(self, args):
        identifier = ProcessSnapshot.identifier(await self.start(args))
        try:
            await asyncio.shield(self.sessions[identifier].task)
        except asyncio.CancelledError:
            await drain_on_cancel(self.cancel(ProcessReferenceArguments(process_id=identifier).to_dict()))
            raise
        return self.snapshot(identifier)

    async def status(self, args):
        self.check()
        return self.snapshot(ProcessReferenceArguments.read(args).process_id)

    async def output(self, args):
        """문자 offset으로 출력만 이어 읽는다. stdout/stderr 사이의 시간 순서는 추측하지 않는다."""
        self.check()
        args = ProcessOutputArguments.read(args)
        item = self.sessions[args.process_id]
        final = item.status != ProcessStatus.RUNNING or item.truncated
        stdout = OutputPage.read(item.stdout, args.stdout_offset, args.max_chars, final=final)
        stderr = OutputPage.read(item.stderr, args.stderr_offset, args.max_chars, final=final)
        return ProcessOutput(args.process_id, item.status.value, item.process.returncode, item.truncated,
            stdout.text, stdout.next_offset, stdout.remaining,
            stderr.text, stderr.next_offset, stderr.remaining).to_dict()

    async def write(self, args):
        """이 Toolkit이 stdin=True로 만든 프로세스에만 입력한다. 자동 개행/재전송은 하지 않는다."""
        self.check()
        args = ProcessWriteArguments.read(args)
        item = self.sessions[args.process_id]
        async with item.input_lock:
            stream = item.process.stdin
            if item.status != ProcessStatus.RUNNING or stream is None or stream.is_closing():
                raise ValueError(self.CLOSED_INPUT)
            args.validate_input()
            content, close = args.text, args.close
            # 승인된 write 후 취소되어도 이미 입력한 바이트의 효과는 불확실하다.
            # common ToolExecutor의 재시도/효과 정책을 유지하고 이 메서드는 재전송하지 않는다.
            stream.write(content.encode(ProcessText.ENCODING))
            await stream.drain()
            if close:
                stream.close()
                await stream.wait_closed()
            return ProcessWritten(args.process_id, len(content.encode(ProcessText.ENCODING)), close).to_dict()

    async def cancel(self, args):
        self.check()
        identifier = ProcessReferenceArguments.read(args).process_id
        await self._stop(self.sessions[identifier])
        return self.snapshot(identifier)

    async def close(self):
        if self.closed:
            return
        self.check()
        async with self.lock:
            self.closed = self.CLOSED
            await asyncio.gather(*(self._stop(item) for item in self.sessions.values()))
