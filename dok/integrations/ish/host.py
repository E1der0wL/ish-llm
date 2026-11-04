"""All access to ish runtime resources and its UI loop lives here."""

from concurrent.futures import Future
from typing import Callable, TypeVar
import asyncio
import termios


Result = TypeVar("Result")


class HostAccess:
    DISPATCH_TIMEOUT = 10.0

    def __init__(self, prompt):
        self.prompt = prompt
        self._loop = None

    def observe_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def dispatch(self, operation: Callable[[], Result]) -> Result:
        loop = self._loop or self.prompt.app.loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is None or not loop.is_running() or current is loop:
            return operation()
        result = Future()

        def invoke():
            if not result.set_running_or_notify_cancel():
                return
            try:
                result.set_result(operation())
            except BaseException as exc:
                result.set_exception(exc)

        loop.call_soon_threadsafe(invoke)
        try:
            return result.result(timeout=self.DISPATCH_TIMEOUT)
        except TimeoutError:
            result.cancel()
            raise

    def pty_columns(self) -> int:
        shell = getattr(self.prompt, "interactive_shell", None)
        descriptor = getattr(shell, "master_fd", None)
        if descriptor is not None:
            try:
                _, columns = termios.tcgetwinsize(descriptor)
                if columns > 0:
                    return columns
            except (OSError, ValueError, TypeError):
                pass
        return self.prompt.app.output.get_size().columns

    def editing_shell(self) -> bool:
        buffer = getattr(self.prompt.app.layout.current_control, "buffer", None)
        return buffer is self.prompt.default_buffer or buffer is self.prompt.search_buffer
