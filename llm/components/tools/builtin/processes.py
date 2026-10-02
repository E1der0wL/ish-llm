"""작업 루트에서 시작한 프로세스만 조회/취소하고 출력과 실행 시간을 제한한다."""

import asyncio
from uuid import uuid4

from llm._platform import require_linux
from llm.services.infrastructure.processes import kill_process_tree
from asyncio import timeout
from llm.services.infrastructure.storage import drain_on_cancel


class ProcessTools:
    """호스트 프로세스 실행기다. cwd는 보안 샌드박스가 아니며 별도 OS 격리를 제공하지 않는다."""

    def __init__(self, files, *, max_seconds=None, max_output_bytes=None, max_sessions=None):
        require_linux()
        self.files = files
        self.max_seconds, self.max_output_bytes, self.max_sessions = max_seconds, max_output_bytes, max_sessions
        self.sessions = {}
        self.closed = False
        self.loop = None
        self.lock = asyncio.Lock()

    async def _terminate_unobserved(self, process):
        # 출력 reader 생성 전 취소된 프로세스도 파이프를 비워야 wait()가 완료된다.
        await asyncio.gather(kill_process_tree(process), process.communicate())

    async def _stop(self, item):
        if not item["task"].done():
            item["task"].cancel()
        await asyncio.gather(item["task"], return_exceptions=True)
        if item["status"] == "running":
            # monitor가 첫 실행 기회를 얻기 전에 취소된 경우에도 프로세스를 회수한다.
            item["status"] = "cancelled"
            await self._terminate_unobserved(item["process"])

    def check(self):
        current = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = current
        if self.closed or self.loop is not current:
            raise RuntimeError("Use open process tools on their original event loop")

    def snapshot(self, identifier):
        item = self.sessions[identifier]
        return {"process_id": identifier, "status": item["status"], "returncode": item["process"].returncode,
                "stdout": bytes(item["stdout"]).decode("utf-8", errors="replace"),
                "stderr": bytes(item["stderr"]).decode("utf-8", errors="replace"),
                "truncated": item["truncated"]}

    async def start(self, args):
        self.check()
        async with self.lock:
            if self.closed:
                raise RuntimeError("Process tools are closed")
            if self.max_sessions is not None and len(self.sessions) >= self.max_sessions:
                finished = next((key for key, item in self.sessions.items() if item["task"].done()), None)
                if finished is None:
                    raise RuntimeError("Too many running Tool processes")
                del self.sessions[finished]
            cwd = self.files.path(args.get("cwd", "."))
            if not cwd.is_dir():
                raise ValueError("Process cwd must be an existing directory")
            seconds = args.get("timeout_seconds", self.max_seconds)
            if seconds is not None and (seconds <= 0 or self.max_seconds is not None and seconds > self.max_seconds):
                raise ValueError("Process timeout exceeds configured limit")
            # 취소가 프로세스 생성과 겹치더라도 핸들을 회수해 종료한다.
            pending = asyncio.create_task(asyncio.create_subprocess_exec(
                *args["argv"], cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True))
            try:
                process = await asyncio.shield(pending)
            except asyncio.CancelledError:
                process = await pending
                await drain_on_cancel(self._terminate_unobserved(process))
                raise
            identifier = uuid4().hex
            item = {"process": process, "stdout": bytearray(), "stderr": bytearray(),
                    "status": "running", "truncated": False}
            self.sessions[identifier] = item

            async def read(name):
                stream = getattr(process, name)
                while True:
                    chunk = await stream.read(8192)
                    if not chunk:
                        break
                    available = len(chunk) if self.max_output_bytes is None else self.max_output_bytes - len(item["stdout"]) - len(item["stderr"])
                    item[name].extend(chunk[:available])
                    item["truncated"] |= len(chunk) > available

            async def monitor():
                readers = [asyncio.create_task(read(name)) for name in ("stdout", "stderr")]
                try:
                    async with timeout(seconds):
                        await process.wait()
                        await asyncio.gather(*readers)
                    item["status"] = "completed"
                except asyncio.CancelledError:
                    item["status"] = "cancelled"
                    await kill_process_tree(process)
                    raise
                except (TimeoutError, asyncio.TimeoutError):
                    item["status"] = "timed_out"
                    await kill_process_tree(process)
                except Exception:
                    item["status"] = "failed"
                    await kill_process_tree(process)
                    raise
                finally:
                    for reader in readers:
                        if not reader.done():
                            reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
            item["task"] = asyncio.create_task(monitor())
            return self.snapshot(identifier)

    async def execute(self, args):
        started = await self.start(args)
        identifier = started["process_id"]
        try:
            await asyncio.shield(self.sessions[identifier]["task"])
        except asyncio.CancelledError:
            await drain_on_cancel(self.cancel({"process_id": identifier}))
            raise
        return self.snapshot(identifier)

    async def status(self, args):
        self.check()
        return self.snapshot(args["process_id"])

    async def cancel(self, args):
        self.check()
        item = self.sessions[args["process_id"]]
        await self._stop(item)
        return self.snapshot(args["process_id"])

    async def close(self):
        if self.closed:
            return
        self.check()
        async with self.lock:
            self.closed = True
            await asyncio.gather(*(self._stop(item) for item in self.sessions.values()))
