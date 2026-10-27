"""작업 루트에서 시작한 프로세스만 조회/취소하고 출력과 실행 시간을 제한한다."""

import asyncio
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
from .capture import current_output_store
from llm.services.infrastructure.captured_text import CapturedText
from .path_access import current_path_access, PathAccess


@dataclass(slots=True)
class ProcessSession:
    process: asyncio.subprocess.Process
    argv: list[str]
    cwd: str
    started_at: str
    stdout: CapturedText = field(default_factory=CapturedText)
    stderr: CapturedText = field(default_factory=CapturedText)
    status: ProcessStatus = ProcessStatus.RUNNING
    truncated: bool = False
    ended_at: str | None = None
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    store: object = None
    sources: dict | None = None

    def snapshot(self, identifier):
        maximum = self.store.preview_chars if self.store is not None else None
        value = ProcessSnapshot(identifier, self.status.value, self.process.returncode, self.process.pid,
            list(self.argv), self.cwd, self.started_at, self.ended_at,
            self.process.stdin is not None and not self.process.stdin.is_closing(),
            self.stdout.read(0, maximum), self.stderr.read(0, maximum), self.truncated).to_dict()
        if self.store is not None:
            value.update(output_sources=self.sources, stdout_chars=self.stdout.characters,
                         stderr_chars=self.stderr.characters, preview_chars=maximum)
        return value


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
        if type(offset) is not int or not ProcessOutputArguments.MIN_OFFSET <= offset <= buffer.characters:
            raise ValueError(cls.INVALID_OFFSET)
        count = buffer.characters if requested_count is UNSET else requested_count
        if type(count) is not int or count < ProcessOutputArguments.MIN_COUNT and requested_count is not UNSET:
            raise ValueError(cls.INVALID_COUNT)
        page = buffer.read(offset, count)
        following = offset + len(page)
        return cls(page, following, buffer.characters - following)


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
        async def discard(stream):
            while await stream.read(self.READ_BYTES):
                pass
        await asyncio.gather(kill_process_tree(process), discard(process.stdout), discard(process.stderr))

    async def _stop(self, item):
        if not item.task.done():
            item.task.cancel()
        await asyncio.gather(item.task, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
        if item.status == ProcessStatus.RUNNING:
            # monitor가 첫 실행 기회를 얻기 전에 취소된 경우에도 프로세스를 회수한다.
            item.status = ProcessStatus.CANCELLED
            await self._terminate_unobserved(item.process)
            item.ended_at = datetime.now(timezone.utc).isoformat()
            item.stdout.finish(); item.stderr.finish()
            if item.store is not None:
                identifier = next(key for key, value in self.sessions.items() if value is item)
                item.sources = await item.store.publish(identifier, item)

    def _session(self, identifier):
        item = self.sessions[identifier]
        current = current_output_store()
        if (item.store is None) != (current is None) or (
            current is not None and item.store.owner != current.owner
        ):
            raise ValueError("Process output belongs to a different Project or destination")
        return item

    def check(self):
        current = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = current
        if self.closed or self.loop is not current:
            raise RuntimeError(self.WRONG_LIFETIME)

    def snapshot(self, identifier):
        return self._session(identifier).snapshot(identifier)

    async def start(self, args):
        self.check()
        args = ProcessStartArguments.read(args)
        # 생략은 기존 subprocess 상속, {}는 빈 환경. 전역 os.environ은 변경하지 않는다.
        environment = args.environment()
        async with self.lock:
            if self.closed:
                raise RuntimeError(self.CLOSED_TOOLS)
            access = current_path_access()
            files = self.files if access is None else self.files.with_access(access)
            cwd = files.cwd_access.resolve(files.root, args.directory)
            if not cwd.is_dir():
                raise ValueError(self.INVALID_CWD)
            args.validate_limits()
            seconds, maximum = args.timeout, args.output_limit
            args.validate_input()
            interactive = args.stdin
            # 취소가 프로세스 생성과 겹치더라도 핸들을 회수해 종료한다.
            with PathAccess.directory(cwd) as directory_fd:
                pending = asyncio.create_task(asyncio.create_subprocess_exec(
                    *args.argv, cwd=f"/proc/self/fd/{directory_fd}", pass_fds=(directory_fd,),
                    stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
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
            item.store = current_output_store()
            self.sessions[identifier] = item
            captured_bytes = 0

            async def read(stream, buffer):
                nonlocal captured_bytes
                while True:
                    chunk = await stream.read(self.READ_BYTES)
                    if not chunk:
                        break
                    # Reserve the shared stdout/stderr allowance before yielding
                    # to the off-loop writer; concurrent readers cannot overspend it.
                    available = len(chunk) if maximum is None else min(len(chunk), max(0, maximum - captured_bytes))
                    captured_bytes += available
                    await drain_on_cancel(asyncio.to_thread(buffer.extend, chunk[:available]))
                    item.truncated |= len(chunk) > available

            async def monitor():
                readers = [asyncio.create_task(read(stream, buffer))
                           for stream, buffer in ((process.stdout, item.stdout), (process.stderr, item.stderr))]
                try:
                    async with timeout(seconds):
                        await asyncio.gather(process.wait(), *readers)
                    item.status = ProcessStatus.COMPLETED
                except asyncio.CancelledError:
                    item.status = ProcessStatus.CANCELLED
                    raise
                except (TimeoutError, asyncio.TimeoutError):
                    item.status = ProcessStatus.TIMED_OUT
                except Exception:
                    item.status = ProcessStatus.FAILED
                    raise
                finally:
                    if process.stdin is not None:
                        process.stdin.close()
                    for reader in readers:
                        if not reader.done():
                            reader.cancel()
                    observed = await asyncio.gather(*readers, return_exceptions=self.COLLECT_CLEANUP_ERRORS)
                    if any(isinstance(value, Exception) for value in observed):
                        # A cancellation racing a failed write must not publish
                        # incomplete bytes under a valid-looking character count.
                        item.status = ProcessStatus.FAILED
                    # A failed disk writer must not leave a child blocked on a full pipe.
                    await drain_on_cancel(self._terminate_unobserved(process))
                    item.ended_at = datetime.now(timezone.utc).isoformat()
                    item.stdout.finish()
                    item.stderr.finish()
                    if item.store is not None and item.status != ProcessStatus.FAILED:
                        try:
                            item.sources = await drain_on_cancel(item.store.publish(identifier, item))
                        except Exception:
                            item.status = ProcessStatus.FAILED
                            raise
            item.task = asyncio.create_task(monitor())
            return await asyncio.to_thread(self.snapshot, identifier)

    async def execute(self, args):
        identifier = ProcessSnapshot.identifier(await self.start(args))
        try:
            await asyncio.shield(self.sessions[identifier].task)
        except asyncio.CancelledError:
            await drain_on_cancel(self.cancel(ProcessReferenceArguments(process_id=identifier).to_dict()))
            raise
        return await asyncio.to_thread(self.snapshot, identifier)

    async def status(self, args):
        self.check()
        identifier = ProcessReferenceArguments.read(args).process_id
        if identifier not in self.sessions and current_output_store() is not None:
            return await current_output_store().load(identifier)
        return await asyncio.to_thread(self.snapshot, identifier)

    async def output(self, args):
        """문자 offset으로 출력만 이어 읽는다. stdout/stderr 사이의 시간 순서는 추측하지 않는다."""
        self.check()
        args = ProcessOutputArguments.read(args)
        if args.process_id not in self.sessions and current_output_store() is not None:
            return await current_output_store().read(args)
        item = self._session(args.process_id)
        final = item.status != ProcessStatus.RUNNING or item.truncated
        stdout = await asyncio.to_thread(OutputPage.read, item.stdout, args.stdout_offset, args.max_chars, final=final)
        stderr = await asyncio.to_thread(OutputPage.read, item.stderr, args.stderr_offset, args.max_chars, final=final)
        return ProcessOutput(args.process_id, item.status.value, item.process.returncode, item.truncated,
            stdout.text, stdout.next_offset, stdout.remaining,
            stderr.text, stderr.next_offset, stderr.remaining).to_dict()

    async def write(self, args):
        """이 Toolkit이 stdin=True로 만든 프로세스에만 입력한다. 자동 개행/재전송은 하지 않는다."""
        self.check()
        args = ProcessWriteArguments.read(args)
        item = self._session(args.process_id)
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
        await self._stop(self._session(identifier))
        return await asyncio.to_thread(self.snapshot, identifier)

    async def close(self):
        if self.closed:
            return
        self.check()
        async with self.lock:
            self.closed = self.CLOSED
            try:
                await asyncio.gather(*(self._stop(item) for item in self.sessions.values()))
            finally:
                for item in self.sessions.values():
                    item.stdout.close()
                    item.stderr.close()
