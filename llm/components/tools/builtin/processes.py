"""작업 루트에서 시작한 프로세스만 조회/취소하고 출력과 실행 시간을 제한한다."""

import asyncio
import codecs
import math
from datetime import datetime, timezone
from uuid import uuid4

from llm._platform import require_linux
from llm.services.infrastructure.processes import kill_process_tree
from asyncio import timeout
from llm.services.infrastructure.storage import drain_on_cancel


class ProcessTools:
    """호스트 프로세스 실행기다. cwd는 보안 샌드박스가 아니며 별도 OS 격리를 제공하지 않는다."""

    def __init__(self, files):
        require_linux()
        self.files = files
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
                "pid": item["process"].pid, "argv": list(item["argv"]), "cwd": item["cwd"],
                "started_at": item["started_at"], "ended_at": item.get("ended_at"),
                "stdin_open": item["process"].stdin is not None and not item["process"].stdin.is_closing(),
                "stdout": bytes(item["stdout"]).decode("utf-8", errors="replace"),
                "stderr": bytes(item["stderr"]).decode("utf-8", errors="replace"),
                "truncated": item["truncated"]}

    async def start(self, args):
        self.check()
        async with self.lock:
            if self.closed:
                raise RuntimeError("Process tools are closed")
            cwd = self.files.path(args.get("cwd", "."))
            if not cwd.is_dir():
                raise ValueError("Process cwd must be an existing directory")
            seconds = args.get("timeout_seconds")
            if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0):
                raise ValueError("Process timeout must be positive and finite")
            maximum = args.get("max_output_bytes")
            if maximum is not None and (type(maximum) is not int or maximum < 1):
                raise ValueError("max_output_bytes must be a positive integer")
            interactive = args.get("stdin", False)
            if type(interactive) is not bool:
                raise ValueError("stdin must be boolean")
            # 취소가 프로세스 생성과 겹치더라도 핸들을 회수해 종료한다.
            pending = asyncio.create_task(asyncio.create_subprocess_exec(
                *args["argv"], cwd=cwd, stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True))
            try:
                process = await asyncio.shield(pending)
            except asyncio.CancelledError:
                process = await pending
                await drain_on_cancel(self._terminate_unobserved(process))
                raise
            identifier = uuid4().hex
            item = {"process": process, "stdout": bytearray(), "stderr": bytearray(),
                    "status": "running", "truncated": False, "argv": list(args["argv"]), "cwd": str(cwd),
                    "started_at": datetime.now(timezone.utc).isoformat(), "input_lock": asyncio.Lock()}
            self.sessions[identifier] = item

            async def read(name):
                stream = getattr(process, name)
                while True:
                    chunk = await stream.read(8192)
                    if not chunk:
                        break
                    available = len(chunk) if maximum is None else max(0, maximum - len(item["stdout"]) - len(item["stderr"]))
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
                    if process.stdin is not None:
                        process.stdin.close()
                    for reader in readers:
                        if not reader.done():
                            reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
                    item["ended_at"] = datetime.now(timezone.utc).isoformat()
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

    async def output(self, args):
        """문자 offset으로 출력만 이어 읽는다. stdout/stderr 사이의 시간 순서는 추측하지 않는다."""
        self.check()
        item = self.sessions[args["process_id"]]
        result = {"process_id": args["process_id"], "status": item["status"],
                  "returncode": item["process"].returncode, "truncated": item["truncated"]}
        for name in ("stdout", "stderr"):
            # 진행 중 나뉘어 도착한 UTF-8 문자는 완성된 뒤 공개하여 cursor가
            # 임시 replacement character를 지나가거나 같은 문자를 재소비하지 않게 한다.
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = decoder.decode(bytes(item[name]), final=item["status"] != "running" or item["truncated"])
            offset = args.get(name + "_offset", 0)
            if type(offset) is not int or not 0 <= offset <= len(text):
                raise ValueError("Output offset is outside the retained stream")
            count = args.get("max_chars", len(text))
            if type(count) is not int or count < 1 and "max_chars" in args:
                raise ValueError("max_chars must be a positive integer")
            result[name] = text[offset:offset + count]
            result["next_" + name + "_offset"] = offset + len(result[name])
            result[name + "_remaining"] = len(text) - result["next_" + name + "_offset"]
        return result

    async def write(self, args):
        """이 Toolkit이 stdin=True로 만든 프로세스에만 입력한다. 자동 개행/재전송은 하지 않는다."""
        self.check()
        item = self.sessions[args["process_id"]]
        async with item["input_lock"]:
            stream = item["process"].stdin
            if item["status"] != "running" or stream is None or stream.is_closing():
                raise ValueError("Process has no open input pipe")
            content = args.get("text", "")
            close = args.get("close", False)
            if not isinstance(content, str) or type(close) is not bool or not content and not close:
                raise ValueError("Provide text or explicitly close stdin")
            # 승인된 write 후 취소되어도 이미 입력한 바이트의 효과는 불확실하다.
            # common ToolExecutor의 재시도/효과 정책을 유지하고 이 메서드는 재전송하지 않는다.
            stream.write(content.encode("utf-8"))
            await stream.drain()
            if close:
                stream.close()
                await stream.wait_closed()
            return {"process_id": args["process_id"], "bytes_written": len(content.encode("utf-8")),
                    "stdin_closed": close}

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
